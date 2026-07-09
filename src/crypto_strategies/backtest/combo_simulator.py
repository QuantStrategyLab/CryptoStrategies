"""Simplified crypto equity combo backtest for orchestrator integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from crypto_strategies.backtest.live_pool_simulator import LivePoolBacktestResult, _performance_metrics
from crypto_strategies.strategies.crypto_equity_combo import (
    DEFAULT_BTC_WEIGHT,
    DEFAULT_TREND_WEIGHT,
    DYNAMIC_REGIME_OFF_CUT,
)

ComboMode = Literal["static", "dynamic"]
BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"
ALTS = ("ETH", "SOL", "AVAX", "MATIC", "DOT")
VOL_MULTIPLIERS = {"ETH": 1.0, "SOL": 1.8, "AVAX": 2.2, "MATIC": 2.0, "DOT": 1.6}
SMA_SHORT = 20
SMA_LONG = 60
BTC_SMA_REGIME = 200


@dataclass(frozen=True)
class CryptoComboBacktestConfig:
    btc_weight: float = DEFAULT_BTC_WEIGHT
    trend_weight: float = DEFAULT_TREND_WEIGHT
    combo_mode: ComboMode = "dynamic"
    min_history_days: int = 260
    dca_amount_usd: float = 100.0
    dynamic_trend_cut: float = DYNAMIC_REGIME_OFF_CUT


def build_close_matrix(
    market_history: pd.DataFrame,
    *,
    symbols: tuple[str, ...] = (BTC_SYMBOL, ETH_SYMBOL),
) -> pd.DataFrame:
    frame = market_history.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=False).dt.tz_localize(None).dt.normalize()
    close = (
        frame.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
        .sort_index()
        .reindex(columns=list(symbols))
    )
    return close.astype(float)


def _simulate_alt_returns(eth_returns: pd.Series, *, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    simulated: dict[str, pd.Series] = {}
    for alt in ALTS:
        mult = VOL_MULTIPLIERS.get(alt, 1.0)
        noise = rng.normal(0, 0.005, size=len(eth_returns))
        raw = np.clip(eth_returns.values * mult + noise, -0.25, 0.25)
        simulated[alt] = pd.Series(raw, index=eth_returns.index)
    return pd.DataFrame(simulated)


def _compute_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _combo_daily_returns(
    close: pd.DataFrame,
    *,
    combo_config: CryptoComboBacktestConfig,
) -> pd.Series:
    btc_col = BTC_SYMBOL if BTC_SYMBOL in close.columns else close.columns[0]
    eth_col = ETH_SYMBOL if ETH_SYMBOL in close.columns else close.columns[min(1, len(close.columns) - 1)]

    btc_close = close[btc_col].dropna()
    eth_close = close[eth_col].dropna()
    idx = btc_close.index.intersection(eth_close.index).sort_values()
    if len(idx) < combo_config.min_history_days:
        return pd.Series(dtype=float)

    eth_returns = eth_close.pct_change().dropna()
    alt_returns = _simulate_alt_returns(eth_returns.reindex(idx).fillna(0.0))
    alt_prices: dict[str, pd.Series] = {}
    for alt in ALTS:
        cum = (1.0 + alt_returns[alt]).cumprod()
        start_price = float(eth_close.reindex(cum.index).iloc[0] or 1.0)
        alt_prices[alt] = start_price * cum / cum.iloc[0]

    btc_sma200 = _compute_sma(btc_close, BTC_SMA_REGIME)
    btc_below_sma200 = btc_close < btc_sma200

    alt_dfs: dict[str, pd.DataFrame] = {}
    for alt in ALTS:
        ap = alt_prices[alt].reindex(idx)
        alt_dfs[alt] = pd.DataFrame(
            {
                "close": ap,
                "sma_short": _compute_sma(ap, SMA_SHORT),
                "sma_long": _compute_sma(ap, SMA_LONG),
            },
            index=idx,
        )

    portfolio_values: list[float] = []
    alt_positions: dict[str, float] = {}
    btc_units = 0.0
    cash_held = 0.0
    dynamic = combo_config.combo_mode == "dynamic"

    for date in idx:
        btc_p = float(btc_close.loc[date])
        trend_weight = combo_config.trend_weight
        extra_btc_alloc = 0.0
        if dynamic and bool(btc_below_sma200.loc[date]):
            trend_weight *= 1.0 - combo_config.dynamic_trend_cut
            extra_btc_alloc = combo_config.dca_amount_usd * combo_config.trend_weight * combo_config.dynamic_trend_cut

        btc_alloc = combo_config.dca_amount_usd * combo_config.btc_weight
        trend_alloc = combo_config.dca_amount_usd * trend_weight
        btc_units += (btc_alloc + extra_btc_alloc) / btc_p

        alt_prices_today: dict[str, float] = {}
        alt_candidates: list[str] = []
        for alt in ALTS:
            row = alt_dfs[alt].loc[date]
            alt_price = float(row["close"])
            alt_prices_today[alt] = alt_price
            short_sma = row["sma_short"]
            long_sma = row["sma_long"]
            if not np.isnan(short_sma) and not np.isnan(long_sma) and short_sma > long_sma:
                alt_candidates.append(alt)

        if alt_candidates and trend_alloc > 0:
            per_alt = trend_alloc / len(alt_candidates)
            for alt in alt_candidates:
                alt_positions[alt] = alt_positions.get(alt, 0.0) + per_alt / alt_prices_today[alt]
        else:
            cash_held += trend_alloc

        btc_value = btc_units * btc_p
        alt_value = sum(
            alt_positions.get(alt, 0.0) * alt_prices_today.get(alt, 0.0)
            for alt in ALTS
        )
        portfolio_values.append(btc_value + alt_value + cash_held)

    equity = pd.Series(portfolio_values, index=idx)
    return equity.pct_change().fillna(0.0)


def run_combo_backtest(
    market_history: pd.DataFrame,
    *,
    combo_config: CryptoComboBacktestConfig | None = None,
    universe_symbols: Any = None,
) -> LivePoolBacktestResult:
    combo = combo_config or CryptoComboBacktestConfig()
    symbols = tuple(universe_symbols or (BTC_SYMBOL, ETH_SYMBOL))
    close = build_close_matrix(market_history, symbols=symbols)
    if len(close) < int(combo.min_history_days):
        raise ValueError(
            f"market_history requires at least {int(combo.min_history_days)} overlapping trading days"
        )
    returns = _combo_daily_returns(close, combo_config=combo)
    return LivePoolBacktestResult(metrics=_performance_metrics(returns), returns=returns)


__all__ = [
    "BTC_SYMBOL",
    "ComboMode",
    "CryptoComboBacktestConfig",
    "build_close_matrix",
    "run_combo_backtest",
]

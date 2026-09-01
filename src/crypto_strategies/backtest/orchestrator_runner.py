"""BacktestRunner adapter for crypto live pool rotation and equity combo."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any, Mapping, cast

import numpy as np
import pandas as pd

from crypto_strategies.backtest.combo_simulator import ComboMode, CryptoComboBacktestConfig, run_combo_backtest
from crypto_strategies.backtest.live_pool_simulator import _performance_metrics, run_live_pool_rotation_backtest
from crypto_strategies.strategies.crypto_equity_combo import PROFILE_NAME as CRYPTO_EQUITY_COMBO_PROFILE

try:
    from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult
except ImportError:  # pragma: no cover
    BacktestResult = None  # type: ignore[misc, assignment]


PROFILE_NAME = "crypto_live_pool_rotation"
DEFAULT_MIN_HISTORY_DAYS = 120
COMBO_DEFAULT_MIN_HISTORY_DAYS = 260
SUPPORTED_PROFILES = frozenset({PROFILE_NAME, CRYPTO_EQUITY_COMBO_PROFILE})
SYNTHETIC_PANEL_GENERATOR_VERSION = "crypto_live_pool_panel.v2"


def _synthetic_digest_int(*parts: str) -> int:
    material = "\x1f".join((SYNTHETIC_PANEL_GENERATOR_VERSION, *parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=False)


def _synthetic_panel(*, days: int = 1500, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=days, freq="D")
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    panel = pd.DataFrame(index=index)
    panel["in_universe"] = True
    rows: list[float] = []
    for symbol in symbols:
        price = 100.0 + _synthetic_digest_int("initial_price", symbol) % 50
        rng = np.random.default_rng(_synthetic_digest_int("price_rng", symbol))
        for _ in dates:
            price *= 1.0 + float(rng.normal(0.001, 0.02))
            rows.append(price)
    panel["open"] = rows
    scores: list[float] = []
    for day_idx, _day in enumerate(dates):
        for sym_idx, symbol in enumerate(symbols):
            salt = _synthetic_digest_int("score", symbol) % 11
            scores.append(float((day_idx + sym_idx * 17 + salt) % 100) / 100.0)
    panel["final_score"] = scores
    panel.attrs["synthetic_generator_version"] = SYNTHETIC_PANEL_GENERATOR_VERSION
    return panel.sort_index()


def _synthetic_market_history(*, days: int = 1500, start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=days, freq="D")
    symbols = ("BTCUSDT", "ETHUSDT")
    rates = {"BTCUSDT": 1.0012, "ETHUSDT": 1.0015}
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        price = 20000.0 if symbol == "BTCUSDT" else 1500.0
        rate = rates[symbol]
        for idx, day in enumerate(dates):
            price *= rate
            close = price * (1.0 + 0.03 * ((idx % 13) - 6) / 13)
            rows.append({"date": day, "symbol": symbol, "close": close})
    return pd.DataFrame(rows)


def _slice_panel(panel: pd.DataFrame, *, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    level_dates = panel.index.get_level_values("date")
    frame = panel
    if start_date is not None:
        frame = frame.loc[level_dates >= pd.Timestamp(start_date)]
        level_dates = frame.index.get_level_values("date")
    if end_date is not None:
        frame = frame.loc[level_dates <= pd.Timestamp(end_date)]
    return frame.sort_index()


def _slice_history(
    market_history: pd.DataFrame,
    *,
    start_date: date | None,
    end_date: date | None,
    lookback_days: int = 0,
) -> pd.DataFrame:
    frame = market_history.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=False).dt.tz_localize(None).dt.normalize()
    if start_date is not None:
        effective_start = pd.Timestamp(start_date) - pd.Timedelta(days=max(int(lookback_days), 0))
        frame = frame[frame["date"] >= effective_start]
    if end_date is not None:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def _slice_daily_returns(
    returns: pd.Series,
    *,
    start_date: date | None,
    end_date: date | None,
) -> pd.Series:
    sliced = returns.copy()
    sliced.index = pd.to_datetime(sliced.index, utc=False).tz_localize(None).normalize()
    if start_date is not None:
        sliced = sliced.loc[sliced.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        sliced = sliced.loc[sliced.index <= pd.Timestamp(end_date)]
    return sliced


def _metrics_to_result(
    *,
    strategy_profile: str,
    params: Mapping[str, Any],
    metrics: Mapping[str, Any],
    start_date: date | None,
    end_date: date | None,
    run_duration_seconds: float,
) -> Any:
    if BacktestResult is None:
        raise ImportError("quant_platform_kit is required to build BacktestResult")
    cagr = float(metrics.get("CAGR") or 0.0)
    max_drawdown = float(metrics.get("Max Drawdown") or 0.0)
    calmar = abs(cagr / max_drawdown) if max_drawdown else None
    return BacktestResult(
        strategy_profile=strategy_profile,
        domain="crypto",
        param_set_id="",
        params=dict(params),
        sharpe_ratio=float(metrics.get("Sharpe") or 0.0),
        calmar_ratio=calmar,
        max_drawdown=max_drawdown,
        cagr=cagr,
        volatility=float(metrics.get("Annualized Volatility") or 0.0),
        win_rate=float(metrics.get("Win Rate") or 0.0),
        total_return=float(metrics.get("total_return") or 0.0),
        start_date=start_date,
        end_date=end_date,
        observation_count=int(metrics.get("Trading Days") or 0),
        source_script="crypto_strategies.backtest.orchestrator_runner",
        computed_at=datetime.now(timezone.utc).isoformat(),
        run_duration_seconds=run_duration_seconds,
    )


class CryptoLivePoolBacktestRunner:
    """Protocol-compatible BacktestRunner for crypto_live_pool_rotation."""

    def __init__(self, *, panel: pd.DataFrame | None = None, synthetic_days: int = 1600) -> None:
        self._panel = panel
        self._synthetic_days = int(synthetic_days)
        self._last_daily_returns = pd.Series(dtype=float)
        self._run_return_history: list[pd.Series] = []

    @property
    def last_daily_returns(self) -> pd.Series:
        return self._last_daily_returns.copy()

    @property
    def run_return_history(self) -> tuple[pd.Series, ...]:
        return tuple(item.copy() for item in self._run_return_history)

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Any:
        if strategy_profile not in SUPPORTED_PROFILES:
            raise ValueError(
                f"Unsupported strategy_profile={strategy_profile!r}; "
                f"supported={sorted(SUPPORTED_PROFILES)}"
            )
        if strategy_profile != PROFILE_NAME:
            raise ValueError(
                f"Unsupported strategy_profile={strategy_profile!r}; "
                f"use CryptoEquityComboBacktestRunner for {CRYPTO_EQUITY_COMBO_PROFILE!r}"
            )

        panel = self._panel
        if panel is None:
            panel = _synthetic_panel(days=max(self._synthetic_days, DEFAULT_MIN_HISTORY_DAYS + 60))
        sliced = _slice_panel(panel, start_date=start_date, end_date=end_date)
        if sliced.empty:
            raise ValueError("No panel rows for requested window")

        started = datetime.now(timezone.utc)
        result = run_live_pool_rotation_backtest(
            sliced,
            top_n=int(params.get("top_n", 2)),
            rebalance_every=int(params.get("rebalance_every", 7)),
            signal_lag_days=int(params.get("signal_lag_days", params.get("signal_lag", 1))),
            fee_bps=float(params.get("fee_bps", 0.0)),
            fee_rate=float(params["fee_rate"]) if "fee_rate" in params else None,
            slippage_bps=float(params.get("slippage_bps", 0.0)),
        )
        self._last_daily_returns = _slice_daily_returns(
            result.returns,
            start_date=start_date,
            end_date=end_date,
        )
        self._run_return_history.append(self._last_daily_returns.copy())
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        eval_dates = sliced.index.get_level_values("date")
        return _metrics_to_result(
            strategy_profile=strategy_profile,
            params=params,
            metrics=result.metrics,
            start_date=start_date or eval_dates.min().date(),
            end_date=end_date or eval_dates.max().date(),
            run_duration_seconds=elapsed,
        )


class CryptoEquityComboBacktestRunner:
    """Protocol-compatible BacktestRunner for crypto_equity_combo research."""

    def __init__(
        self,
        *,
        market_history: pd.DataFrame | None = None,
        synthetic_days: int = 1600,
    ) -> None:
        self._market_history = market_history
        self._synthetic_days = int(synthetic_days)
        self._last_daily_returns = pd.Series(dtype=float)
        self._run_return_history: list[pd.Series] = []

    @property
    def last_daily_returns(self) -> pd.Series:
        return self._last_daily_returns.copy()

    @property
    def run_return_history(self) -> tuple[pd.Series, ...]:
        return tuple(item.copy() for item in self._run_return_history)

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Any:
        if strategy_profile != CRYPTO_EQUITY_COMBO_PROFILE:
            raise ValueError(
                f"Unsupported strategy_profile={strategy_profile!r}; "
                f"supported={CRYPTO_EQUITY_COMBO_PROFILE!r}"
            )

        min_history_days = int(params.get("min_history_days", COMBO_DEFAULT_MIN_HISTORY_DAYS))
        combo_mode = str(params.get("combo_mode", "dynamic"))
        if combo_mode not in {"static", "dynamic"}:
            raise ValueError("combo_mode must be 'static' or 'dynamic'")

        history = self._market_history
        if history is None:
            history = _synthetic_market_history(
                days=max(self._synthetic_days, min_history_days + 400),
            )
        sliced = _slice_history(
            history,
            start_date=start_date,
            end_date=end_date,
            lookback_days=min_history_days + 5,
        )
        if sliced.empty:
            raise ValueError("No market history rows for requested window")

        started = datetime.now(timezone.utc)
        result = run_combo_backtest(
            sliced,
            combo_config=CryptoComboBacktestConfig(
                combo_mode=cast(ComboMode, combo_mode),
                min_history_days=min_history_days,
            ),
        )
        self._last_daily_returns = _slice_daily_returns(
            result.returns,
            start_date=start_date,
            end_date=end_date,
        )
        self._run_return_history.append(self._last_daily_returns.copy())
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        eval_frame = sliced
        if start_date is not None:
            eval_frame = sliced[sliced["date"] >= pd.Timestamp(start_date)]
        return _metrics_to_result(
            strategy_profile=strategy_profile,
            params=params,
            metrics=_performance_metrics(self._last_daily_returns),
            start_date=start_date or (eval_frame["date"].min().date() if not eval_frame.empty else None),
            end_date=end_date or (eval_frame["date"].max().date() if not eval_frame.empty else None),
            run_duration_seconds=elapsed,
        )


def build_backtest_runner(
    strategy_profile: str,
    *,
    panel: pd.DataFrame | None = None,
    market_history: pd.DataFrame | None = None,
    synthetic_days: int = 1600,
) -> CryptoLivePoolBacktestRunner | CryptoEquityComboBacktestRunner:
    if strategy_profile == CRYPTO_EQUITY_COMBO_PROFILE:
        return CryptoEquityComboBacktestRunner(
            market_history=market_history,
            synthetic_days=synthetic_days,
        )
    return CryptoLivePoolBacktestRunner(
        panel=panel,
        synthetic_days=synthetic_days,
    )


__all__ = [
    "COMBO_DEFAULT_MIN_HISTORY_DAYS",
    "DEFAULT_MIN_HISTORY_DAYS",
    "PROFILE_NAME",
    "SYNTHETIC_PANEL_GENERATOR_VERSION",
    "SUPPORTED_PROFILES",
    "CryptoEquityComboBacktestRunner",
    "CryptoLivePoolBacktestRunner",
    "build_backtest_runner",
]

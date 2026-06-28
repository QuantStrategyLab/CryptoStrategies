"""Crypto combo backtest research script.

Simulates three portfolio strategies over crypto data (2021-2026):

  A. Pure BTC DCA (100% BTC, daily buy)
  B. Static combo (50% BTC DCA + 50% Trend Rotation)
  C. Dynamic combo (same as B, but when BTC < 200d SMA the trend leg
     allocation is reduced by 50 %)

Because high-quality historical altcoin data is scarce through yfinance, the
trend-leg returns are simulated from ETH-USD data with a volatility multiplier
per altcoin.

Usage
-----
    PYTHONPATH=src:scripts python3 scripts/research_crypto_combo_backtest.py
    PYTHONPATH=src:scripts python3 scripts/research_crypto_combo_backtest.py --json-output
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

START_DATE = "2021-01-01"
END_DATE = "2026-06-28"

# Combo weights
BTC_WEIGHT = 0.50
TREND_WEIGHT = 0.50

# Dynamic regime: reduce trend leg by 50 % when BTC is below its 200d SMA
DYNAMIC_TREND_CUT = 0.50

# DCA parameters
DCA_AMOUNT_USD = 100.0  # daily dollar amount

# Altcoin simulation parameters
ALTS = ["ETH", "SOL", "AVAX", "MATIC", "DOT"]

# Each altcoin's volatility multiplier relative to ETH
VOL_MULTIPLIERS = {
    "ETH": 1.0,
    "SOL": 1.8,
    "AVAX": 2.2,
    "MATIC": 2.0,
    "DOT": 1.6,
}

# Trend leg parameters
SMA_SHORT = 20
SMA_LONG = 60

# Period breakdowns for reporting
PERIODS: dict[str, tuple[str, str]] = {
    "Full Period": (START_DATE, END_DATE),
    "Bear (2022)": ("2022-01-01", "2022-12-31"),
    "Bull (2023-2024)": ("2023-01-01", "2024-12-31"),
    "Recent (2025-2026)": ("2025-01-01", END_DATE),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_annual_return(df: pd.DataFrame) -> float:
    """Compute CAGR from a daily equity curve."""
    if df.empty or df.iloc[0] <= 0:
        return 0.0
    total_return = df.iloc[-1] / df.iloc[0] - 1.0
    n_days = max(len(df), 1)
    years = n_days / 365.25
    if years <= 0:
        return 0.0
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def _safe_max_drawdown(df: pd.DataFrame) -> float:
    """Compute maximum drawdown from a daily equity curve."""
    if df.empty:
        return 0.0
    rolling_max = df.expanding().max()
    dd = (df - rolling_max) / rolling_max
    return float(dd.min())


def _safe_sharpe(df: pd.DataFrame, rf: float = 0.0) -> float:
    """Compute annualised Sharpe ratio from daily returns."""
    if df.empty or len(df) < 2:
        return 0.0
    daily_returns = df.pct_change().dropna()
    if daily_returns.empty or daily_returns.std() == 0:
        return 0.0
    excess = daily_returns.mean() - rf / 365.25
    return float(np.sqrt(365.25) * excess / daily_returns.std())


def _compute_sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average that returns NaN until enough data."""
    return series.rolling(window=window, min_periods=window).mean()


def _simulate_alt_returns(
    eth_returns: pd.Series,
    seed: int = 42,
) -> pd.DataFrame:
    """Simulate daily returns for each altcoin from ETH returns.

    Each altcoin's return is:
        r_alt = r_eth * vol_multiplier + idio_noise

    where idio_noise ~ N(0, 0.005) and is clipped to [-0.25, 0.25].
    """
    rng = np.random.default_rng(seed)
    simulated: dict[str, pd.Series] = {}
    for alt in ALTS:
        mult = VOL_MULTIPLIERS.get(alt, 1.0)
        noise = rng.normal(0, 0.005, size=len(eth_returns))
        raw = eth_returns.values * mult + noise
        raw = np.clip(raw, -0.25, 0.25)
        simulated[alt] = pd.Series(raw, index=eth_returns.index)
    return pd.DataFrame(simulated)


# ---------------------------------------------------------------------------
# Data loading (yfinance)
# ---------------------------------------------------------------------------


def load_crypto_data() -> pd.DataFrame:
    """Download BTC-USD and ETH-USD daily closes from yfinance.

    Returns a DataFrame indexed by date with columns:
        btc_close, eth_close
    """
    # Lazy import so the module can be syntax-checked without yfinance installed
    try:
        import yfinance as yf  # noqa: T100
    except ImportError:
        # Fallback to quant_strategy_plugins if available
        try:
            from quant_strategy_plugins.yfinance_prices import download_price_history

            raw = download_price_history(
                ["BTC-USD", "ETH-USD"],
                start=START_DATE,
                end=END_DATE,
            )
            prices = raw.pivot_table(
                index="as_of",
                columns="symbol",
                values="close",
                aggfunc="last",
            )
            prices.index = pd.to_datetime(prices.index).normalize()
            prices = prices.rename(
                columns={
                    "BTC-USD": "btc_close",
                    "ETH-USD": "eth_close",
                }
            )
            return prices.sort_index()
        except (ImportError, Exception):
            raise  # no fallback available

    raw = yf.download(  # noqa: T100
        ["BTC-USD", "ETH-USD"],
        start=START_DATE,
        end=END_DATE,
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"].copy()
        closes.columns = closes.columns.str.upper()
    else:
        closes = raw[["Close"]].copy()
        closes.columns = ["BTC-USD"]

    prices = pd.DataFrame(index=closes.index)
    prices.index = pd.to_datetime(prices.index).normalize()
    if "BTC-USD" in closes.columns:
        prices["btc_close"] = closes["BTC-USD"].astype(float)
    if "ETH-USD" in closes.columns:
        prices["eth_close"] = closes["ETH-USD"].astype(float)

    return prices.sort_index()


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def run_backtest(
    prices: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    """Run the three-strategy backtest.

    Returns nested dict keyed by strategy name, each containing an equity
    curve DataFrame and per-period metrics.
    """
    btc_close = prices["btc_close"].dropna()
    eth_close = prices["eth_close"].dropna()

    # ---------- Simulate altcoin prices ----------
    eth_returns = eth_close.pct_change().dropna()
    alt_returns = _simulate_alt_returns(eth_returns)
    alt_prices: dict[str, pd.Series] = {}
    for alt in ALTS:
        cum = (1.0 + alt_returns[alt]).cumprod()
        alt_prices[alt] = eth_close.reindex(cum.index).iloc[0] * cum / cum.iloc[0]

    # Shared date index
    idx = btc_close.index.intersection(eth_close.index).sort_values()
    idx = idx[idx >= pd.Timestamp("2021-02-01")]  # skip SMA warm-up

    # ---------- Indicators ----------
    btc_sma200 = _compute_sma(btc_close, 200)
    btc_below_sma200 = btc_close < btc_sma200  # regime signal

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

    # ---------- Strategy A: Pure BTC DCA ----------
    equity_a = _simulate_btc_dca(btc_close, idx)
    metrics_a = _compute_metrics(equity_a, "Pure BTC DCA")

    # ---------- Strategy B: Static combo ----------
    equity_b = _simulate_combo(
        btc_close,
        alt_dfs,
        idx,
        dynamic=False,
    )
    metrics_b = _compute_metrics(equity_b, "Static Combo")

    # ---------- Strategy C: Dynamic combo ----------
    equity_c = _simulate_combo(
        btc_close,
        alt_dfs,
        idx,
        dynamic=True,
        regime_series=btc_below_sma200,
    )
    metrics_c = _compute_metrics(equity_c, "Dynamic Combo")

    return {
        "Pure BTC DCA": {"equity": equity_a, "metrics": metrics_a},
        "Static Combo": {"equity": equity_b, "metrics": metrics_b},
        "Dynamic Combo": {"equity": equity_c, "metrics": metrics_c},
    }


def _simulate_btc_dca(
    btc_close: pd.Series,
    idx: pd.DatetimeIndex,
) -> pd.Series:
    """Simulate daily BTC DCA: buy DCA_AMOUNT_USD worth of BTC every day.

    Returns an equity curve (portfolio value in USD) aligned to `idx`.
    """
    price = btc_close.reindex(idx)
    btc_units = pd.Series(DCA_AMOUNT_USD / price, index=idx)
    cum_units = btc_units.cumsum()
    return cum_units * price


def _simulate_combo(
    btc_close: pd.Series,
    alt_dfs: dict[str, pd.DataFrame],
    idx: pd.DatetimeIndex,
    dynamic: bool = False,
    regime_series: pd.Series | None = None,
) -> pd.Series:
    """Simulate a combo portfolio.

    Each day:
      1. DCA_AMOUNT_USD is split per BTC_WEIGHT / TREND_WEIGHT.
      2. BTC leg: buy BTC with the BTC portion.
      3. Trend leg: the trend portion is allocated across altcoins that
         pass the SMA crossover (short > long). If no altcoin passes,
         the trend portion stays in cash.
      4. Dynamic mode: when regime_series is True, the trend allocation
         is reduced by DYNAMIC_TREND_CUT and the freed amount reallocated
         to BTC.
    """
    # Track cumulative portfolio value
    portfolio_values: list[float] = []
    alt_positions: dict[str, float] = {}  # alt -> units held
    btc_units_held = 0.0
    cash_held = 0.0  # trend leg cash not deployed

    for date in idx:
        btc_p = float(btc_close.loc[date])
        trend_weight = TREND_WEIGHT

        if dynamic and regime_series is not None:
            if regime_series.loc[date]:
                trend_weight *= 1.0 - DYNAMIC_TREND_CUT

        btc_alloc = DCA_AMOUNT_USD * BTC_WEIGHT
        trend_alloc = DCA_AMOUNT_USD * trend_weight
        extra_btc_alloc = 0.0
        if dynamic and regime_series is not None and regime_series.loc[date]:
            # The freed-up portion goes to BTC
            extra_btc_alloc = DCA_AMOUNT_USD * TREND_WEIGHT * DYNAMIC_TREND_CUT

        # Daily BTC purchase
        total_btc_daily = btc_alloc + extra_btc_alloc
        btc_units_held += total_btc_daily / btc_p

        # Daily trend: allocate across altcoins that pass SMA crossover
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
            # Trend allocation stays in cash
            cash_held += trend_alloc

        # Compute total portfolio value
        btc_value = btc_units_held * btc_p
        alt_value = sum(
            alt_positions.get(alt, 0.0) * alt_prices_today.get(alt, 0.0)
            for alt in ALTS
        )
        portfolio_values.append(btc_value + alt_value + cash_held)

    return pd.Series(portfolio_values, index=idx)


def _compute_metrics(
    equity: pd.Series,
    label: str,
) -> dict[str, Any]:
    """Compute per-period metrics for a single equity curve."""
    periods_metrics: dict[str, Any] = {}
    for period_name, (start_str, end_str) in PERIODS.items():
        start_ts = pd.Timestamp(start_str)
        end_ts = pd.Timestamp(end_str)
        sub = equity.loc[start_ts:end_ts]
        if sub.empty:
            periods_metrics[period_name] = {
                "annual_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "total_return": 0.0,
            }
            continue

        start_val = sub.iloc[0]
        end_val = sub.iloc[-1]
        total_ret = end_val / start_val - 1.0 if start_val > 0 else 0.0
        ann_ret = _safe_annual_return(sub)
        mdd = _safe_max_drawdown(sub)
        sharpe = _safe_sharpe(sub)

        periods_metrics[period_name] = {
            "annual_return": round(float(ann_ret), 4),
            "max_drawdown": round(float(mdd), 4),
            "sharpe": round(float(sharpe), 4),
            "total_return": round(float(total_ret), 4),
        }

    return periods_metrics


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(results: dict[str, Any]) -> None:
    """Print a formatted summary table."""
    strategies = ["Pure BTC DCA", "Static Combo", "Dynamic Combo"]
    period_names = list(PERIODS.keys())

    # Column widths
    col_w = 14
    name_w = max(len(s) for s in strategies) + 2

    for period in period_names:
        header = f"  {period}"
        print()
        print(header)
        print("-" * (len(header) + 4))
        print(
            f"  {'Strategy':<{name_w}} {'AnnRet':>{col_w}} {'MDD':>{col_w}} "
            f"{'Sharpe':>{col_w}} {'TotRet':>{col_w}}"
        )
        print("  " + "-" * (name_w + col_w * 4 + 6))
        for strat in strategies:
            m = results[strat]["metrics"][period]
            print(
                f"  {strat:<{name_w}} {m['annual_return']:>{col_w}.2%} "
                f"{m['max_drawdown']:>{col_w}.2%} {m['sharpe']:>{col_w}.2f} "
                f"{m['total_return']:>{col_w}.2%}"
            )
    print()


def print_summary_comparison(results: dict[str, Any]) -> None:
    """Print a high-level comparison of strategy characteristics."""
    print("=" * 70)
    print("  COMBO BACKTEST SUMMARY")
    print("=" * 70)

    for strategy_name in ["Pure BTC DCA", "Static Combo", "Dynamic Combo"]:
        m = results[strategy_name]["metrics"]["Full Period"]
        print(f"\n  {strategy_name}:")
        print(f"    Annual Return : {m['annual_return']:.2%}")
        print(f"    Max Drawdown  : {m['max_drawdown']:.2%}")
        print(f"    Sharpe        : {m['sharpe']:.2f}")
        print(f"    Total Return  : {m['total_return']:.2%}")

    print()
    print("  Period breakdowns:")
    for period in list(PERIODS)[1:]:
        print(f"\n    {period}:")
        for strat in ["Pure BTC DCA", "Static Combo", "Dynamic Combo"]:
            m = results[strat]["metrics"][period]
            print(
                f"      {strat:<20s} AnnRet={m['annual_return']:.2%}  "
                f"MDD={m['max_drawdown']:.2%}  Sharpe={m['sharpe']:.2f}  "
                f"TotRet={m['total_return']:.2%}"
            )
    print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crypto combo backtest research script"
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Output results as JSON to stdout",
    )
    args = parser.parse_args()

    print("Loading crypto price data via yfinance ...", file=sys.stderr)
    prices = load_crypto_data()
    print(
        f"  Loaded {len(prices)} days: {prices.index[0].date()} to {prices.index[-1].date()}",
        file=sys.stderr,
    )

    print("Running backtest simulation ...", file=sys.stderr)
    results = run_backtest(prices)
    print("  Done.", file=sys.stderr)

    if args.json_output:
        # Strip equity curves for JSON output (too large)
        json_results: dict[str, Any] = {}
        for strat_name, data in results.items():
            json_results[strat_name] = {"metrics": data["metrics"]}
        json.dump(json_results, sys.stdout, indent=2)
        print()
    else:
        print_summary_comparison(results)
        print_report(results)


if __name__ == "__main__":
    main()

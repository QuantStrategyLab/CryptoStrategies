"""Combo strategy backtest: validate dynamic rebalancing rules and parameter sensitivity.

Tests the crypto combo (BTC DCA + Trend Rotation) with varying dynamic parameters
to check for overfitting and validate the two-tier defense design.

Strategies tested:
  A. Static 50/50 (no dynamic adjustment)
  B. Dynamic ×0.5 (single-tier: regime_off → trend cut 50%)
  C. Dynamic two-tier (soft=15% cut, hard=50% cut) — NEW design
  D. Dynamic ×0.3 (mild cut)
  E. Dynamic ×0.7 (aggressive cut)

Metrics: CAGR, Max Drawdown, Sharpe, Calmar per market period.

Usage
-----
    cd CryptoStrategies
    PYTHONPATH=src:scripts python scripts/research_combo_dynamic_backtest.py
"""

from __future__ import annotations

import argparse
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

# DCA parameters
DCA_MONTHLY_USD = 100.0

# Trend leg parameters
SMA_SHORT = 20
SMA_LONG = 60

# Altcoin simulation
ALTS = ["ETH", "SOL", "AVAX", "MATIC", "DOT"]
VOL_MULTIPLIERS = {"ETH": 1.0, "SOL": 1.8, "AVAX": 2.2, "MATIC": 2.0, "DOT": 1.6}

# Two-tier defense parameters
SOFT_DEFENSE_SMA200_RATIO = 0.85

PERIODS: dict[str, tuple[str, str]] = {
    "Full Period": (START_DATE, END_DATE),
    "Bear (2022)": ("2022-01-01", "2022-12-31"),
    "Bull (2023-2024)": ("2023-01-01", "2024-12-31"),
    "Recent (2025-2026)": ("2025-01-01", END_DATE),
}

# Dynamic cut values to test
CUT_VALUES = [0.30, 0.50, 0.70]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def load_crypto_data() -> pd.DataFrame:
    try:
        import yfinance as yf
        raw = yf.download(["BTC-USD", "ETH-USD"], start=START_DATE, end=END_DATE,
                          auto_adjust=True, progress=False, threads=False)
        closes = raw["Close"].copy() if "Close" in raw.columns else raw["close"].copy()
        if isinstance(closes, pd.DataFrame):
            closes = closes.rename(columns=lambda c: str(c).upper())
        prices = pd.DataFrame(index=closes.index)
        prices.index = pd.to_datetime(prices.index).normalize()
        for col in closes.columns:
            label = "btc_close" if "BTC" in str(col).upper() else "eth_close"
            prices[label] = closes[col].astype(float)
        return prices.sort_index()
    except ImportError:
        from quant_strategy_plugins.yfinance_prices import download_price_history
        raw = download_price_history(["BTC-USD", "ETH-USD"], start=START_DATE, end=END_DATE)
        raw = raw.pivot_table(index="as_of", columns="symbol", values="close", aggfunc="last")
        raw.index = pd.to_datetime(raw.index).normalize()
        raw = raw.rename(columns={"BTC-USD": "btc_close", "ETH-USD": "eth_close"})
        return raw.sort_index()


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _simulate_alt_returns(eth_returns: pd.Series, seed: int = 42) -> pd.DataFrame:
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
# Simulation
# ---------------------------------------------------------------------------


def run_backtest(prices: pd.DataFrame) -> dict[str, dict[str, Any]]:
    btc_close = prices["btc_close"].dropna()
    eth_close = prices["eth_close"].dropna()

    eth_returns = eth_close.pct_change().dropna()
    alt_returns = _simulate_alt_returns(eth_returns)
    alt_prices: dict[str, pd.Series] = {}
    for alt in ALTS:
        cum = (1.0 + alt_returns[alt]).cumprod()
        alt_prices[alt] = eth_close.reindex(cum.index).iloc[0] * cum / cum.iloc[0]

    idx = btc_close.index.intersection(eth_close.index).sort_values()
    idx = idx[idx >= pd.Timestamp("2021-02-01")]

    btc_sma200 = _sma(btc_close, 200)

    alt_dfs: dict[str, pd.DataFrame] = {}
    for alt in ALTS:
        ap = alt_prices[alt].reindex(idx)
        alt_dfs[alt] = pd.DataFrame({
            "close": ap,
            "sma_short": _sma(ap, SMA_SHORT),
            "sma_long": _sma(ap, SMA_LONG),
        }, index=idx)

    results: dict[str, dict[str, Any]] = {}

    # A. Static 50/50
    results["A. Static 50/50"] = _simulate_combo(
        btc_close, alt_dfs, idx, btc_sma200,
        dynamic=False, soft_cut=0.0, hard_cut=0.0,
    )

    # B. Single-tier ×0.5 (original design)
    results["B. Single ×0.5"] = _simulate_combo(
        btc_close, alt_dfs, idx, btc_sma200,
        dynamic=True, soft_cut=0.50, hard_cut=0.50,  # same cut for both
    )

    # C. Two-tier (new design: soft=15%, hard=50%)
    results["C. Two-tier (15/50%)"] = _simulate_combo(
        btc_close, alt_dfs, idx, btc_sma200,
        dynamic=True, soft_cut=0.15, hard_cut=0.50,
    )

    # D-F. Sensitivity: vary cut values
    for i, cut in enumerate(CUT_VALUES):
        label = f"{'DEF'[i]}. Single ×{cut:.0%}"
        results[label] = _simulate_combo(
            btc_close, alt_dfs, idx, btc_sma200,
            dynamic=True, soft_cut=cut, hard_cut=cut,
        )

    return results


def _simulate_combo(
    btc_close: pd.Series,
    alt_dfs: dict[str, pd.DataFrame],
    idx: pd.DatetimeIndex,
    btc_sma200: pd.Series,
    *,
    dynamic: bool = False,
    soft_cut: float = 0.50,
    hard_cut: float = 0.50,
) -> dict[str, Any]:
    """Simulate combo portfolio with configurable dynamic defense."""
    equity: list[float] = []
    btc_units = 0.0
    alt_positions: dict[str, float] = {}
    cash_held = 0.0

    for date in idx:
        btc_p = float(btc_close.loc[date])
        sma200 = float(btc_sma200.loc[date]) if date in btc_sma200.index and not pd.isna(btc_sma200.loc[date]) else btc_p

        trend_weight = TREND_WEIGHT
        if dynamic and not pd.isna(sma200) and sma200 > 0:
            ratio = btc_p / sma200
            if ratio < SOFT_DEFENSE_SMA200_RATIO:
                cut = hard_cut
            elif ratio < 1.0:
                cut = soft_cut
            else:
                cut = 0.0
            trend_weight = TREND_WEIGHT * (1.0 - cut)

        btc_alloc = DCA_MONTHLY_USD * BTC_WEIGHT
        trend_alloc = DCA_MONTHLY_USD * trend_weight

        # BTC leg: daily DCA
        btc_units += btc_alloc / btc_p

        # Trend leg: allocate across passing altcoins
        alt_prices_today: dict[str, float] = {}
        alt_candidates: list[str] = []
        for alt in ALTS:
            row = alt_dfs[alt].loc[date]
            ap = float(row["close"])
            alt_prices_today[alt] = ap
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
        equity.append(btc_value + alt_value + cash_held)

    eq_series = pd.Series(equity, index=idx)
    return {
        "equity": eq_series,
        "metrics": _compute_metrics(eq_series),
    }


def _compute_metrics(equity: pd.Series) -> dict[str, Any]:
    periods_metrics: dict[str, Any] = {}
    for period_name, (start_str, end_str) in PERIODS.items():
        sub = equity.loc[start_str:end_str]
        nonzero = sub[sub > 0]
        if len(nonzero) < 2:
            periods_metrics[period_name] = {"cagr": 0.0, "max_dd": 0.0, "sharpe": 0.0, "calmar": 0.0}
            continue
        cagr = _cagr(nonzero)
        mdd = _max_dd(nonzero)
        shrp = _sharpe(nonzero)
        cal = cagr / abs(mdd) if abs(mdd) > 0.001 else 0.0
        periods_metrics[period_name] = {"cagr": round(cagr, 4), "max_dd": round(mdd, 4),
                                         "sharpe": round(shrp, 4), "calmar": round(cal, 4)}
    return periods_metrics


def _cagr(eq: pd.Series) -> float:
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return 0.0
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1.0
    years = len(eq) / 365.25
    return (1.0 + total_ret) ** (1.0 / years) - 1.0 if years > 0 else 0.0


def _max_dd(eq: pd.Series) -> float:
    if len(eq) < 2:
        return 0.0
    rolling_max = eq.expanding().max()
    return float(((eq - rolling_max) / rolling_max).min())


def _sharpe(eq: pd.Series) -> float:
    if len(eq) < 2:
        return 0.0
    daily = eq.pct_change().dropna()
    if daily.std() == 0:
        return 0.0
    return float(np.sqrt(365.25) * daily.mean() / daily.std())


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(results: dict[str, dict[str, Any]]) -> None:
    strategy_names = list(results.keys())
    period_names = list(PERIODS.keys())

    print()
    print("=" * 95)
    print("  COMBO DYNAMIC REBALANCE BACKTEST (2021-2026)")
    print("=" * 95)
    print(f"  Base weights: BTC {BTC_WEIGHT:.0%} / Trend {TREND_WEIGHT:.0%}")
    print(f"  Two-tier thresholds: soft < SMA200, hard < SMA200 × {SOFT_DEFENSE_SMA200_RATIO}")
    print()

    for period in period_names:
        print(f"  {period}:")
        print(f"  {'Strategy':<24s} {'CAGR':>8s} {'MDD':>8s} {'Sharpe':>8s} {'Calmar':>8s}")
        print("  " + "-" * 56)
        for name in strategy_names:
            m = results[name]["metrics"][period]
            print(f"  {name:<24s} {m['cagr']:>7.1%} {m['max_dd']:>7.1%} "
                  f"{m['sharpe']:>7.2f} {m['calmar']:>7.2f}")
        print()

    # Best per period
    print("  Best Strategy Per Period (by Calmar):")
    for period in period_names:
        best = max(strategy_names, key=lambda n: results[n]["metrics"][period]["calmar"])
        cal = results[best]["metrics"][period]["calmar"]
        print(f"    {period:<20s} → {best} (Calmar={cal:.2f})")
    print()

    # Sensitivity summary
    print("  Parameter Sensitivity (Full Period Calmar):")
    for name in strategy_names:
        cal = results[name]["metrics"]["Full Period"]["calmar"]
        bar = "█" * max(1, int(cal * 15))
        print(f"    {name:<24s} {cal:>6.2f} {bar}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Combo dynamic rebalance backtest")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    print("Loading data ...", file=sys.stderr)
    prices = load_crypto_data()
    print(f"  {len(prices)} days loaded", file=sys.stderr)

    print("Running backtest ...", file=sys.stderr)
    results = run_backtest(prices)

    if args.json:
        import json
        output = {name: data["metrics"] for name, data in results.items()}
        json.dump(output, sys.stdout, indent=2, default=str)
    else:
        print_report(results)


if __name__ == "__main__":
    main()

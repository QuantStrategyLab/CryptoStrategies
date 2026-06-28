"""BTC DCA strategy backtest: compare ordinary vs smart DCA with escape.

Simulates five BTC DCA strategies (2021-2026):

  A. Ordinary DCA    — fixed monthly buy on day 25, no smart sizing
  B. Smart (AHR999)   — AHR999 cycle multiplier only
  C. Smart (Drawdown) — drawdown-based multiplier only
  D. Smart (Full)     — AHR999 + drawdown, pick max multiplier
  E. Smart + Escape   — Full smart + Z-score exit to USDT

Metrics: CAGR / Max Drawdown / Sharpe / Calmar / total return
Periods: Full / Bear 2022 / Bull 2023-2024 / Recent 2025-2026

Usage
-----
    cd CryptoStrategies
    PYTHONPATH=src:scripts python scripts/research_crypto_dca_backtest.py
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

START_DATE = "2021-01-01"
END_DATE = "2026-06-28"

DCA_AMOUNT_USD = 100.0  # monthly base investment
MONTHLY_DAY = 25
MONTHLY_WINDOW = 5

# AHR999 thresholds (matching the strategy config)
AHR999_BOTTOM = 0.45
AHR999_ACCUMULATION = 0.80
AHR999_DCA = 1.20

AHR999_BOTTOM_MULT = 3.0
AHR999_ACCUMULATION_MULT = 2.25
AHR999_DCA_MULT = 1.50
AHR999_EXPENSIVE_MULT = 0.0  # skip buy

# Drawdown thresholds
MILD_DD = 0.12
DEEP_DD = 0.25
SEVERE_DD = 0.40
MILD_DISCOUNT_GAP = 0.08
DEEP_DISCOUNT_GAP = 0.18
EXPENSIVE_GAP = 0.30
VERY_EXPENSIVE_GAP = 0.60

MILD_MULT = 1.50
DEEP_MULT = 2.25
SEVERE_MULT = 3.0

# Z-score exit thresholds (simulated — real implementation uses MVRV Z-Score)
ZSCORE_EXIT_SOFT = 7.0  # risk_reduced → 50% BTC
ZSCORE_EXIT_HARD = 9.0  # risk_off → 25% BTC

BITCOIN_GENESIS = pd.Timestamp("2009-01-03")

PERIODS: dict[str, tuple[str, str]] = {
    "Full Period": (START_DATE, END_DATE),
    "Bear (2022)": ("2022-01-01", "2022-12-31"),
    "Bull (2023-2024)": ("2023-01-01", "2024-12-31"),
    "Recent (2025-2026)": ("2025-01-01", END_DATE),
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_btc_data() -> pd.DataFrame:
    """Download BTC-USD daily data from yfinance."""
    try:
        import yfinance as yf
        raw = yf.download(
            "BTC-USD",
            start=START_DATE,
            end=END_DATE,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except ImportError:
        from quant_strategy_plugins.yfinance_prices import download_price_history
        raw = download_price_history(["BTC-USD"], start=START_DATE, end=END_DATE)
        raw = raw.pivot_table(index="as_of", columns="symbol", values="close", aggfunc="last")

    closes = raw["Close"].copy() if "Close" in raw.columns else raw["close"].copy()
    if isinstance(closes, pd.DataFrame):
        closes = closes.iloc[:, 0]
    closes = closes.astype(float).dropna()
    closes.index = pd.to_datetime(closes.index).normalize()
    return closes.sort_index()


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _gma(series: pd.Series, window: int) -> pd.Series:
    """Geometric moving average."""
    log_values = np.log(series.clip(lower=1e-10))
    return np.exp(log_values.rolling(window=window, min_periods=window).mean())


def _estimate_price(as_of: pd.Timestamp) -> float:
    """Bitcoin age-based fair price estimate (power-law model)."""
    age_days = max(1, (as_of.normalize() - BITCOIN_GENESIS).days)
    return float(10 ** (5.84 * math.log10(age_days) - 17.01))


def _ahr999_from_series(price, sma200, gma200, estimate) -> float:
    """Compute AHR999 index: (price/gma200) * (price/estimate_price)."""
    if sma200 <= 0 or gma200 <= 0 or estimate <= 0:
        return float("nan")
    return float((price / gma200) * (price / estimate))


def _mayer_multiple(price, sma200) -> float:
    """Mayer Multiple: price / SMA200."""
    return float(price / sma200) if sma200 > 0 else float("nan")


def _simulate_zscore(price, sma200, gma200) -> float:
    """Approximate MVRV Z-Score using market-value to realized-value ratio.

    This is a coarse proxy — real MVRV Z-Score requires on-chain data.
    Uses (Mayer Multiple - 1) / rolling_std as a rough stand-in.
    """
    if sma200 <= 0 or gma200 <= 0:
        return float("nan")
    # Simplified: use Mayer Multiple deviation from 1.0 as proxy
    mm = price / sma200
    return (mm - 1.0) / 0.5  # rough normalization


def compute_indicators(btc_close: pd.Series) -> pd.DataFrame:
    """Compute all needed indicators from price series."""
    df = pd.DataFrame({"close": btc_close}, index=btc_close.index)
    df["sma200"] = _sma(df["close"], 200)
    df["gma200"] = _gma(df["close"], 200)
    df["sma50"] = _sma(df["close"], 50)
    df["high252"] = df["close"].rolling(252, min_periods=1).max()
    df["drawdown_252d"] = 1.0 - df["close"] / df["high252"]
    df["sma200_gap"] = df["close"] / df["sma200"] - 1.0
    df["rsi14"] = _compute_rsi(df["close"], 14)
    df["vol20"] = df["close"].pct_change().rolling(20).std()

    df["ahr999"] = df.apply(
        lambda r: _ahr999_from_series(r["close"], r["sma200"], r["gma200"],
                                      _estimate_price(r.name)),
        axis=1,
    )
    df["mayer_multiple"] = df.apply(lambda r: _mayer_multiple(r["close"], r["sma200"]), axis=1)
    df["zscore_proxy"] = df.apply(
        lambda r: _simulate_zscore(r["close"], r["sma200"], r["gma200"]), axis=1,
    )

    return df


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# Multiplier logic (matching the strategy implementation)
# ---------------------------------------------------------------------------


def get_ahr999_multiplier(ahr999: float) -> tuple[float, str]:
    if pd.isna(ahr999):
        return 1.0, "normal"
    if ahr999 <= AHR999_BOTTOM:
        return AHR999_BOTTOM_MULT, "ahr999_bottom"
    if ahr999 <= AHR999_ACCUMULATION:
        return AHR999_ACCUMULATION_MULT, "ahr999_accumulation"
    if ahr999 <= AHR999_DCA:
        return AHR999_DCA_MULT, "ahr999_dca"
    return AHR999_EXPENSIVE_MULT, "ahr999_expensive"


def get_drawdown_multiplier(
    drawdown: float, sma_gap: float, rsi: float,
) -> tuple[float, str]:
    if pd.isna(drawdown) or pd.isna(sma_gap):
        return 1.0, "normal"
    if drawdown >= SEVERE_DD:
        return SEVERE_MULT, "severe_pullback"
    if drawdown >= DEEP_DD or sma_gap <= -abs(DEEP_DISCOUNT_GAP):
        return DEEP_MULT, "deep_pullback"
    if drawdown >= MILD_DD or sma_gap <= -abs(MILD_DISCOUNT_GAP):
        return MILD_MULT, "mild_pullback"
    if sma_gap >= VERY_EXPENSIVE_GAP and drawdown <= 0.05 and not pd.isna(rsi) and rsi >= 75:
        return 1.0, "very_expensive_overbought"
    if sma_gap >= EXPENSIVE_GAP and drawdown <= 0.05:
        return 1.0, "expensive"
    return 1.0, "normal"


def get_smart_multiplier(row: pd.Series) -> tuple[float, str, str]:
    """Get combined multiplier: max of AHR999 and drawdown multipliers.

    Returns (multiplier, regime, source).
    """
    ahr999 = row.get("ahr999", float("nan"))
    dd = row.get("drawdown_252d", 0.0)
    gap = row.get("sma200_gap", 0.0)
    rsi = row.get("rsi14", float("nan"))

    a_mult, a_regime = get_ahr999_multiplier(ahr999)
    d_mult, d_regime = get_drawdown_multiplier(dd, gap, rsi)

    if a_mult >= d_mult:
        return a_mult, a_regime, "ahr999"
    return d_mult, d_regime, "drawdown"


def should_zscore_exit(row: pd.Series) -> tuple[bool, str, float]:
    """Check Z-score exit signal.

    Returns (should_exit, route, target_btc_exposure).
    """
    zscore = row.get("zscore_proxy", float("nan"))
    if pd.isna(zscore):
        return False, "normal", 1.0
    if zscore >= ZSCORE_EXIT_HARD:
        return True, "risk_off", 0.25
    if zscore >= ZSCORE_EXIT_SOFT:
        return True, "risk_reduced", 0.50
    return False, "normal", 1.0


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def run_strategies(
    btc_close: pd.Series,
    indicators: pd.DataFrame,
    warm_up: int = 252,
) -> dict[str, pd.Series]:
    """Simulate all five strategies and return equity curves."""
    idx = indicators.index[warm_up:]
    equity_curves: dict[str, pd.Series] = {}

    # A. Ordinary DCA
    equity_curves["A. Ordinary DCA"] = _simulate_ordinary_dca(indicators, idx)

    # B. Smart (AHR999 only)
    equity_curves["B. Smart (AHR999)"] = _simulate_smart_dca(
        indicators, idx, use_ahr999=True, use_drawdown=False, use_escape=False,
    )

    # C. Smart (Drawdown only)
    equity_curves["C. Smart (Drawdown)"] = _simulate_smart_dca(
        indicators, idx, use_ahr999=False, use_drawdown=True, use_escape=False,
    )

    # D. Smart (Full)
    equity_curves["D. Smart (Full)"] = _simulate_smart_dca(
        indicators, idx, use_ahr999=True, use_drawdown=True, use_escape=False,
    )

    # E. Smart + Escape
    equity_curves["E. Smart + Escape"] = _simulate_smart_dca(
        indicators, idx, use_ahr999=True, use_drawdown=True, use_escape=True,
    )

    return equity_curves


def _is_execution_day(day: int, month: int, year: int) -> bool:
    """Check if this day falls within the execution window (day 25 ± 5)."""
    return abs(day - MONTHLY_DAY) < MONTHLY_WINDOW


def _simulate_ordinary_dca(
    indicators: pd.DataFrame,
    idx: pd.DatetimeIndex,
) -> pd.Series:
    """Simulate ordinary monthly DCA."""
    btc_units = 0.0
    cash_held = 0.0
    equity = []

    for date in idx:
        price = float(indicators.loc[date, "close"])
        if pd.isna(price) or price <= 0:
            if equity:
                equity.append(equity[-1])
            else:
                equity.append(0.0)
            continue

        if _is_execution_day(date.day, date.month, date.year):
            btc_units += DCA_AMOUNT_USD / price

        btc_value = btc_units * price
        equity.append(btc_value + cash_held)

    return pd.Series(equity, index=idx)


def _simulate_smart_dca(
    indicators: pd.DataFrame,
    idx: pd.DatetimeIndex,
    *,
    use_ahr999: bool = True,
    use_drawdown: bool = True,
    use_escape: bool = False,
) -> pd.Series:
    """Simulate smart DCA with configurable features."""
    btc_units = 0.0
    usdt_units = 0.0  # cash parked in USDT
    equity = []
    escape_active = False
    escape_target_btc = 1.0  # proportion of portfolio in BTC

    for date in idx:
        row = indicators.loc[date]
        price = float(row["close"])
        if pd.isna(price) or price <= 0:
            if equity:
                equity.append(equity[-1])
            else:
                equity.append(0.0)
            continue

        # Check Z-score exit
        if use_escape:
            should_exit, escape_route, target_btc = should_zscore_exit(row)
            if should_exit:
                escape_active = True
                escape_target_btc = target_btc
            else:
                escape_active = False
                escape_target_btc = 1.0

        # On execution day, buy BTC with smart sizing
        if _is_execution_day(date.day, date.month, date.year):
            multiplier = 1.0
            regime = "ordinary_dca"

            if use_ahr999 or use_drawdown:
                ahr999 = row.get("ahr999", float("nan"))
                dd = row.get("drawdown_252d", 0.0)
                gap = row.get("sma200_gap", 0.0)
                rsi = row.get("rsi14", float("nan"))

                if use_ahr999 and use_drawdown:
                    multiplier, regime, _ = get_smart_multiplier(row)
                elif use_ahr999:
                    multiplier, regime = get_ahr999_multiplier(ahr999)
                elif use_drawdown:
                    multiplier, regime = get_drawdown_multiplier(dd, gap, rsi)

            invest_amount = max(0.0, DCA_AMOUNT_USD * max(0.0, multiplier))
            if multiplier > 0.0 and invest_amount > 0:
                btc_units += min(invest_amount, DCA_AMOUNT_USD * 3.0) / price

        # Z-score exit: rebalance between BTC and USDT
        if escape_active:
            total_value = btc_units * price + usdt_units
            target_btc_value = total_value * escape_target_btc
            if total_value > 0:
                # Sell BTC to USDT if over target
                current_btc_value = btc_units * price
                if current_btc_value > target_btc_value:
                    excess = current_btc_value - target_btc_value
                    btc_units -= excess / price
                    usdt_units += excess
                elif usdt_units > 0:
                    # Buy back BTC from USDT if under target
                    deficit = target_btc_value - current_btc_value
                    buy_back = min(deficit, usdt_units)
                    btc_units += buy_back / price
                    usdt_units -= buy_back

        total_value = btc_units * price + usdt_units
        equity.append(total_value)

    return pd.Series(equity, index=idx)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _cagr(equity: pd.Series) -> float:
    """CAGR from first positive value to last value."""
    nonzero = equity[equity > 0]
    if len(nonzero) < 2:
        return 0.0
    start_val = nonzero.iloc[0]
    end_val = nonzero.iloc[-1]
    if start_val <= 0:
        return 0.0
    total_return = end_val / start_val - 1.0
    n_days = max(len(nonzero), 1)
    years = n_days / 365.25
    if years <= 0:
        return 0.0
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def _max_drawdown(equity: pd.Series) -> float:
    nonzero = equity[equity > 0]
    if len(nonzero) < 2:
        return 0.0
    rolling_max = nonzero.expanding().max()
    dd = (nonzero - rolling_max) / rolling_max
    return float(dd.min())


def _sharpe(equity: pd.Series) -> float:
    nonzero = equity[equity > 0]
    if len(nonzero) < 2:
        return 0.0
    daily_returns = nonzero.pct_change().dropna()
    if daily_returns.empty or daily_returns.std() == 0:
        return 0.0
    return float(np.sqrt(365.25) * daily_returns.mean() / daily_returns.std())


def _calmar(equity: pd.Series) -> float:
    nonzero = equity[equity > 0]
    series = nonzero if len(nonzero) > 1 else equity
    c = _cagr(series)
    mdd = abs(_max_drawdown(series))
    return c / mdd if mdd > 0.001 else 0.0


def compute_all_metrics(
    equity_curves: dict[str, pd.Series],
) -> dict[str, dict[str, dict[str, float]]]:
    """Compute per-period metrics for each strategy."""
    results: dict[str, dict[str, dict[str, float]]] = {}

    for name, equity in equity_curves.items():
        results[name] = {}
        for period_name, (start, end) in PERIODS.items():
            sub = equity.loc[start:end]
            if sub.empty or len(sub) < 2:
                results[name][period_name] = {
                    "cagr": 0.0, "max_dd": 0.0, "sharpe": 0.0,
                    "calmar": 0.0, "total_return": 0.0, "final_value": 0.0,
                }
                continue

            nonzero_sub = sub[sub > 0]
            if len(nonzero_sub) >= 2:
                total_ret = nonzero_sub.iloc[-1] / nonzero_sub.iloc[0] - 1.0
            elif len(sub) >= 2 and sub.iloc[0] > 0:
                total_ret = sub.iloc[-1] / sub.iloc[0] - 1.0
            else:
                total_ret = 0.0
            # Use nonzero series for CAGR calculation
            nonzero = sub[sub > 0]
            cagr_val = _cagr(nonzero) if len(nonzero) > 1 else _cagr(sub)
            mdd_val = _max_drawdown(nonzero) if len(nonzero) > 1 else _max_drawdown(sub)
            shrp_val = _sharpe(nonzero) if len(nonzero) > 1 else _sharpe(sub)
            cal_val = cagr_val / abs(mdd_val) if abs(mdd_val) > 0.001 else 0.0
            results[name][period_name] = {
                "cagr": round(cagr_val, 4),
                "max_dd": round(mdd_val, 4),
                "sharpe": round(shrp_val, 4),
                "calmar": round(cal_val, 4),
                "total_return": round(float(total_ret), 4),
                "final_value": round(float(sub.iloc[-1]), 2),
            }

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(
    equity_curves: dict[str, pd.Series],
    metrics: dict[str, dict[str, dict[str, float]]],
) -> None:
    """Print formatted report."""
    strategy_names = list(equity_curves.keys())
    period_names = list(PERIODS.keys())

    print()
    print("=" * 90)
    print("  BTC DCA STRATEGY BACKTEST  (2021-2026)")
    print("=" * 90)
    print(f"  Monthly investment: ${DCA_AMOUNT_USD:.0f} on day {MONTHLY_DAY} ± {MONTHLY_WINDOW}d")
    print(f"  AHR999 thresholds: bottom≤{AHR999_BOTTOM} accum≤{AHR999_ACCUMULATION} dca≤{AHR999_DCA}")
    print(f"  Smart multipliers: bottom={AHR999_BOTTOM_MULT}x accum={AHR999_ACCUMULATION_MULT}x "
          f"dca={AHR999_DCA_MULT}x severe={SEVERE_MULT}x")
    print()
    print(f"  Note: CAGR/TotalRet computed from first non-zero equity (first DCA buy).")
    print()

    # Full period summary
    print("  Full Period Summary:")
    print(f"  {'Strategy':<28s} {'CAGR':>8s} {'MDD':>8s} {'Sharpe':>8s} {'Calmar':>8s} {'TotRet':>9s} {'Final':>10s}")
    print("  " + "-" * 79)
    for name in strategy_names:
        m = metrics[name]["Full Period"]
        print(
            f"  {name:<28s} {m['cagr']:>7.1%} {m['max_dd']:>7.1%} "
            f"{m['sharpe']:>7.2f} {m['calmar']:>7.2f} "
            f"{m['total_return']:>8.1%} ${m['final_value']:>9,.0f}"
        )

    # Period breakdowns
    for period in period_names[1:]:
        print(f"\n  {period}:")
        print(f"  {'Strategy':<28s} {'CAGR':>8s} {'MDD':>8s} {'Sharpe':>8s} {'Calmar':>8s} {'TotRet':>9s}")
        print("  " + "-" * 69)
        for name in strategy_names:
            m = metrics[name][period]
            print(
                f"  {name:<28s} {m['cagr']:>7.1%} {m['max_dd']:>7.1%} "
                f"{m['sharpe']:>7.2f} {m['calmar']:>7.2f} "
                f"{m['total_return']:>8.1%}"
            )

    # Winner analysis
    print()
    print("  Best Strategy Per Period:")
    for period in period_names:
        best = max(strategy_names, key=lambda n: metrics[n][period]["calmar"])
        print(f"    {period:<20s} → {best}  (Calmar: {metrics[best][period]['calmar']:.2f})")

    print()


def print_regime_distribution(indicators: pd.DataFrame) -> None:
    """Print AHR999 regime distribution over the backtest period."""
    warm_up = 252
    sub = indicators.iloc[warm_up:]
    if sub.empty:
        return

    regimes: dict[str, int] = {"bottom (≤0.45)": 0, "accumulation (0.45-0.80)": 0,
                                 "dca (0.80-1.20)": 0, "expensive (>1.20)": 0, "no_data": 0}
    for _, row in sub.iterrows():
        ahr999 = row["ahr999"]
        if pd.isna(ahr999):
            regimes["no_data"] += 1
        elif ahr999 <= 0.45:
            regimes["bottom (≤0.45)"] += 1
        elif ahr999 <= 0.80:
            regimes["accumulation (0.45-0.80)"] += 1
        elif ahr999 <= 1.20:
            regimes["dca (0.80-1.20)"] += 1
        else:
            regimes["expensive (>1.20)"] += 1

    total = sum(regimes.values()) or 1
    print("  AHR999 Regime Distribution:")
    for regime, count in regimes.items():
        pct = count / total * 100
        bar = "█" * int(pct / 2)
        print(f"    {regime:<25s}: {count:>5d} ({pct:5.1f}%) {bar}")
    print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="BTC DCA strategy backtest")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    print("Loading BTC price data ...", file=sys.stderr)
    btc_close = load_btc_data()
    print(f"  Loaded {len(btc_close)} days: {btc_close.index[0].date()} → {btc_close.index[-1].date()}",
          file=sys.stderr)

    print("Computing indicators ...", file=sys.stderr)
    indicators = compute_indicators(btc_close)
    print(f"  Indicator range: {indicators.index[0].date()} → {indicators.index[-1].date()}",
          file=sys.stderr)

    print("Running strategies ...", file=sys.stderr)
    equity_curves = run_strategies(btc_close, indicators)
    metrics = compute_all_metrics(equity_curves)

    if args.json:
        import json
        output = {
            name: {
                period: {k: float(v) for k, v in pd.items()}
                for period, pd in periods.items()
            }
            for name, periods in metrics.items()
        }
        json.dump(output, sys.stdout, indent=2)
    else:
        print_report(equity_curves, metrics)
        print_regime_distribution(indicators)


if __name__ == "__main__":
    main()

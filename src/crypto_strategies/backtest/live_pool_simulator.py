"""Minimal synthetic live-pool rotation backtest for orchestrator integration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LivePoolBacktestResult:
    metrics: dict[str, float]
    returns: pd.Series
    trade_log: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["signal_date", "effective_date", "turnover", "fee", "slippage", "cost"]
    ))
    cost_status: str = "not_modelled"
    accounting_status: str = "unavailable"
    accounting: dict[str, Any] = field(default_factory=dict)
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    external_flows: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


def _cashflow_adjusted_returns(
    equity: pd.Series,
    external_flows: pd.Series,
    *,
    initial_equity: float = 0.0,
    flow_timing: str = "end",
) -> pd.Series:
    """Return daily time-weighted returns with explicitly timed external flows."""
    if not math.isfinite(float(initial_equity)) or initial_equity < 0.0:
        raise ValueError("initial_equity must be finite and non-negative")
    if flow_timing not in {"start", "end"}:
        raise ValueError("flow_timing must be 'start' or 'end'")
    if not equity.index.equals(external_flows.index):
        raise ValueError("equity and external_flows must have identical indexes")
    values = equity.astype(float)
    flows = external_flows.astype(float)
    if not np.isfinite(values.to_numpy()).all() or not np.isfinite(flows.to_numpy()).all():
        raise ValueError("equity and external_flows must be finite")
    returns: list[float] = []
    prior_equity = float(initial_equity)
    for value, flow in zip(values, flows, strict=True):
        value = float(value)
        flow = float(flow)
        if prior_equity == 0.0:
            if flow_timing != "end" or flow <= 0.0 or value <= 0.0:
                raise ValueError("first period requires positive initial cash or initial_equity")
            period_return = 0.0
        elif flow_timing == "end":
            investment_value = value - flow
            if prior_equity <= 0.0 or investment_value <= 0.0 or value <= 0.0:
                raise ValueError("wealth must remain positive after external cash flows")
            period_return = investment_value / prior_equity - 1.0
        else:
            invested_equity = prior_equity + flow
            if invested_equity <= 0.0 or value <= 0.0:
                raise ValueError("wealth must remain positive after external cash flows")
            period_return = value / invested_equity - 1.0
        if not math.isfinite(period_return) or period_return <= -1.0:
            raise ValueError("net return must be finite and greater than -1.0")
        if abs(period_return) < 1e-12:
            period_return = 0.0
        if value <= 0.0:
            raise ValueError("wealth must remain positive after external cash flows")
        returns.append(period_return)
        prior_equity = value
    return pd.Series(returns, index=equity.index, dtype=float)


def _cashflow_accounting_metrics(
    equity: pd.Series,
    external_flows: pd.Series,
    *,
    initial_equity: float = 0.0,
    initial_equity_date: Any | None = None,
) -> dict[str, Any]:
    """Summarize portfolio cash flows; flows are signed and dated at period end."""
    if not equity.index.equals(external_flows.index):
        raise ValueError("equity and external_flows must have identical indexes")
    values = equity.astype(float)
    flows = external_flows.astype(float)
    if not np.isfinite(values.to_numpy()).all() or not np.isfinite(flows.to_numpy()).all():
        raise ValueError("equity and external_flows must be finite")
    if not math.isfinite(float(initial_equity)) or initial_equity < 0.0:
        raise ValueError("initial_equity must be finite and non-negative")
    if values.empty:
        return {
            "status": "unavailable",
            "xirr_status": "unavailable",
            "xirr": None,
            "initial_equity": float(initial_equity),
            "ending_equity": float(initial_equity),
            "cumulative_contributions": 0.0,
            "cumulative_withdrawals": 0.0,
            "net_contributions": 0.0,
            "net_profit": 0.0,
            "flow_timing": "end_of_day",
            "initial_equity_date": None,
        }
    if (values <= 0.0).any():
        raise ValueError("wealth must remain positive")
    dates = pd.DatetimeIndex(pd.to_datetime(values.index)).normalize()
    investor_flows = -flows.to_numpy(dtype=float)
    xirr_status: str
    if initial_equity > 0.0:
        if initial_equity_date is None:
            xirr, xirr_status = None, "unavailable_missing_initial_equity_date"
        else:
            initial_date = pd.Timestamp(initial_equity_date).normalize()
            if initial_date >= dates[0]:
                raise ValueError("initial_equity_date must precede the first cash-flow date")
            investor_flows = np.insert(investor_flows, 0, -float(initial_equity))
            dates = dates.insert(0, initial_date)
            xirr = None
            xirr_status = "unavailable"
    else:
        xirr = None
        xirr_status = "unavailable"
    if initial_equity <= 0.0 or initial_equity_date is not None:
        investor_flows[-1] += float(values.iloc[-1])
        xirr, xirr_status = _solve_xirr(dates, investor_flows)
    contributions = float(flows.clip(lower=0.0).sum())
    withdrawals = float(-flows.clip(upper=0.0).sum())
    net_contributions = float(initial_equity) + contributions - withdrawals
    return {
        "status": "computed",
        "xirr_status": xirr_status,
        "xirr": xirr,
        "initial_equity": float(initial_equity),
        "ending_equity": float(values.iloc[-1]),
        "cumulative_contributions": contributions,
        "cumulative_withdrawals": withdrawals,
        "net_contributions": net_contributions,
        "net_profit": float(values.iloc[-1]) - net_contributions,
        "flow_timing": "end_of_day",
        "cashflow_start_date": pd.Timestamp(values.index[0]).date().isoformat(),
        "cashflow_end_date": pd.Timestamp(values.index[-1]).date().isoformat(),
        "initial_equity_date": (
            pd.Timestamp(initial_equity_date).date().isoformat()
            if initial_equity > 0.0 and initial_equity_date is not None
            else None
        ),
    }


def _solve_xirr(dates: pd.DatetimeIndex, cashflows: np.ndarray) -> tuple[float | None, str]:
    dates = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    cashflows = np.asarray(cashflows, dtype=float)
    if len(dates) != len(cashflows) or not np.isfinite(cashflows).all():
        return None, "unavailable_invalid_cashflows"
    if len(dates) and dates.nunique() == 1:
        return None, "unavailable_no_elapsed_time"
    grouped = pd.Series(cashflows, index=dates).groupby(level=0, sort=True).sum()
    grouped = grouped.loc[grouped != 0.0]
    dates = pd.DatetimeIndex(grouped.index)
    cashflows = grouped.to_numpy(dtype=float)
    if not len(dates):
        return None, "unavailable_insufficient_signs"
    day_offsets = (dates - dates[0]).days.to_numpy(dtype=float) / 365.25
    if np.ptp(day_offsets) == 0.0:
        return None, "unavailable_no_elapsed_time"
    if not (cashflows < 0).any() or not (cashflows > 0).any():
        return None, "unavailable_insufficient_signs"
    signs = np.sign(cashflows)
    sign_changes = int(np.count_nonzero(signs[1:] != signs[:-1]))

    def npv(rate: float) -> float:
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                return float(np.sum(cashflows / np.power(1.0 + rate, day_offsets)))
        except (FloatingPointError, ZeroDivisionError):
            return math.copysign(math.inf, float(cashflows[0]))

    grid = np.unique(np.concatenate((np.linspace(-0.999999, 1.0, 1000), np.geomspace(2.0, 1001.0, 800) - 1.0, [0.0])))
    roots: list[float] = []
    previous_rate = float(grid[0])
    previous_value = npv(previous_rate)
    for rate_value in grid[1:]:
        rate = float(rate_value)
        value = npv(rate)
        tolerance = 1e-12 * max(1.0, float(np.abs(cashflows).sum()))
        if abs(value) <= tolerance:
            roots.append(rate)
        elif math.isfinite(value) and math.isfinite(previous_value) and value * previous_value < 0.0:
            low, high = previous_rate, rate
            low_value = previous_value
            for _ in range(100):
                mid = (low + high) / 2.0
                mid_value = npv(mid)
                if abs(mid_value) <= 1e-10 or high - low <= 1e-12:
                    low = high = mid
                    break
                if low_value * mid_value <= 0.0:
                    high = mid
                else:
                    low, low_value = mid, mid_value
            roots.append((low + high) / 2.0)
        previous_rate, previous_value = rate, value
    if len(roots) > 1:
        return None, "ambiguous_multiple_roots"
    if sign_changes > 1:
        return None, "ambiguous_nonconventional_cashflows"
    if not roots:
        return None, "unavailable_no_root"
    return min(roots, key=abs), "computed"


def _performance_metrics(
    returns: pd.Series,
    *,
    turnover: pd.Series | None = None,
    fees: pd.Series | None = None,
    slippage: pd.Series | None = None,
) -> dict[str, float]:
    clean = returns.dropna()
    total_turnover = float(turnover.fillna(0.0).sum()) if turnover is not None else 0.0
    total_fees = float(fees.fillna(0.0).sum()) if fees is not None else 0.0
    total_slippage = float(slippage.fillna(0.0).sum()) if slippage is not None else 0.0
    cost_metrics = {
        "Turnover": (total_turnover / len(clean) * 365.25) if len(clean) else 0.0,
        "total_turnover": total_turnover,
        "total_fees": total_fees,
        "total_slippage": total_slippage,
        "total_cost": total_fees + total_slippage,
    }
    if clean.empty:
        return {
            "CAGR": 0.0,
            "Max Drawdown": 0.0,
            "Sharpe": 0.0,
            "Annualized Volatility": 0.0,
            "Win Rate": 0.0,
            "Trading Days": 0.0,
            **cost_metrics,
        }
    equity = (1.0 + clean).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    years = max(len(clean) / 365.25, 1.0 / 365.25)
    cagr = float((equity.iloc[-1]) ** (1.0 / years) - 1.0) if equity.iloc[-1] > 0 else 0.0
    vol = float(clean.std(ddof=0) * np.sqrt(365.25))
    sharpe = float((clean.mean() * 365.25) / vol) if vol > 0 else 0.0
    # High-water must include the unobserved initial equity of 1.0.
    drawdown = float((equity / equity.cummax().clip(lower=1.0) - 1.0).min())
    win_rate = float((clean > 0).mean())
    return {
        "CAGR": cagr,
        "Max Drawdown": drawdown,
        "Sharpe": sharpe,
        "Annualized Volatility": vol,
        "Win Rate": win_rate,
        "Trading Days": float(len(clean)),
        "total_return": total_return,
        **cost_metrics,
    }


def _rebalance_holdings(
    shares: pd.Series,
    cash: float,
    prices: pd.Series,
    targets: pd.Series,
    *,
    cost_rate: float,
) -> tuple[pd.Series, float, float, float, float]:
    """Fill pre-fee targets, selling first and budgeting buy costs from cash.

    Fractional buys scale together when cash is insufficient so entry fees cannot
    self-finance. Returns shares, cash, total costs, sale notional, buy notional.
    """
    needed = (shares > 0.0) | (targets > 0.0)
    if any(not math.isfinite(price) or price <= 0.0 for price in prices[needed]):
        raise ValueError("required open prices must be finite and positive")
    safe_prices = prices.where(needed, 1.0)
    values = shares * safe_prices
    equity = cash + float(values.sum())
    delta = targets * equity - values
    sells = -delta.clip(upper=0.0)
    buys = delta.clip(lower=0.0)
    sale_notional = float(sells.sum())
    available_cash = cash + sale_notional * (1.0 - cost_rate)
    desired_buys = float(buys.sum())
    if desired_buys > 0.0:
        buys *= min(1.0, available_cash / (desired_buys * (1.0 + cost_rate)))
    purchase_notional = float(buys.sum())
    costs = (sale_notional + purchase_notional) * cost_rate
    cash = max(0.0, available_cash - purchase_notional * (1.0 + cost_rate))
    return (values - sells + buys) / safe_prices, cash, costs, sale_notional, purchase_notional


def run_live_pool_rotation_backtest(
    panel: pd.DataFrame,
    *,
    score_column: str = "final_score",
    top_n: int = 2,
    rebalance_every: int = 7,
    signal_lag_days: int = 1,
    fee_bps: float | None = None,
    slippage_bps: float = 0.0,
    signal_lag: int | None = None,
    fee_rate: float | None = None,
) -> LivePoolBacktestResult:
    """Long-only equal-weight top-N rotation on a synthetic/live panel.

    A score observed on ``signal_date`` is tradable at ``effective_date`` after
    ``signal_lag`` rows; returns are measured from that effective open to the
    next open. Rebalances use a cash/share ledger so entry costs constrain the
    affordable notional instead of self-financing full target weights.
    Required execution and valuation opens must be finite and positive; missing
    prices on unexposed assets do not invalidate a cash or invested period.
    """
    if int(top_n) <= 0:
        raise ValueError("top_n must be positive")
    if int(rebalance_every) <= 0:
        raise ValueError("rebalance_every must be positive")
    if signal_lag is not None:
        if signal_lag_days != 1 and int(signal_lag_days) != int(signal_lag):
            raise ValueError("signal_lag and signal_lag_days disagree")
        signal_lag_days = int(signal_lag)
    if fee_rate is not None:
        if fee_bps is not None and not math.isclose(float(fee_bps), float(fee_rate) * 10_000.0):
            raise ValueError("fee_rate and fee_bps disagree")
        fee_bps = float(fee_rate) * 10_000.0
    elif fee_bps is None:
        fee_bps = 0.0
    if int(signal_lag_days) < 0:
        raise ValueError("signal_lag_days must be non-negative")
    if not math.isfinite(float(fee_bps)) or float(fee_bps) < 0:
        raise ValueError("fee_bps must be a finite non-negative number")
    if not math.isfinite(float(slippage_bps)) or float(slippage_bps) < 0:
        raise ValueError("slippage_bps must be a finite non-negative number")

    top_n = int(top_n)
    rebalance_every = int(rebalance_every)
    signal_lag = int(signal_lag_days)
    fee_bps = float(fee_bps)
    slippage_bps = float(slippage_bps)
    fee_rate_value = fee_bps / 10_000.0
    slippage_rate = slippage_bps / 10_000.0
    cost_rate = fee_rate_value + slippage_rate
    if not math.isfinite(cost_rate) or not 0.0 <= cost_rate < 1.0:
        raise ValueError("transaction cost must be less than 1.0")

    dates = sorted(panel.index.get_level_values("date").unique())
    if dates and not pd.DatetimeIndex(dates).equals(pd.date_range(dates[0], dates[-1], freq="D")):
        raise ValueError("panel dates must be consecutive calendar days")
    symbols = sorted(panel.loc[panel["in_universe"]].index.get_level_values("symbol").unique())
    if not dates or not symbols:
        empty = pd.Series(dtype=float)
        return LivePoolBacktestResult(metrics=_performance_metrics(empty), returns=empty)

    open_matrix = (
        panel.reset_index()
        .pivot(index="date", columns="symbol", values="open")
        .reindex(index=dates, columns=symbols)
        .astype(float)
    )

    shares = pd.Series(0.0, index=symbols, dtype=float)
    cash = 1.0
    equity = 1.0
    daily_returns: list[float] = []
    daily_turnover: list[float] = []
    daily_fees: list[float] = []
    daily_slippage: list[float] = []
    trade_records: list[dict[str, Any]] = []
    effective_dates = dates[signal_lag:-1]
    for effective_idx, effective_date in zip(
        range(signal_lag, len(dates) - 1), effective_dates, strict=True
    ):
        signal_idx = effective_idx - signal_lag
        signal_date = dates[signal_idx]
        turnover = 0.0
        fee = 0.0
        slippage = 0.0
        current_prices = open_matrix.loc[effective_date]

        if signal_idx % rebalance_every == 0:
            snapshot = panel.xs(signal_date, level="date")
            ranked = (
                snapshot[snapshot["in_universe"]]
                .sort_values(score_column, ascending=False)
                .head(top_n)
            )
            target_weights = pd.Series(0.0, index=symbols, dtype=float)
            if not ranked.empty:
                weight = 1.0 / len(ranked)
                for symbol in ranked.index:
                    target_weights.loc[symbol] = weight
            shares, cash, costs, sale_notional, purchase_notional = _rebalance_holdings(
                shares,
                cash,
                current_prices,
                target_weights,
                cost_rate=cost_rate,
            )
            traded = sale_notional + purchase_notional
            turnover = traded / (2.0 * equity) if equity > 0.0 else 0.0
            if cost_rate > 0.0 and costs > 0.0:
                fee = costs * (fee_rate_value / cost_rate)
                slippage = costs * (slippage_rate / cost_rate)
            else:
                fee = 0.0
                slippage = 0.0
            trade_records.append(
                {
                    "signal_date": pd.Timestamp(signal_date),
                    "effective_date": pd.Timestamp(effective_date),
                    "turnover": turnover,
                    "fee": fee,
                    "slippage": slippage,
                    "cost": fee + slippage,
                }
            )

        held = shares > 0.0
        next_prices = open_matrix.iloc[effective_idx + 1]
        if held.any():
            required = next_prices.loc[held]
            if not (np.isfinite(required) & required.gt(0.0)).all():
                raise ValueError("required open prices must be finite and positive")
        marked = cash + float((shares[held] * next_prices[held]).sum())
        if equity <= 0.0:
            raise ValueError("net return must be greater than -1.0")
        net_return = marked / equity - 1.0
        if net_return <= -1.0:
            raise ValueError("net return must be greater than -1.0")
        daily_returns.append(net_return)
        daily_turnover.append(turnover)
        daily_fees.append(fee)
        daily_slippage.append(slippage)
        equity = marked

    returns = pd.Series(daily_returns, index=pd.DatetimeIndex(effective_dates))
    turnover = pd.Series(daily_turnover, index=returns.index, dtype=float)
    fees = pd.Series(daily_fees, index=returns.index, dtype=float)
    slippage = pd.Series(daily_slippage, index=returns.index, dtype=float)
    trade_log = pd.DataFrame(trade_records)
    if trade_log.empty:
        trade_log = pd.DataFrame(
            columns=["signal_date", "effective_date", "turnover", "fee", "slippage", "cost"]
        )
    return LivePoolBacktestResult(
        metrics=_performance_metrics(returns, turnover=turnover, fees=fees, slippage=slippage),
        returns=returns,
        trade_log=trade_log,
    )

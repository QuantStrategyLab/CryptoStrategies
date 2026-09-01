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
    drawdown = float((equity / equity.cummax() - 1.0).min())
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


def run_live_pool_rotation_backtest(
    panel: pd.DataFrame,
    *,
    score_column: str = "final_score",
    top_n: int = 2,
    rebalance_every: int = 7,
    signal_lag_days: int = 1,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    signal_lag: int | None = None,
    fee_rate: float | None = None,
) -> LivePoolBacktestResult:
    """Long-only equal-weight top-N rotation on a synthetic/live panel.

    A score observed on ``signal_date`` is tradable at ``effective_date`` after
    ``signal_lag`` rows; returns are measured from that effective open to the
    next open. Costs are charged on half-L1 turnover at each rebalance.
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
        if fee_bps != 0.0 and not math.isclose(float(fee_bps), float(fee_rate) * 10_000.0):
            raise ValueError("fee_rate and fee_bps disagree")
        fee_bps = float(fee_rate) * 10_000.0
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
    dates = sorted(panel.index.get_level_values("date").unique())
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
    open_returns = open_matrix.shift(-1).div(open_matrix).sub(1.0).fillna(0.0)

    portfolio_weights = pd.Series(0.0, index=symbols, dtype=float)
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
            previous_cash_weight = 1.0 - float(portfolio_weights.sum())
            target_cash_weight = 1.0 - float(target_weights.sum())
            turnover = float(
                ((target_weights - portfolio_weights).abs().sum() + abs(target_cash_weight - previous_cash_weight))
                * 0.5
            )
            fee = turnover * fee_bps / 10_000.0
            slippage = turnover * slippage_bps / 10_000.0
            portfolio_weights = target_weights
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

        gross_return = float((portfolio_weights * open_returns.loc[effective_date]).sum())
        cost = fee + slippage
        if cost >= 1.0:
            raise ValueError("transaction cost must be less than 1.0")
        net_return = gross_return - cost
        if net_return <= -1.0:
            raise ValueError("net return must be greater than -1.0")
        daily_returns.append(net_return)
        daily_turnover.append(turnover)
        daily_fees.append(fee)
        daily_slippage.append(slippage)
        gross_growth = 1.0 + gross_return
        portfolio_weights = (
            portfolio_weights.mul(1.0 + open_returns.loc[effective_date])
            .div(gross_growth)
            .fillna(0.0)
            if gross_growth > 0.0
            else pd.Series(0.0, index=symbols, dtype=float)
        )

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

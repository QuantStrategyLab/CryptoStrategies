"""Minimal synthetic live-pool rotation backtest for orchestrator integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LivePoolBacktestResult:
    metrics: dict[str, float]
    returns: pd.Series


def _performance_metrics(returns: pd.Series) -> dict[str, float]:
    clean = returns.dropna()
    if clean.empty:
        return {
            "CAGR": 0.0,
            "Max Drawdown": 0.0,
            "Sharpe": 0.0,
            "Annualized Volatility": 0.0,
            "Win Rate": 0.0,
            "Trading Days": 0.0,
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
    }


def run_live_pool_rotation_backtest(
    panel: pd.DataFrame,
    *,
    score_column: str = "final_score",
    top_n: int = 2,
    rebalance_every: int = 7,
) -> LivePoolBacktestResult:
    """Long-only equal-weight top-N rotation on a synthetic/live panel."""
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

    weights = pd.Series(0.0, index=symbols, dtype=float)
    daily_returns: list[float] = []
    for idx, day in enumerate(dates[:-1]):
        if idx % rebalance_every == 0:
            snapshot = panel.xs(day, level="date")
            ranked = (
                snapshot[snapshot["in_universe"]]
                .sort_values(score_column, ascending=False)
                .head(top_n)
            )
            weights[:] = 0.0
            if not ranked.empty:
                weight = 1.0 / len(ranked)
                for symbol in ranked.index:
                    weights.loc[symbol] = weight
        daily_returns.append(float((weights * open_returns.loc[day]).sum()))

    returns = pd.Series(daily_returns, index=pd.DatetimeIndex(dates[:-1]))
    return LivePoolBacktestResult(metrics=_performance_metrics(returns), returns=returns)

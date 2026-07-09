"""Shared helpers for crypto research scripts calling BacktestOrchestrator adapters."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import pandas as pd

from crypto_strategies.backtest.orchestrator_runner import (
    COMBO_DEFAULT_MIN_HISTORY_DAYS,
    CryptoEquityComboBacktestRunner,
    CryptoLivePoolBacktestRunner,
    DEFAULT_MIN_HISTORY_DAYS,
    PROFILE_NAME as LIVE_POOL_PROFILE,
)
from crypto_strategies.strategies.crypto_equity_combo import PROFILE_NAME as CRYPTO_EQUITY_COMBO_PROFILE


def _result_to_metrics(result: Any) -> dict[str, Any]:
    return {
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown": result.max_drawdown,
        "annual_return": result.cagr,
        "total_return": result.total_return,
        "annual_volatility": result.volatility,
        "days": result.observation_count,
    }


def run_live_pool_profile_backtest(
    profile: str,
    *,
    panel: pd.DataFrame | None = None,
    synthetic_days: int = 1600,
    start_date: date | None = None,
    end_date: date | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single-window live-pool rotation backtest through CryptoLivePoolBacktestRunner."""
    if profile != LIVE_POOL_PROFILE:
        raise ValueError(f"unsupported profile={profile!r}")

    runner = CryptoLivePoolBacktestRunner(panel=panel, synthetic_days=synthetic_days)
    merged_params = {
        "min_history_days": DEFAULT_MIN_HISTORY_DAYS,
        "top_n": 2,
        "rebalance_every": 7,
    }
    if params:
        merged_params.update(dict(params))
    result = runner.run(profile, merged_params, start_date=start_date, end_date=end_date)
    return {
        "profile": profile,
        "params": merged_params,
        "start_date": result.start_date.isoformat() if result.start_date else None,
        "end_date": result.end_date.isoformat() if result.end_date else None,
        "metrics": _result_to_metrics(result),
        "source": "CryptoLivePoolBacktestRunner",
        "run_id": getattr(result, "run_id", None),
    }


def run_combo_profile_backtest(
    profile: str,
    *,
    market_history: pd.DataFrame | None = None,
    synthetic_days: int = 1600,
    start_date: date | None = None,
    end_date: date | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single-window crypto equity combo backtest through CryptoEquityComboBacktestRunner."""
    if profile != CRYPTO_EQUITY_COMBO_PROFILE:
        raise ValueError(f"unsupported profile={profile!r}")

    runner = CryptoEquityComboBacktestRunner(
        market_history=market_history,
        synthetic_days=synthetic_days,
    )
    merged_params = {
        "min_history_days": COMBO_DEFAULT_MIN_HISTORY_DAYS,
        "combo_mode": "dynamic",
    }
    if params:
        merged_params.update(dict(params))
    result = runner.run(profile, merged_params, start_date=start_date, end_date=end_date)
    return {
        "profile": profile,
        "params": merged_params,
        "start_date": result.start_date.isoformat() if result.start_date else None,
        "end_date": result.end_date.isoformat() if result.end_date else None,
        "metrics": _result_to_metrics(result),
        "source": "CryptoEquityComboBacktestRunner",
        "run_id": getattr(result, "run_id", None),
    }


__all__ = ["run_combo_profile_backtest", "run_live_pool_profile_backtest"]

"""BacktestRunner adapter for crypto live pool rotation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

import numpy as np
import pandas as pd

from crypto_strategies.backtest.live_pool_simulator import run_live_pool_rotation_backtest

try:
    from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult
except ImportError:  # pragma: no cover
    BacktestResult = None  # type: ignore[misc, assignment]


PROFILE_NAME = "crypto_live_pool_rotation"
DEFAULT_MIN_HISTORY_DAYS = 120
SUPPORTED_PROFILES = frozenset({PROFILE_NAME})


def _synthetic_panel(*, days: int = 1500, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=days, freq="D")
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    panel = pd.DataFrame(index=index)
    panel["in_universe"] = True
    rng = np.random.default_rng(42)
    rows: list[float] = []
    for symbol in symbols:
        price = 100.0 + hash(symbol) % 50
        for _ in dates:
            price *= 1.0 + float(rng.normal(0.001, 0.02))
            rows.append(price)
    panel["open"] = rows
    scores: list[float] = []
    for day_idx, _day in enumerate(dates):
        for sym_idx, symbol in enumerate(symbols):
            scores.append(float((day_idx + sym_idx * 17 + hash(symbol) % 11) % 100) / 100.0)
    panel["final_score"] = scores
    return panel.sort_index()


def _slice_panel(panel: pd.DataFrame, *, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    level_dates = panel.index.get_level_values("date")
    frame = panel
    if start_date is not None:
        frame = frame.loc[level_dates >= pd.Timestamp(start_date)]
        level_dates = frame.index.get_level_values("date")
    if end_date is not None:
        frame = frame.loc[level_dates <= pd.Timestamp(end_date)]
    return frame.sort_index()


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
        )
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


__all__ = ["PROFILE_NAME", "SUPPORTED_PROFILES", "CryptoLivePoolBacktestRunner"]

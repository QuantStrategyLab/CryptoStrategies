from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from quant_platform_kit.risk.gate import apply_risk_gate as _qpk_apply_risk_gate
from quant_platform_kit.risk.gate import enrich_decision_risk_diagnostics
from quant_platform_kit.risk.portfolio_diagnostics import extract_portfolio_risk_diagnostics
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext, StrategyDecision
from quant_platform_kit.strategy_lifecycle.performance_monitor import PerformanceMonitor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 风控硬门 — 每个 entrypoint 返回 StrategyDecision 前必须调用
# ---------------------------------------------------------------------------

_performance_monitor: PerformanceMonitor | None = None


def record_strategy_decision(
    _ctx: object,
    decision: StrategyDecision,
    *,
    profile_id: str,
    domain: str,
) -> None:
    """Record per-run decision for live monitoring (roadmap 5a).

    Crypto entrypoints don't consistently thread `StrategyContext` typing, so we
    accept `ctx` as `object` and only use it for future extension.
    """
    global _performance_monitor
    try:
        if _performance_monitor is None:
            _performance_monitor = PerformanceMonitor()
        _performance_monitor.record(
            profile_id,
            decision,
            execution_result={},
            domain=domain,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("PerformanceMonitor.record failed: %s", exc)


def apply_risk_gate(
    decision: StrategyDecision,
    *,
    ctx: StrategyContext | None = None,
    max_single_weight: float = 1.0,
    max_positions: int = 20,
    max_total_exposure: float = 1.0,
    portfolio_snapshot: Any | None = None,
    market_data: Mapping[str, Any] | None = None,
) -> StrategyDecision:
    """QPK unified risk gate: stop-loss, circuit breaker, concentration (task 8)."""
    snapshot = portfolio_snapshot if portfolio_snapshot is not None else (
        ctx.portfolio if ctx is not None else None
    )
    if snapshot is not None:
        portfolio_diag = extract_portfolio_risk_diagnostics(snapshot)
        decision = enrich_decision_risk_diagnostics(
            decision,
            unrealized_pnl_pct=portfolio_diag.get("unrealized_pnl_pct"),
            consecutive_losses=portfolio_diag.get("consecutive_losses"),
        )
    if market_data is None and ctx is not None:
        market_data = dict(ctx.market_data or {})
    return _qpk_apply_risk_gate(
        decision,
        max_single_weight=max_single_weight,
        max_positions=max_positions,
        max_total_exposure=max_total_exposure,
        portfolio_snapshot=snapshot,
        market_data=market_data,
    )

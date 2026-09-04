from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from quant_platform_kit.risk.contracts import CandidateRiskIdentity
from quant_platform_kit.risk.gate import assess_with_evidence as _qpk_assess_with_evidence
from quant_platform_kit.risk.gate import enrich_decision_risk_diagnostics
from quant_platform_kit.risk.portfolio_diagnostics import extract_portfolio_risk_diagnostics
from quant_platform_kit.common.strategy_contracts import StrategyContext, StrategyDecision
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
    max_single_weight: float | None = None,
    portfolio_snapshot: Any | None = None,
    market_data: Mapping[str, Any] | None = None,
) -> StrategyDecision:
    """Run the QPK MEMBER gate and propagate only its redacted assessment."""
    snapshot = portfolio_snapshot
    if snapshot is None and ctx is not None:
        snapshot = ctx.portfolio
        if snapshot is None:
            snapshot = ctx.market_data.get("portfolio_snapshot")
    if snapshot is not None:
        portfolio_diag = extract_portfolio_risk_diagnostics(snapshot)
        decision = enrich_decision_risk_diagnostics(
            decision,
            unrealized_pnl_pct=portfolio_diag.get("unrealized_pnl_pct"),
            consecutive_losses=portfolio_diag.get("consecutive_losses"),
        )
    if market_data is None and ctx is not None:
        market_data = dict(ctx.market_data or {})
    mandate_provenance = None if ctx is None else ctx.artifacts.get("mandate_provenance")
    if not isinstance(mandate_provenance, Mapping):
        mandate_provenance = {}
    candidate_identity = None if ctx is None else ctx.artifacts.get("candidate_risk_identity")
    if not isinstance(candidate_identity, CandidateRiskIdentity):
        candidate_identity = None
    result = _qpk_assess_with_evidence(
        decision,
        snapshot,
        scope="MEMBER",
        mandate_provenance=mandate_provenance,
        market_data=market_data or {},
        candidate_identity=candidate_identity,
    )
    risk_flags = tuple(
        dict.fromkeys(tuple(decision.risk_flags or ()) + tuple(result.decision.risk_flags or ()))
    )
    strategy_weights = []
    strategy_weight_invalid = False
    for position in result.decision.positions:
        try:
            weight = float(position.target_weight)
        except (TypeError, ValueError):
            strategy_weight_invalid = True
            break
        if not math.isfinite(weight):
            strategy_weight_invalid = True
            break
        strategy_weights.append(abs(weight))
    strategy_concentration_rejected = False
    if max_single_weight is not None:
        cap = float(max_single_weight)
        if not math.isfinite(cap) or not 0.0 <= cap <= 1.0:
            raise ValueError("max_single_weight must be finite and between 0 and 1")
        strategy_concentration_rejected = strategy_weight_invalid or any(
            weight > cap for weight in strategy_weights
        )
    if strategy_concentration_rejected:
        risk_flags = tuple(dict.fromkeys(risk_flags + ("rejected:strategy_concentration",)))
    strategy_position_count_rejected = len(result.decision.positions) > 20
    if strategy_position_count_rejected:
        risk_flags = tuple(dict.fromkeys(risk_flags + ("rejected:too_many_positions",)))
    total_exposure = sum(strategy_weights)
    strategy_total_exposure_rejected = (
        strategy_weight_invalid
        or not math.isfinite(total_exposure)
        or total_exposure > 1.0 + 1e-9
    )
    if strategy_total_exposure_rejected:
        risk_flags = tuple(dict.fromkeys(risk_flags + ("rejected:overexposed",)))
    strategy_rejected = (
        strategy_concentration_rejected
        or strategy_position_count_rejected
        or strategy_total_exposure_rejected
    )
    return StrategyDecision(
        positions=() if strategy_rejected else result.decision.positions,
        budgets=() if strategy_rejected else result.decision.budgets,
        risk_flags=risk_flags,
        diagnostics={
            **dict(result.decision.diagnostics or {}),
            "member_risk_assessment": asdict(result.assessment),
        },
    )

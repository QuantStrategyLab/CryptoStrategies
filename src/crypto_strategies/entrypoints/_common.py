from __future__ import annotations

import logging

from quant_platform_kit.strategy_contracts import PositionTarget, StrategyDecision
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


def _position_weight(position: PositionTarget) -> float | None:
    if position.target_weight is not None:
        return abs(float(position.target_weight))
    return None


def _reject_risk_gate(
    decision: StrategyDecision,
    *,
    risk_flag: str,
    reason: str,
) -> StrategyDecision:
    return StrategyDecision(
        positions=(),
        budgets=decision.budgets,
        risk_flags=(*decision.risk_flags, risk_flag),
        diagnostics={
            **(decision.diagnostics or {}),
            "risk_gate": "REJECT",
            "reason": reason,
        },
    )


def apply_risk_gate(
    decision: StrategyDecision,
    *,
    max_single_weight: float = 1.0,
    max_positions: int = 20,
    max_total_exposure: float = 1.0,
) -> StrategyDecision:
    """对所有 StrategyDecision 施加硬风控门。

    检查项：
    1. 单仓位集中度（> max_single_weight → REJECT，默认 100% 即不限制）
    2. 持仓数量（> max_positions → REJECT）
    3. 总仓位超限（> max_total_exposure → REJECT）

    各策略类型可根据自身特点调整门限：
    - ETF 轮动：max_single_weight=1.0（ETF 本身就是分散的篮子）
    - 个股精选：max_single_weight=0.10
    - 加密货币：max_single_weight=0.20, max_positions=10

    如果 REJECT，返回空仓决策并标注拒绝原因。
    这个函数不可绕过 —— AGENTS.md 要求所有 entrypoint 必须调用。
    """
    positions = decision.positions or ()

    if not positions:
        return StrategyDecision(
            positions=decision.positions,
            budgets=decision.budgets,
            risk_flags=decision.risk_flags,
            diagnostics={**(decision.diagnostics or {}), "risk_gate": "APPROVE"},
        )

    weight_positions = [p for p in positions if _position_weight(p) is not None]

    if max_single_weight < 1.0 and weight_positions:
        for p in weight_positions:
            weight = _position_weight(p)
            assert weight is not None
            if weight > max_single_weight:
                logger.warning(
                    "risk_gate REJECT concentration: symbol=%s weight=%.2f%% limit=%.0f%%",
                    p.symbol, weight * 100, max_single_weight * 100,
                )
                return _reject_risk_gate(
                    decision,
                    risk_flag="rejected:concentration",
                    reason=f"{p.symbol} {weight:.1%} > {max_single_weight:.0%} 上限",
                )

    if len(positions) > max_positions:
        logger.warning(
            "risk_gate REJECT position_count: %d > %d", len(positions), max_positions,
        )
        return _reject_risk_gate(
            decision,
            risk_flag="rejected:too_many_positions",
            reason=f"{len(positions)} 个持仓 > {max_positions} 上限",
        )

    if weight_positions:
        total_weight = sum(_position_weight(p) or 0.0 for p in weight_positions)
        if total_weight > max_total_exposure + 1e-9:
            logger.warning(
                "risk_gate REJECT total_exposure: %.2f%% > %.0f%%",
                total_weight * 100, max_total_exposure * 100,
            )
            return _reject_risk_gate(
                decision,
                risk_flag="rejected:overexposed",
                reason=f"总仓位 {total_weight:.1%} > {max_total_exposure:.0%}",
            )

    return StrategyDecision(
        positions=decision.positions,
        budgets=decision.budgets,
        risk_flags=decision.risk_flags,
        diagnostics={**(decision.diagnostics or {}), "risk_gate": "APPROVE"},
    )

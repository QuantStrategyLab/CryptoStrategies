from __future__ import annotations

import logging

from quant_platform_kit.strategy_contracts import StrategyDecision

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 风控硬门 — 每个 entrypoint 返回 StrategyDecision 前必须调用
# ---------------------------------------------------------------------------


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
    risk_flags = list(decision.risk_flags or ())

    # 空仓放行（risk_off 场景）
    if not positions:
        return decision

    # 1. 集中度检查（默认不限制，由策略自行设定）
    if max_single_weight < 1.0:
        for p in positions:
            weight = abs(float(p.target_weight))
            if weight > max_single_weight:
                logger.warning(
                    "risk_gate REJECT concentration: symbol=%s weight=%.2f%% limit=%.0f%%",
                    p.symbol, weight * 100, max_single_weight * 100,
                )
                return StrategyDecision(
                    positions=(),
                    risk_flags=("rejected:concentration",),
                    diagnostics={
                        **(decision.diagnostics or {}),
                        "risk_gate": "REJECT",
                        "reason": f"{p.symbol} {weight:.1%} > {max_single_weight:.0%} 上限",
                    },
                )

    # 2. 持仓数量检查
    if len(positions) > max_positions:
        logger.warning(
            "risk_gate REJECT position_count: %d > %d", len(positions), max_positions,
        )
        return StrategyDecision(
            positions=(),
            risk_flags=("rejected:too_many_positions",),
            diagnostics={
                **(decision.diagnostics or {}),
                "risk_gate": "REJECT",
                "reason": f"{len(positions)} 个持仓 > {max_positions} 上限",
            },
        )

    # 3. 总仓位检查
    total_weight = sum(abs(float(p.target_weight)) for p in positions)
    if total_weight > max_total_exposure + 1e-9:
        logger.warning(
            "risk_gate REJECT total_exposure: %.2f%% > %.0f%%",
            total_weight * 100, max_total_exposure * 100,
        )
        return StrategyDecision(
            positions=(),
            risk_flags=("rejected:overexposed",),
            diagnostics={
                **(decision.diagnostics or {}),
                "risk_gate": "REJECT",
                "reason": f"总仓位 {total_weight:.1%} > {max_total_exposure:.0%}",
            },
        )

    # 通过
    risk_flags.append("risk_gate:passed")
    return StrategyDecision(
        positions=decision.positions,
        risk_flags=tuple(risk_flags),
        diagnostics={**(decision.diagnostics or {}), "risk_gate": "APPROVE"},
    )

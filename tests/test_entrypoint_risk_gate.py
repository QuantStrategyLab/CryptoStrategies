from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import BudgetIntent, PositionTarget, StrategyContext, StrategyDecision

from crypto_strategies.entrypoints._common import apply_risk_gate


def _zero_cap_mandate(now: datetime) -> dict[str, object]:
    return {
        "mandate_id": "binance_crypto_research_only_v1",
        "mandate_version": "2026-08-04.1",
        "authority_receipt_sha256": "246c39b8023b25f913bf1e67dc175005955a7102f3727dfc1bd8e981cf8128ee",
        "authority_scope": "RESEARCH_ONLY",
        "strategy_profile": "crypto_live_pool_rotation",
        "account_mode": "single_strategy_account_v1",
        "effective_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "max_snapshot_age_seconds": 300,
        "effective_exposure_cap": 0.0,
        "loss_budget": 0.0,
        "product_caps": 0.0,
        "nominal_caps": 0.0,
        "product_leverage_factors": {},
        "allowed_nonzero_assets": [],
        "source_revision": "b371322b948e4298920a7d8613b155245dcd5f8d",
    }


def test_apply_risk_gate_enriches_stop_loss_diagnostics_from_portfolio() -> None:
    snapshot = PortfolioSnapshot(
        as_of=datetime(2026, 7, 9, tzinfo=timezone.utc),
        total_equity=1000.0,
        positions=(
            Position(symbol="BTCUSDT", quantity=1.0, market_value=700.0, average_cost=1000.0),
        ),
        metadata={"consecutive_losses": 2},
    )
    ctx = StrategyContext(as_of=snapshot.as_of, portfolio=snapshot, market_data={}, state={}, runtime_config={})
    decision = StrategyDecision(positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.5),))
    result = apply_risk_gate(decision, ctx=ctx)
    assert result.positions == ()
    assert "rejected:risk_gate_assessment" in result.risk_flags
    assert result.diagnostics["member_risk_assessment"]["outcome"] == "REJECT"


def test_apply_risk_gate_uses_member_evidence_and_zero_cap_clears_authority() -> None:
    now = datetime.now(timezone.utc)
    snapshot = PortfolioSnapshot(
        as_of=now,
        total_equity=1000.0,
        metadata={
            "observed_effective_exposure": 0.0,
            "private_position_rows": [{"symbol": "BTCUSDT", "quantity": 123.0}],
        },
    )
    ctx = StrategyContext(
        as_of=now,
        portfolio=snapshot,
        market_data={"private_api_token": "must-not-propagate"},
        artifacts={"mandate_provenance": _zero_cap_mandate(now)},
    )
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.1),),
        budgets=(BudgetIntent(name="btc", amount=1.0),),
    )

    result = apply_risk_gate(decision, ctx=ctx)

    assessment = result.diagnostics["member_risk_assessment"]
    assert result.positions == ()
    assert result.budgets == ()
    assert assessment["contract_version"] == "qsl.risk_gate_assessment.v1"
    assert assessment["scope"] == "MEMBER"
    assert assessment["outcome"] == "REJECT"
    assert assessment["mandate_id"] == "binance_crypto_research_only_v1"
    assert "private_position_rows" not in repr(assessment)
    assert "must-not-propagate" not in repr(assessment)


def test_apply_risk_gate_preserves_stricter_strategy_concentration_cap() -> None:
    now = datetime.now(timezone.utc)
    mandate = _zero_cap_mandate(now)
    mandate.update(
        {
            "mandate_id": "synthetic_algorithm_equivalence_only",
            "effective_exposure_cap": 1.0,
            "loss_budget": 1000.0,
            "product_caps": {"BTCUSDT": 1.0},
            "nominal_caps": {"BTCUSDT": 1.0},
            "product_leverage_factors": {"BTCUSDT": 1},
            "allowed_nonzero_assets": ["BTCUSDT"],
        }
    )
    snapshot = PortfolioSnapshot(
        as_of=now,
        total_equity=1000.0,
        metadata={"observed_effective_exposure": 0.0},
    )
    ctx = StrategyContext(
        as_of=now,
        portfolio=snapshot,
        artifacts={"mandate_provenance": mandate},
    )

    result = apply_risk_gate(
        StrategyDecision(positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.6),)),
        ctx=ctx,
        max_single_weight=0.5,
    )

    assert result.diagnostics["member_risk_assessment"]["outcome"] == "APPROVE"
    assert result.positions == ()
    assert result.budgets == ()
    assert "rejected:strategy_concentration" in result.risk_flags

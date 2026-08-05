from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.risk import gate as qpk_risk_gate
from quant_platform_kit.risk.contracts import (
    CandidateRiskIdentity,
    RiskGateAssessment,
    RiskGateResult,
)
from quant_platform_kit.strategy_contracts import BudgetIntent, PositionTarget, StrategyContext, StrategyDecision

from crypto_strategies.entrypoints._common import apply_risk_gate


def _candidate_identity(
    *, strategy_profile: str = "crypto_live_pool_rotation"
) -> CandidateRiskIdentity:
    return CandidateRiskIdentity(
        strategy_profile=strategy_profile,
        account_mode="single_strategy_account_v1",
        strategy_revision="1" * 40,
        runner_revision="2" * 40,
        config_sha256="3" * 64,
        input_manifest_sha256="4" * 64,
        authority_receipt_sha256="246c39b8023b25f913bf1e67dc175005955a7102f3727dfc1bd8e981cf8128ee",
    )


def _zero_cap_mandate(
    now: datetime,
    *,
    candidate_identity: CandidateRiskIdentity,
) -> dict[str, object]:
    return {
        "mandate_id": "binance_crypto_research_only_v1",
        "mandate_version": "2026-08-04.1",
        "authority_receipt_sha256": candidate_identity.authority_receipt_sha256,
        "authority_scope": "RESEARCH_ONLY",
        "strategy_profile": candidate_identity.strategy_profile,
        "account_mode": candidate_identity.account_mode,
        "strategy_revision": candidate_identity.strategy_revision,
        "runner_revision": candidate_identity.runner_revision,
        "config_sha256": candidate_identity.config_sha256,
        "input_manifest_sha256": candidate_identity.input_manifest_sha256,
        "candidate_identity_sha256": candidate_identity.candidate_sha256,
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


def _candidate_bound_artifacts(
    now: datetime,
    *,
    candidate_identity: CandidateRiskIdentity | None = None,
) -> dict[str, object]:
    candidate = candidate_identity or _candidate_identity()
    return {
        "mandate_provenance": _zero_cap_mandate(
            now,
            candidate_identity=candidate,
        ),
        "candidate_risk_identity": candidate,
    }


def _apply_risk_gate_once(
    decision: StrategyDecision,
    **kwargs: object,
) -> StrategyDecision:
    engine = qpk_risk_gate.build_risk_engine()
    with patch.object(engine, "assess", wraps=engine.assess) as assess, patch.object(
        qpk_risk_gate,
        "build_risk_engine",
        return_value=engine,
    ):
        result = apply_risk_gate(decision, **kwargs)
    assess.assert_called_once()
    return result


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
    result = _apply_risk_gate_once(decision, ctx=ctx)
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
        artifacts=_candidate_bound_artifacts(now),
    )
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.1),),
        budgets=(BudgetIntent(name="btc", amount=1.0),),
    )

    result = _apply_risk_gate_once(decision, ctx=ctx)

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
    candidate_identity = _candidate_identity()
    mandate = _zero_cap_mandate(now, candidate_identity=candidate_identity)
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
        artifacts={
            "mandate_provenance": mandate,
            "candidate_risk_identity": candidate_identity,
        },
    )

    result = _apply_risk_gate_once(
        StrategyDecision(positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.6),)),
        ctx=ctx,
        max_single_weight=0.5,
    )

    assert result.diagnostics["member_risk_assessment"]["outcome"] == "APPROVE"
    assert result.positions == ()
    assert result.budgets == ()
    assert "rejected:strategy_concentration" in result.risk_flags


def test_apply_risk_gate_preserves_hard_position_count_limit() -> None:
    now = datetime.now(timezone.utc)
    symbols = [f"ASSET{index}USDT" for index in range(21)]
    candidate_identity = _candidate_identity()
    mandate = _zero_cap_mandate(now, candidate_identity=candidate_identity)
    mandate.update(
        {
            "mandate_id": "synthetic_algorithm_equivalence_only",
            "effective_exposure_cap": 1.0,
            "loss_budget": 1000.0,
            "product_caps": {symbol: 1.0 for symbol in symbols},
            "nominal_caps": {symbol: 1.0 for symbol in symbols},
            "product_leverage_factors": {symbol: 1 for symbol in symbols},
            "allowed_nonzero_assets": symbols,
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
        artifacts={
            "mandate_provenance": mandate,
            "candidate_risk_identity": candidate_identity,
        },
    )
    decision = StrategyDecision(
        positions=tuple(
            PositionTarget(symbol=symbol, target_weight=0.04) for symbol in symbols
        ),
        budgets=(BudgetIntent(name="portfolio", amount=1.0),),
    )

    result = _apply_risk_gate_once(decision, ctx=ctx)

    assert result.diagnostics["member_risk_assessment"]["outcome"] == "APPROVE"
    assert result.positions == ()
    assert result.budgets == ()
    assert "rejected:too_many_positions" in result.risk_flags


def test_apply_risk_gate_preserves_hard_total_exposure_limit() -> None:
    now = datetime.now(timezone.utc)
    symbols = [f"ASSET{index}USDT" for index in range(5)]
    candidate_identity = _candidate_identity()
    mandate = _zero_cap_mandate(now, candidate_identity=candidate_identity)
    mandate.update(
        {
            "mandate_id": "synthetic_algorithm_equivalence_only",
            "effective_exposure_cap": 2.0,
            "loss_budget": 1000.0,
            "product_caps": {symbol: 1.0 for symbol in symbols},
            "nominal_caps": {symbol: 1.0 for symbol in symbols},
            "product_leverage_factors": {symbol: 1 for symbol in symbols},
            "allowed_nonzero_assets": symbols,
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
        artifacts={
            "mandate_provenance": mandate,
            "candidate_risk_identity": candidate_identity,
        },
    )
    decision = StrategyDecision(
        positions=tuple(
            PositionTarget(symbol=symbol, target_weight=0.25) for symbol in symbols
        ),
        budgets=(BudgetIntent(name="portfolio", amount=1.0),),
    )

    permissive_assessment = RiskGateAssessment(
        contract_version="qsl.risk_gate_assessment.v1",
        scope="MEMBER",
        evaluated_at=now.isoformat().replace("+00:00", "Z"),
        policy_id="qpk.risk_gate",
        policy_version="v1",
        qpk_source_revision="b371322b948e4298920a7d8613b155245dcd5f8d",
        mandate_id="synthetic_algorithm_equivalence_only",
        mandate_version="test-v1",
        mandate_authority_receipt_sha256="a" * 64,
        mandate_scope="RESEARCH_ONLY",
        candidate_identity_sha256=candidate_identity.candidate_sha256,
        decision_digest_sha256="b" * 64,
        portfolio_snapshot_digest_sha256="c" * 64,
        normalization_origin_digest_sha256=None,
        effective_exposure_cap=2.0,
        observed_effective_exposure=0.0,
        proposed_effective_exposure=1.25,
        outcome="APPROVE",
        reason_codes=(),
    )
    with patch(
        "crypto_strategies.entrypoints._common._qpk_assess_with_evidence",
        return_value=RiskGateResult(
            decision=decision,
            assessment=permissive_assessment,
        ),
    ) as assess:
        result = apply_risk_gate(decision, ctx=ctx)
    assess.assert_called_once()

    assert result.diagnostics["member_risk_assessment"]["outcome"] == "APPROVE"
    assert result.positions == ()
    assert result.budgets == ()
    assert "rejected:overexposed" in result.risk_flags

    for invalid_weight in (None, float("nan"), float("inf"), float("-inf")):
        invalid_decision = StrategyDecision(
            positions=(PositionTarget(symbol=symbols[0], target_weight=invalid_weight),),
            budgets=(BudgetIntent(name="portfolio", amount=1.0),),
        )
        with patch(
            "crypto_strategies.entrypoints._common._qpk_assess_with_evidence",
            return_value=RiskGateResult(
                decision=invalid_decision,
                assessment=permissive_assessment,
            ),
        ) as assess:
            invalid_result = apply_risk_gate(invalid_decision, ctx=ctx)
        assess.assert_called_once()

        assert invalid_result.positions == ()
        assert invalid_result.budgets == ()
        assert "rejected:overexposed" in invalid_result.risk_flags


def test_apply_risk_gate_does_not_coerce_mapping_candidate_identity() -> None:
    now = datetime.now(timezone.utc)
    candidate_identity = _candidate_identity()
    artifacts = _candidate_bound_artifacts(now, candidate_identity=candidate_identity)
    artifacts["candidate_risk_identity"] = {
        "strategy_profile": candidate_identity.strategy_profile,
        "candidate_sha256": candidate_identity.candidate_sha256,
    }
    ctx = StrategyContext(
        as_of=now,
        portfolio=PortfolioSnapshot(
            as_of=now,
            total_equity=1000.0,
            metadata={"observed_effective_exposure": 0.0},
        ),
        artifacts=artifacts,
    )

    result = _apply_risk_gate_once(
        StrategyDecision(
            positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.1),)
        ),
        ctx=ctx,
    )

    assessment = result.diagnostics["member_risk_assessment"]
    assert result.positions == ()
    assert result.budgets == ()
    assert assessment["outcome"] == "REJECT"
    assert "missing_candidate_identity" in assessment["reason_codes"]


def test_apply_risk_gate_wrong_typed_candidate_identity_fails_closed() -> None:
    now = datetime.now(timezone.utc)
    expected_identity = _candidate_identity()
    wrong_identity = _candidate_identity(strategy_profile="crypto_equity_combo")
    artifacts = _candidate_bound_artifacts(now, candidate_identity=expected_identity)
    artifacts["candidate_risk_identity"] = wrong_identity
    ctx = StrategyContext(
        as_of=now,
        portfolio=PortfolioSnapshot(
            as_of=now,
            total_equity=1000.0,
            metadata={"observed_effective_exposure": 0.0},
        ),
        artifacts=artifacts,
    )

    result = _apply_risk_gate_once(
        StrategyDecision(
            positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.1),)
        ),
        ctx=ctx,
    )

    assessment = result.diagnostics["member_risk_assessment"]
    assert result.positions == ()
    assert result.budgets == ()
    assert assessment["outcome"] == "REJECT"
    assert "candidate_strategy_profile_mismatch" in assessment["reason_codes"]


def test_apply_risk_gate_incomplete_mandate_stays_fail_closed() -> None:
    now = datetime.now(timezone.utc)
    candidate_identity = _candidate_identity()
    ctx = StrategyContext(
        as_of=now,
        portfolio=PortfolioSnapshot(
            as_of=now,
            total_equity=1000.0,
            metadata={"observed_effective_exposure": 0.0},
        ),
        artifacts={
            "mandate_provenance": {"mandate_id": "incomplete"},
            "candidate_risk_identity": candidate_identity,
        },
    )

    result = _apply_risk_gate_once(
        StrategyDecision(
            positions=(PositionTarget(symbol="BTCUSDT", target_weight=0.1),)
        ),
        ctx=ctx,
    )

    assessment = result.diagnostics["member_risk_assessment"]
    assert result.positions == ()
    assert result.budgets == ()
    assert assessment["outcome"] == "REJECT"
    assert "invalid_mandate" in assessment["reason_codes"]

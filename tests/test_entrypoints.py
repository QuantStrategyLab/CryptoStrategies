from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from quant_platform_kit import PortfolioSnapshot, Position
from quant_platform_kit.risk import gate as qpk_risk_gate
from quant_platform_kit.risk.contracts import CandidateRiskIdentity
from quant_platform_kit.common.strategy_contracts import StrategyContext
from crypto_strategies import get_strategy_entrypoint


def _synthetic_candidate_identity(strategy_profile: str) -> CandidateRiskIdentity:
    return CandidateRiskIdentity(
        strategy_profile=strategy_profile,
        account_mode="single_strategy_account_v1",
        strategy_revision="1" * 40,
        runner_revision="2" * 40,
        config_sha256="3" * 64,
        input_manifest_sha256="4" * 64,
        authority_receipt_sha256="a" * 64,
    )


def _synthetic_member_mandate(
    *symbols: str,
    candidate_identity: CandidateRiskIdentity,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "mandate_id": "synthetic_algorithm_equivalence_only",
        "mandate_version": "test-v1",
        "authority_receipt_sha256": "a" * 64,
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
        "effective_exposure_cap": 1.0,
        "loss_budget": 1_000_000.0,
        "product_caps": {symbol: 1.0 for symbol in symbols},
        "nominal_caps": {symbol: 1.0 for symbol in symbols},
        "product_leverage_factors": {symbol: 1 for symbol in symbols},
        "allowed_nonzero_assets": list(symbols),
        "source_revision": "b371322b948e4298920a7d8613b155245dcd5f8d",
    }


def _synthetic_risk_artifacts(
    strategy_profile: str,
    *symbols: str,
) -> dict[str, object]:
    candidate_identity = _synthetic_candidate_identity(strategy_profile)
    return {
        "mandate_provenance": _synthetic_member_mandate(
            *symbols,
            candidate_identity=candidate_identity,
        ),
        "candidate_risk_identity": candidate_identity,
    }


def _evaluate_once(entrypoint, ctx: StrategyContext):
    engine = qpk_risk_gate.build_risk_engine()
    with patch.object(engine, "assess", wraps=engine.assess) as assess, patch.object(
        qpk_risk_gate,
        "build_risk_engine",
        return_value=engine,
    ):
        decision = entrypoint.evaluate(ctx)
    assess.assert_called_once()
    return decision


def _fresh_as_of() -> datetime:
    return datetime.now(timezone.utc)


class CryptoStrategyEntrypointTests(unittest.TestCase):
    def test_btc_entrypoint_accepts_translator_without_duplicate_keyword(self) -> None:
        from crypto_strategies.strategies import crypto_btc_dca

        entrypoint = get_strategy_entrypoint("crypto_btc_dca")
        from crypto_strategies.runtime_adapters import get_platform_runtime_adapter
        adapter = get_platform_runtime_adapter("crypto_btc_dca", platform_id="binance")
        self.assertIn("derived_indicators", adapter.available_inputs)
        self.assertIn("derived_indicators", entrypoint.manifest.required_inputs)
        from crypto_strategies.catalog import get_strategy_definition
        self.assertIn("derived_indicators", get_strategy_definition("crypto_btc_dca").required_inputs)
        indicators = {"BTCUSDT": {"close": 60000.0, "sma200": 50000.0,
                                   "high252": 65000.0, "ahr999": 0.7}}
        snapshot = PortfolioSnapshot(as_of=_fresh_as_of(), total_equity=1000.0,
                                     buying_power=1000.0, cash_balance=1000.0,
                                     metadata={"observed_effective_exposure": 0.0,
                                               "btc_zscore_exit": {"route": "normal"}})
        market = {"market_prices": {"BTCUSDT": 60000.0},
                  "derived_indicators": indicators, "portfolio_snapshot": snapshot}
        artifacts = _synthetic_risk_artifacts("crypto_btc_dca", "BTCUSDT")
        from quant_platform_kit.common.runtime_inputs import build_strategy_evaluation_inputs
        from quant_platform_kit.common.strategy_contracts import build_strategy_context_from_available_inputs
        inputs = build_strategy_evaluation_inputs(available_inputs=adapter.available_inputs,
            market_inputs=market, portfolio_snapshot=snapshot)
        mapped = build_strategy_context_from_available_inputs(entrypoint=entrypoint, runtime_adapter=adapter,
            as_of=_fresh_as_of(), available_inputs=inputs)
        self.assertIs(mapped.market_data["derived_indicators"], indicators)
        def translator(key, **_kwargs):
            return key

        decisions = []
        for config in ({}, {"translator": translator}):
            ctx = StrategyContext(as_of=_fresh_as_of(), market_data=market, state={},
                                  runtime_config=config, artifacts=artifacts)
            with patch("crypto_strategies.entrypoints.record_strategy_decision"), patch.object(
                crypto_btc_dca, "compute_signals", wraps=crypto_btc_dca.compute_signals
            ) as compute:
                decision = _evaluate_once(entrypoint, ctx)
            self.assertIs(compute.call_args.kwargs["derived_indicators"], indicators)
            if config:
                self.assertIs(compute.call_args.kwargs["translator"], translator)
            self.assertEqual(decision.diagnostics["member_risk_assessment"]["outcome"], "APPROVE")
            decisions.append(decision)
        self.assertEqual(decisions[0].positions, decisions[1].positions)
        self.assertEqual(decisions[0].diagnostics["btc_target_ratio"],
                         decisions[1].diagnostics["btc_target_ratio"])

    def test_btc_default_plan_reaches_risk_gate_and_respects_window_and_cash(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_btc_dca")
        now = _fresh_as_of().replace(day=25)
        snapshot = PortfolioSnapshot(as_of=now, total_equity=1000.0, buying_power=1000.0,
                                     cash_balance=1000.0, metadata={"observed_effective_exposure": 0.0,
                                         "btc_zscore_exit": {"route": "normal"}})
        artifacts = _synthetic_risk_artifacts("crypto_btc_dca", "BTCUSDT")
        mandate = artifacts["mandate_provenance"]
        mandate["effective_at"] = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        mandate["expires_at"] = (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        market = {"market_prices": {"BTCUSDT": 60000.0}, "portfolio_snapshot": snapshot,
                  "derived_indicators": {"BTCUSDT": {"close": 60000.0, "sma200": 50000.0, "ahr999": .7}}}
        ctx = StrategyContext(as_of=now, market_data=market, state={}, artifacts=artifacts)
        with patch("crypto_strategies.entrypoints.record_strategy_decision"), \
             patch.object(qpk_risk_gate, "_utc_now", return_value=now):
            decision = _evaluate_once(entrypoint, ctx)
        self.assertEqual(decision.diagnostics["member_risk_assessment"]["outcome"], "APPROVE")
        self.assertTrue(decision.diagnostics["in_execution_window"])
        self.assertGreater(decision.diagnostics["planned_investment_usd"], 0.0)
        self.assertLessEqual(decision.diagnostics["planned_investment_usd"], 1000.0)
        self.assertAlmostEqual(decision.positions[0].target_weight,
                               decision.diagnostics["planned_investment_usd"] / 1000.0)
        self.assertEqual(decision.budgets[0].amount, decision.diagnostics["planned_investment_usd"])
        self.assertEqual(decision.diagnostics["btc_value_usd"], 0.0)
        self.assertEqual(decision.diagnostics["target_btc_value_usd"], decision.budgets[0].amount)
        with patch("crypto_strategies.entrypoints.record_strategy_decision"), \
             patch.object(qpk_risk_gate, "_utc_now", return_value=now):
            capped = _evaluate_once(entrypoint, StrategyContext(as_of=now, market_data=market,
                state={}, artifacts=artifacts, runtime_config={"cash_reserve_usd": 950.0,
                                                               "max_investment_usd": 40.0}))
        self.assertEqual(capped.diagnostics["planned_investment_usd"], 40.0)
        self.assertAlmostEqual(capped.positions[0].target_weight, .04)
        no_holdings = PortfolioSnapshot(as_of=now, total_equity=1000.0, buying_power=1000.0,
                                        cash_balance=1000.0, metadata={"observed_effective_exposure": 0.0,
                                            "btc_zscore_exit": {"route": "risk_off"}})
        with patch("crypto_strategies.entrypoints.record_strategy_decision"), \
             patch.object(qpk_risk_gate, "_utc_now", return_value=now):
            risk_off = _evaluate_once(entrypoint, StrategyContext(as_of=now,
                market_data={**market, "portfolio_snapshot": no_holdings}, state={}, artifacts=artifacts))
        self.assertEqual(risk_off.diagnostics["planned_investment_usd"], 0.0)
        self.assertEqual(risk_off.diagnostics["target_btc_value_usd"], 0.0)
        self.assertIn("no_execute", risk_off.risk_flags)
        off_day = now.replace(day=10)
        snapshot = PortfolioSnapshot(as_of=off_day, total_equity=1000.0, buying_power=1000.0,
                                     cash_balance=1000.0, metadata={"observed_effective_exposure": 0.0,
                                         "btc_zscore_exit": {"route": "normal"}})
        mandate["effective_at"] = (off_day - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        mandate["expires_at"] = (off_day + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        with patch("crypto_strategies.entrypoints.record_strategy_decision"), \
             patch.object(qpk_risk_gate, "_utc_now", return_value=off_day):
            off = _evaluate_once(entrypoint, StrategyContext(as_of=off_day,
                market_data={**market, "portfolio_snapshot": snapshot}, state={}, artifacts=artifacts))
        self.assertFalse(off.diagnostics["in_execution_window"])
        self.assertEqual(off.diagnostics["planned_investment_usd"], 0.0)
        self.assertIn("no_execute", off.risk_flags)
        self.assertEqual(off.budgets[0].amount, 0.0)
        held = PortfolioSnapshot(as_of=off_day, total_equity=1000.0, buying_power=900.0,
                                 cash_balance=900.0,
                                 positions=(Position(symbol="BTCUSDT", quantity=100.0 / 60000.0, market_value=100.0),),
                                 metadata={"observed_effective_exposure": .1,
                                           "btc_zscore_exit": {"route": "risk_off"}})
        with patch("crypto_strategies.entrypoints.record_strategy_decision"), \
             patch.object(qpk_risk_gate, "_utc_now", return_value=off_day):
            exit_decision = _evaluate_once(entrypoint, StrategyContext(as_of=off_day,
                market_data={**market, "portfolio_snapshot": held}, state={}, artifacts=artifacts))
        self.assertAlmostEqual(exit_decision.positions[0].target_weight, .025)
        self.assertEqual(exit_decision.diagnostics["btc_value_usd"], 100.0)
        self.assertEqual(exit_decision.diagnostics["target_btc_value_usd"], 25.0)
        self.assertTrue(exit_decision.diagnostics["zscore_exit"]["applied"])
        self.assertEqual(exit_decision.budgets[0].amount, 0.0)
        oversized = PortfolioSnapshot(as_of=now, total_equity=1000.0, buying_power=100.0,
                                      cash_balance=100.0,
                                      positions=(Position(symbol="BTCUSDT", quantity=.015, market_value=900.0),),
                                      metadata={"observed_effective_exposure": .9,
                                                "btc_zscore_exit": {"route": "normal"}})
        mandate["effective_at"] = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        mandate["expires_at"] = (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        with patch("crypto_strategies.entrypoints.record_strategy_decision"), \
             patch.object(qpk_risk_gate, "_utc_now", return_value=now):
            held_decision = _evaluate_once(entrypoint, StrategyContext(as_of=now,
                market_data={**market, "portfolio_snapshot": oversized}, state={}, artifacts=artifacts))
        self.assertAlmostEqual(held_decision.diagnostics["btc_target_ratio"], .9)
        self.assertEqual(held_decision.diagnostics["planned_investment_usd"], 0.0)
        self.assertIn("no_execute", held_decision.risk_flags)
        self.assertIn("rejected:strategy_concentration", held_decision.risk_flags)
        self.assertEqual(held_decision.positions, ())
        self.assertEqual(held_decision.budgets, ())

    def test_btc_entrypoint_rejects_invalid_or_unsupported_plan_configuration(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_btc_dca")
        now = _fresh_as_of()
        snapshot = PortfolioSnapshot(as_of=now, total_equity=1000.0, buying_power=1000.0,
                                     cash_balance=1000.0, metadata={"observed_effective_exposure": 0.0,
                                         "btc_zscore_exit": {"route": "normal"}})
        market = {"market_prices": {"BTCUSDT": 60000.0}, "portfolio_snapshot": snapshot,
                  "derived_indicators": {"BTCUSDT": {"close": 60000.0, "sma200": 50000.0, "ahr999": .7}}}
        for config in ({"target_ratio_max": .7}, {"cash_reserve_usd": float("nan")},
                       {"cadence": "daily"}, {"mystery_cash_cap": 100},
                       {"smart_multiplier_enabled": "unexpected"}, {"mild_drawdown_threshold": float("nan")},
                       {"signal_symbol": "ETHUSDT"}, {"zscore_exit_risk_off_exposure": 2.0},
                       {"zscore_exit_risk_reduced_exposure": 1.1}, {"monthly_day": 0},
                       {"monthly_day": 999}, {"quarterly_months": "1,garbage"},
                       {"zscore_exit_allow_outside_execution_window": "no"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                entrypoint.evaluate(StrategyContext(as_of=now, market_data=market, state={},
                                                    runtime_config=config))
        # Ordinary DCA is available only when both optional features are explicitly disabled.
        with patch("crypto_strategies.entrypoints.record_strategy_decision"):
            ordinary = _evaluate_once(entrypoint, StrategyContext(as_of=now,
                market_data={**market, "derived_indicators": {}}, state={},
                runtime_config={"smart_multiplier_enabled": False, "zscore_exit_enabled": False},
                artifacts=_synthetic_risk_artifacts("crypto_btc_dca", "BTCUSDT")))
        self.assertFalse(ordinary.diagnostics["smart_multiplier_enabled"])
        with self.assertRaisesRegex(ValueError, "smart cycle input"):
            entrypoint.evaluate(StrategyContext(as_of=now,
                market_data={**market, "derived_indicators": {}}, state={}))
        for invalid in ({"close": 60000.0, "sma200": 50000.0},
                        {"close": -1.0, "sma200": 50000.0, "ahr999": .7},
                        {"close": 60000.0, "sma200": 50000.0, "ahr999": True},
                        {" close ": True, "sma200": 50000.0, "ahr999": .7},
                        {"close": np.bool_(True), "sma200": 50000.0, "ahr999": .7}):
            with self.subTest(indicator=invalid), self.assertRaises(ValueError):
                entrypoint.evaluate(StrategyContext(as_of=now,
                    market_data={**market, "derived_indicators": {"BTCUSDT": invalid}}, state={}))
        # The drawdown smart branch requires real drawdown/high and RSI evidence.
        for indicator in ({"close": 60000.0, "sma200": 50000.0},
                          {"close": 60000.0, "sma200": 50000.0, "high252": 65000.0},
                          {"close": 60000.0, "sma200": 50000.0, "rsi14": 40.0},
                          *({"close": 60000.0, "sma200": 50000.0, "high252": 65000.0,
                             "rsi14": 40.0, "drawdown_252d": value} for value in (True, -.1, 1.1))):
            with self.subTest(drawdown_indicator=indicator), self.assertRaises(ValueError):
                entrypoint.evaluate(StrategyContext(as_of=now,
                    market_data={**market, "derived_indicators": {"BTCUSDT": indicator}},
                    runtime_config={"cycle_indicator_enabled": False}, state={}))
        for value, recorded, buying_power, cash in (
            (float("nan"), None, 800.0, 800.0), (-10.0, None, 800.0, 800.0),
            (400.0, 0.0, 600.0, 600.0), (0.0, 0.0, 800.0, 100.0),
        ):
            position = SimpleNamespace(symbol="BTCUSDT", quantity=value / 60000.0, market_value=value)
            meta = {"observed_effective_exposure": 0.0, "btc_zscore_exit": {"route": "normal"}}
            if recorded is not None:
                meta["dca_value"] = recorded
            bad_snapshot = SimpleNamespace(total_equity=1000.0, buying_power=buying_power,
                                           cash_balance=cash, positions=(position,), metadata=meta)
            with self.subTest(value=value, recorded=recorded, cash=cash), self.assertRaises(ValueError):
                entrypoint.evaluate(StrategyContext(as_of=now,
                    market_data={**market, "portfolio_snapshot": bad_snapshot}, state={}))
        for route in ({"route": "risk_off", "target_allocations": {"BTCUSDT": 1.0}},
                      {"route": "risk_reduced", "target_allocations": {"BTCUSDT": float("nan")}},
                      {"route": "risk_off", "target_allocations": {"BTCUSDT": True}}):
            invalid_exit = PortfolioSnapshot(as_of=now, total_equity=1000.0, buying_power=1000.0,
                cash_balance=1000.0, metadata={"observed_effective_exposure": 0.0, "btc_zscore_exit": route})
            with self.subTest(exit_route=route), self.assertRaises(ValueError):
                entrypoint.evaluate(StrategyContext(as_of=now,
                    market_data={**market, "portfolio_snapshot": invalid_exit}, state={}))
        no_zscore = PortfolioSnapshot(as_of=now, total_equity=1000.0, buying_power=1000.0,
                                     cash_balance=1000.0, metadata={"observed_effective_exposure": 0.0})
        with self.assertRaisesRegex(ValueError, "z-score exit input"):
            entrypoint.evaluate(StrategyContext(as_of=now,
                market_data={**market, "portfolio_snapshot": no_zscore}, state={}))

    def test_trend_entrypoint_uses_authoritative_pool_and_keeps_held_positions_visible(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_trend_rotation")
        def row(close, roc):
            return {"close": close, "sma20": close * .95, "sma60": close * .90,
                    "sma200": close * .80, "roc20": roc, "roc60": roc,
                    "roc120": roc, "vol20": 2.0, "avg_quote_vol_30": 1e9,
                    "avg_quote_vol_90": 1e9, "avg_quote_vol_180": 1e9,
                    "trend_persist_90": .8, "age_days": 500}
        portfolio = PortfolioSnapshot(
            as_of=_fresh_as_of(), total_equity=1000.0, buying_power=900.0, cash_balance=900.0,
            positions=(Position(symbol="OLDUSDT", quantity=1.0, market_value=100.0),),
            metadata={"observed_effective_exposure": 0.1},
        )
        market_data = {
            "market_prices": {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0,
                              "SOLUSDT": 180.0, "OLDUSDT": 100.0},
            "derived_indicators": {"BTCUSDT": row(60000.0, .01),
                                   "ETHUSDT": row(3000.0, .3),
                                   "SOLUSDT": row(180.0, .8)},
            "benchmark_snapshot": {"regime_on": True}, "portfolio_snapshot": portfolio,
            "universe_snapshot": ("ETHUSDT", "BTCUSDT"),
        }
        artifacts = _synthetic_risk_artifacts("crypto_trend_rotation", "ETHUSDT", "OLDUSDT")
        def context(data):
            return StrategyContext(as_of=_fresh_as_of(), market_data=data, state={}, artifacts=artifacts)
        with patch("crypto_strategies.entrypoints.record_strategy_decision"):
            decision = _evaluate_once(entrypoint, context(market_data))
        self.assertEqual(decision.diagnostics["member_risk_assessment"]["outcome"], "APPROVE")
        self.assertEqual({item.symbol for item in decision.positions}, {"ETHUSDT"})
        self.assertEqual(set(decision.diagnostics["managed_symbols"]), {"ETHUSDT", "OLDUSDT"})
        for missing_universe in (None, (), ("BTCUSDT",), ("MISSINGUSDT",)):
            bad = dict(market_data)
            if missing_universe is None:
                bad.pop("universe_snapshot")
            else:
                bad["universe_snapshot"] = missing_universe
            with self.subTest(universe=missing_universe), self.assertRaises(ValueError):
                entrypoint.evaluate(context(bad))

    def test_trend_entrypoint_forwards_volatility_sizing_config(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_trend_rotation")

        def row(close, roc):
            return {"close": close, "sma20": close * .95, "sma60": close * .90,
                    "sma200": close * .80, "roc20": roc, "roc60": roc,
                    "roc120": roc, "vol20": 2.0, "avg_quote_vol_30": 1e9,
                    "avg_quote_vol_90": 1e9, "avg_quote_vol_180": 1e9,
                    "trend_persist_90": .8, "age_days": 500}

        now = _fresh_as_of()
        portfolio = PortfolioSnapshot(as_of=now, total_equity=1000.0, buying_power=1000.0,
                                      cash_balance=1000.0, metadata={"observed_effective_exposure": 0.0})
        market = {"market_prices": {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0},
                  "derived_indicators": {"BTCUSDT": row(60000.0, .01), "ETHUSDT": row(3000.0, .3)},
                  "portfolio_snapshot": portfolio, "universe_snapshot": ("ETHUSDT",)}
        artifacts = _synthetic_risk_artifacts("crypto_trend_rotation", "ETHUSDT")

        def evaluate(config):
            ctx = StrategyContext(as_of=now, market_data=market, state={}, runtime_config=config, artifacts=artifacts)
            return _evaluate_once(entrypoint, ctx)

        with patch("crypto_strategies.entrypoints.record_strategy_decision"), \
             patch.object(qpk_risk_gate, "_utc_now", return_value=now):
            for config, expected in (({}, .20), ({"target_vol": .20}, .10), ({"max_leverage": .05}, .05)):
                with self.subTest(config=config):
                    decision = evaluate(config)
                    self.assertEqual(decision.diagnostics["member_risk_assessment"]["outcome"], "APPROVE")
                    self.assertEqual(len(decision.positions), 1)
                    self.assertAlmostEqual(decision.positions[0].target_weight, expected)
            for config in ({"target_vol": -1}, {"max_leverage": -1}):
                with self.subTest(config=config), self.assertRaises(ValueError):
                    evaluate(config)

    def test_crypto_live_pool_rotation_entrypoint_resolves_pool_from_upstream_artifact(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_live_pool_rotation")
        upstream_pool = ["SOLUSDT", "ETHUSDT"]
        calls: dict[str, object] = {}

        def compute_allocation_budgets(total_equity, cash_usdt, trend_value, dca_value):
            return {
                "btc_target_ratio": 0.4,
                "trend_target_ratio": 0.6,
                "trend_usdt_pool": 300.0,
                "dca_usdt_pool": 200.0,
                "trend_layer_equity": 700.0,
            }

        def select_rotation_weights(indicators_map, prices, btc_snapshot, candidate_pool, top_n, *, weight_mode):
            calls["candidate_pool"] = tuple(candidate_pool)
            return {
                "SOLUSDT": {
                    "weight": 1.0,
                    "relative_score": 1.0,
                    "abs_momentum": 0.25,
                }
            }

        def resolve_authoritative_rotation_pool(state, *, trend_universe_symbols, trend_pool_size, allow_refresh=True, now_utc=None):
            calls["trend_universe_symbols"] = tuple(trend_universe_symbols)
            state["rotation_pool_source_version"] = state.get("trend_pool_version", "")
            state["rotation_pool_source_as_of_date"] = state.get("trend_pool_as_of_date", "")
            state["rotation_pool_last_month"] = "2026-03"
            state["rotation_pool_symbols"] = list(upstream_pool)
            return list(upstream_pool)

        def plan_trend_buys(
            state,
            runtime_trend_universe,
            selected_candidates,
            trend_indicators,
            prices,
            available_trend_buy_budget,
            allow_new_trend_entries,
            *,
            get_symbol_trade_state_fn,
            allocate_trend_buy_budget_fn,
        ):
            calls["runtime_trend_universe"] = tuple(runtime_trend_universe)
            return ["SOLUSDT"], {"SOLUSDT": 100.0}

        fake_core = SimpleNamespace(
            compute_allocation_budgets=compute_allocation_budgets,
            select_rotation_weights=select_rotation_weights,
            get_dynamic_btc_base_order=lambda total_equity: 15.0,
            allocate_trend_buy_budget=lambda *args, **kwargs: {},
        )
        fake_rotation = SimpleNamespace(
            resolve_authoritative_rotation_pool=resolve_authoritative_rotation_pool,
            get_trend_sell_reason=lambda *args, **kwargs: "",
            plan_trend_buys=plan_trend_buys,
        )

        with patch(
            "crypto_strategies.entrypoints._load_legacy_modules",
            return_value=(fake_core, fake_rotation),
        ):
            decision = _evaluate_once(
                entrypoint,
                StrategyContext(
                    as_of="2026-04-06",
                    market_data={
                        "market_prices": {"SOLUSDT": 180.0, "ETHUSDT": 3000.0},
                        "derived_indicators": {
                            "SOLUSDT": {"sma20": 170.0, "sma60": 160.0, "sma200": 120.0},
                            "ETHUSDT": {"sma20": 2900.0, "sma60": 2700.0, "sma200": 2300.0},
                        },
                        "benchmark_snapshot": {"regime_on": True},
                        "portfolio_snapshot": PortfolioSnapshot(
                            as_of="2026-04-06",
                            total_equity=1000.0,
                            buying_power=500.0,
                            cash_balance=500.0,
                        ),
                        "universe_snapshot": upstream_pool,
                    },
                    state={
                        "trend_pool_version": "2026-03-15-core_major",
                        "trend_pool_as_of_date": "2026-03-15",
                    },
                )
            )

        self.assertEqual(calls["trend_universe_symbols"], tuple(upstream_pool))
        self.assertEqual(calls["candidate_pool"], tuple(upstream_pool))
        self.assertEqual(calls["runtime_trend_universe"], tuple(upstream_pool))
        self.assertEqual(decision.diagnostics["trend_pool"], tuple(upstream_pool))
        self.assertEqual(decision.diagnostics["ranking_preview"], tuple(upstream_pool))
        self.assertEqual(decision.diagnostics["rotation_pool_source_version"], "2026-03-15-core_major")

    def test_crypto_live_pool_rotation_entrypoint_uses_authoritative_upstream_pool(self) -> None:
        try:
            from crypto_strategies.strategies.crypto_live_pool_rotation import core as legacy_core
            from crypto_strategies.strategies.crypto_live_pool_rotation import rotation as legacy_rotation
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed")
            raise
        entrypoint = get_strategy_entrypoint("crypto_live_pool_rotation")
        prices = {
            "ETHUSDT": 3000.0,
            "SOLUSDT": 180.0,
            "BNBUSDT": 700.0,
        }
        trend_indicators = {
            "ETHUSDT": {
                "close": 3000.0,
                "sma20": 2800.0,
                "sma60": 2600.0,
                "sma200": 2200.0,
                "roc20": 0.20,
                "roc60": 0.35,
                "roc120": 0.60,
                "vol20": 0.25,
                "avg_quote_vol_30": 60000000.0,
                "avg_quote_vol_90": 50000000.0,
                "avg_quote_vol_180": 45000000.0,
                "trend_persist_90": 0.80,
                "age_days": 500,
                "atr14": 120.0,
            },
            "SOLUSDT": {
                "close": 180.0,
                "sma20": 170.0,
                "sma60": 160.0,
                "sma200": 120.0,
                "roc20": 0.28,
                "roc60": 0.45,
                "roc120": 0.75,
                "vol20": 0.30,
                "avg_quote_vol_30": 42000000.0,
                "avg_quote_vol_90": 39000000.0,
                "avg_quote_vol_180": 36000000.0,
                "trend_persist_90": 0.76,
                "age_days": 450,
                "atr14": 8.0,
            },
            "BNBUSDT": {
                "close": 700.0,
                "sma20": 690.0,
                "sma60": 650.0,
                "sma200": 540.0,
                "roc20": 0.10,
                "roc60": 0.22,
                "roc120": 0.40,
                "vol20": 0.18,
                "avg_quote_vol_30": 30000000.0,
                "avg_quote_vol_90": 28000000.0,
                "avg_quote_vol_180": 26000000.0,
                "trend_persist_90": 0.72,
                "age_days": 600,
                "atr14": 20.0,
            },
        }
        btc_snapshot = {
            "regime_on": True,
            "btc_roc20": 0.08,
            "btc_roc60": 0.16,
            "btc_roc120": 0.30,
        }
        account_metrics = {
            "total_equity": 100000.0,
            "cash_usdt": 25000.0,
            "trend_value": 15000.0,
            "dca_value": 12000.0,
        }
        state = {
            "trend_pool_version": "2026-03-15-core_major",
            "trend_pool_as_of_date": "2026-03-15",
            "ETHUSDT": {"is_holding": True, "entry_price": 2500.0, "highest_price": 3000.0},
            "SOLUSDT": {"is_holding": True, "entry_price": 150.0, "highest_price": 180.0},
        }
        upstream_pool = ["BNBUSDT", "ETHUSDT", "SOLUSDT"]
        expected_budgets = legacy_core.compute_allocation_budgets(
            account_metrics["total_equity"],
            account_metrics["cash_usdt"],
            account_metrics["trend_value"],
            account_metrics["dca_value"],
        )
        expected_state = dict(state)
        expected_pool = legacy_rotation.resolve_authoritative_rotation_pool(
            expected_state,
            trend_universe_symbols=upstream_pool,
            trend_pool_size=entrypoint.manifest.default_config["trend_pool_size"],
        )
        expected_candidates = legacy_core.select_rotation_weights(
            trend_indicators,
            prices,
            btc_snapshot,
            expected_pool,
            entrypoint.manifest.default_config["rotation_top_n"],
            weight_mode=entrypoint.manifest.default_config["weight_mode"],
        )
        expected_eligible_buy_symbols, expected_planned_trend_buys = legacy_rotation.plan_trend_buys(
            dict(expected_state),
            runtime_trend_universe={symbol: {"base_asset": symbol[:-4]} for symbol in upstream_pool},
            selected_candidates=expected_candidates,
            trend_indicators=trend_indicators,
            prices=prices,
            available_trend_buy_budget=expected_budgets["trend_usdt_pool"],
            allow_new_trend_entries=True,
            get_symbol_trade_state_fn=lambda current_state, symbol: current_state.get(
                symbol,
                {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0},
            ),
            allocate_trend_buy_budget_fn=legacy_core.allocate_trend_buy_budget,
        )

        decision = _evaluate_once(

            entrypoint,
            StrategyContext(
                as_of="2026-04-06",
                market_data={
                    "market_prices": prices,
                    "derived_indicators": trend_indicators,
                    "benchmark_snapshot": btc_snapshot,
                    "portfolio_snapshot": PortfolioSnapshot(
                        as_of=_fresh_as_of(),
                        total_equity=account_metrics["total_equity"],
                        buying_power=account_metrics["cash_usdt"],
                        cash_balance=account_metrics["cash_usdt"],
                        positions=(
                            Position(symbol="BTCUSDT", quantity=0.2, market_value=account_metrics["dca_value"]),
                            Position(symbol="ETHUSDT", quantity=2.0, market_value=9000.0),
                            Position(symbol="SOLUSDT", quantity=20.0, market_value=6000.0),
                        ),
                        metadata={
                            "account_metrics": account_metrics,
                            "cash_available_for_trading": account_metrics["cash_usdt"],
                            "observed_effective_exposure": 0.27,
                        },
                    ),
                    "universe_snapshot": upstream_pool,
                },
                state=state,
                artifacts={
                    "trend_pool_contract": {"source": "explicit_artifact"},
                    **_synthetic_risk_artifacts(
                        "crypto_live_pool_rotation",
                        "BTCUSDT",
                        "BNBUSDT",
                        "ETHUSDT",
                        "SOLUSDT",
                    ),
                },
            )
        )

        self.assertEqual(
            decision.diagnostics["member_risk_assessment"]["outcome"],
            "APPROVE",
            decision.diagnostics["member_risk_assessment"],
        )
        budget_map = {budget.name: budget.amount for budget in decision.budgets}
        self.assertAlmostEqual(budget_map["btc_core_dca_pool"], expected_budgets["dca_usdt_pool"])
        self.assertAlmostEqual(budget_map["trend_rotation_pool"], expected_budgets["trend_usdt_pool"])
        position_map = {position.symbol: position.target_weight for position in decision.positions}
        self.assertAlmostEqual(position_map["BTCUSDT"], expected_budgets["btc_target_ratio"])
        for symbol, payload in expected_candidates.items():
            self.assertAlmostEqual(
                position_map[symbol],
                expected_budgets["trend_target_ratio"] * payload["weight"],
            )
        self.assertEqual(decision.diagnostics["trend_pool"], tuple(expected_pool))
        self.assertEqual(
            decision.diagnostics["rotation_pool_source_version"],
            expected_state["rotation_pool_source_version"],
        )
        self.assertEqual(
            tuple(decision.diagnostics["ranking_preview"]),
            tuple(expected_pool[: entrypoint.manifest.default_config["trend_pool_size"]]),
        )
        self.assertEqual(decision.diagnostics["artifact_contract"]["source"], "explicit_artifact")
        self.assertEqual(tuple(decision.diagnostics["eligible_buy_symbols"]), tuple(expected_eligible_buy_symbols))
        self.assertEqual(decision.diagnostics["planned_trend_buys"], expected_planned_trend_buys)
        self.assertEqual(decision.diagnostics["sell_reasons"], {})
        self.assertEqual(decision.diagnostics["strategy_stop_evaluation"]["outcome"], "CLEAR")
        self.assertEqual(
            decision.diagnostics["strategy_stop_evaluation"]["decision_digest_sha256"],
            decision.diagnostics["member_risk_assessment"]["decision_digest_sha256"],
        )
        self.assertAlmostEqual(
            decision.diagnostics["btc_base_order_usdt"],
            legacy_core.get_dynamic_btc_base_order(account_metrics["total_equity"]),
        )

    def test_crypto_equity_combo_entrypoint_exposes_binance_execution_contract(self) -> None:
        try:
            entrypoint = get_strategy_entrypoint("crypto_equity_combo")
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed")
            raise

        decision = _evaluate_once(

            entrypoint,
            StrategyContext(
                as_of="2026-04-06",
                market_data={
                    "market_prices": {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0, "SOLUSDT": 180.0},
                    "derived_indicators": {
                        "BTCUSDT": {
                            "close": 60000.0,
                            "sma200": 50000.0,
                            "roc20": 0.08,
                            "roc60": 0.16,
                            "roc120": 0.30,
                            "regime_on": True,
                        },
                        "ETHUSDT": {
                            "close": 3000.0,
                            "sma20": 2800.0,
                            "sma60": 2600.0,
                            "sma200": 2200.0,
                            "roc20": 0.20,
                            "roc60": 0.35,
                            "roc120": 0.60,
                            "vol20": 0.25,
                        },
                        "SOLUSDT": {
                            "close": 180.0,
                            "sma20": 170.0,
                            "sma60": 160.0,
                            "sma200": 120.0,
                            "roc20": 0.28,
                            "roc60": 0.45,
                            "roc120": 0.75,
                            "vol20": 0.30,
                        },
                    },
                    "benchmark_snapshot": {"regime_on": True},
                    "portfolio_snapshot": PortfolioSnapshot(
                        as_of=_fresh_as_of(),
                        total_equity=1000.0,
                        buying_power=1000.0,
                        cash_balance=1000.0,
                        metadata={
                            "account_metrics": {
                                "total_equity": 1000.0,
                                "cash_usdt": 1000.0,
                                "trend_value": 0.0,
                                "dca_value": 0.0,
                            },
                            "observed_effective_exposure": 0.0,
                        },
                    ),
                    "universe_snapshot": ("ETHUSDT", "SOLUSDT"),
                },
                state={},
                artifacts={
                    **_synthetic_risk_artifacts(
                        "crypto_equity_combo",
                        "BTCUSDT",
                        "ETHUSDT",
                        "SOLUSDT",
                    )
                },
            )
        )

        self.assertEqual(
            decision.diagnostics["member_risk_assessment"]["outcome"],
            "APPROVE",
            decision.diagnostics["member_risk_assessment"],
        )
        self.assertEqual(decision.positions, ())
        self.assertEqual(decision.budgets, ())
        self.assertIn("rejected:strategy_concentration", decision.risk_flags)
        self.assertGreater(decision.diagnostics["btc_base_order_usdt"], 0.0)
        self.assertGreater(decision.diagnostics["btc_target_ratio"], 0.0)
        self.assertGreater(decision.diagnostics["trend_target_ratio"], 0.0)
        self.assertEqual(set(decision.diagnostics["rotation_candidates"]), {"ETHUSDT", "SOLUSDT"})
        self.assertEqual(set(decision.diagnostics["eligible_buy_symbols"]), {"ETHUSDT", "SOLUSDT"})
        self.assertEqual(set(decision.diagnostics["planned_trend_buys"]), {"ETHUSDT", "SOLUSDT"})

    def test_crypto_live_pool_rotation_entrypoint_sets_regime_off_flag_when_btc_regime_is_off(self) -> None:
        try:
            entrypoint = get_strategy_entrypoint("crypto_live_pool_rotation")
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed")
            raise
        try:
            decision = _evaluate_once(
                entrypoint,
                StrategyContext(
                    as_of="2026-04-06",
                    market_data={
                        "market_prices": {},
                        "derived_indicators": {},
                        "benchmark_snapshot": {"regime_on": False, "btc_roc20": 0.0, "btc_roc60": 0.0, "btc_roc120": 0.0},
                        "portfolio_snapshot": PortfolioSnapshot(
                            as_of="2026-04-06",
                            total_equity=1000.0,
                            buying_power=1000.0,
                            cash_balance=1000.0,
                            metadata={"account_metrics": {"total_equity": 1000.0, "cash_usdt": 1000.0, "trend_value": 0.0, "dca_value": 0.0}},
                        ),
                        "universe_snapshot": [],
                    },
                    state={},
                )
            )
        except ModuleNotFoundError as exc:
            if exc.name == "pandas":
                self.skipTest("pandas is not installed")
            raise

        self.assertIn("regime_off", decision.risk_flags)
        self.assertIn("no_trend_candidates", decision.risk_flags)
        self.assertEqual(decision.positions, ())
        self.assertEqual(decision.budgets, ())
        self.assertEqual(decision.diagnostics["member_risk_assessment"]["outcome"], "REJECT")

    def test_crypto_live_pool_rotation_missing_held_stop_input_is_no_order(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_live_pool_rotation")
        buy_plan_calls: list[object] = []

        def plan_trend_buys(*args, **kwargs):
            buy_plan_calls.append((args, kwargs))
            return [], {}

        fake_core = SimpleNamespace(
            compute_allocation_budgets=lambda *_args: {
                "btc_target_ratio": 0.1,
                "trend_target_ratio": 0.1,
                "trend_usdt_pool": 10.0,
                "dca_usdt_pool": 10.0,
            },
            select_rotation_weights=lambda *_args, **_kwargs: {
                "ETHUSDT": {"weight": 1.0, "relative_score": 1.0, "abs_momentum": 0.1}
            },
            get_dynamic_btc_base_order=lambda _total_equity: 1.0,
            allocate_trend_buy_budget=lambda *_args, **_kwargs: {},
        )
        fake_rotation = SimpleNamespace(
            resolve_authoritative_rotation_pool=lambda *_args, **_kwargs: ["ETHUSDT"],
            plan_trend_buys=plan_trend_buys,
        )
        def evaluate(prices, indicators):
            now = _fresh_as_of()
            return _evaluate_once(
                entrypoint,
                StrategyContext(
                    as_of=now,
                    market_data={
                        "market_prices": prices,
                        "derived_indicators": {"ETHUSDT": indicators},
                        "benchmark_snapshot": {"regime_on": True},
                        "portfolio_snapshot": PortfolioSnapshot(
                            as_of=now,
                            total_equity=1000.0,
                            positions=(
                                Position(symbol="ETHUSDT", quantity=1.0, market_value=3000.0),
                            ),
                            metadata={
                                "account_metrics": {
                                    "total_equity": 1000.0,
                                    "cash_usdt": 1000.0,
                                    "trend_value": 0.0,
                                    "dca_value": 0.0,
                                },
                                "observed_effective_exposure": 0.0,
                            },
                        ),
                        "universe_snapshot": (),
                    },
                    state={
                        "ETHUSDT": {
                            "is_holding": True,
                            "entry_price": 2800.0,
                            "highest_price": 3200.0,
                        }
                    },
                    artifacts={
                        **_synthetic_risk_artifacts(
                            "crypto_live_pool_rotation",
                            "BTCUSDT",
                            "ETHUSDT",
                        )
                    },
                )
            )

        with patch(
            "crypto_strategies.entrypoints._load_legacy_modules",
            return_value=(fake_core, fake_rotation),
        ):
            missing = evaluate({}, {"sma60": 2600.0})
            triggered = evaluate(
                {"ETHUSDT": 2000.0},
                {"atr14": 100.0, "sma60": 2600.0},
            )
            buy_plan_calls_after_valid_stop = len(buy_plan_calls)
            invalid_atr = tuple(
                evaluate(
                    {"ETHUSDT": 3200.0},
                    {"atr14": atr14, "sma60": 2600.0},
                )
                for atr14 in (0.0, -1.0)
            )

        for decision in (missing, triggered, *invalid_atr):
            with self.subTest(decision=decision):
                self.assertEqual(decision.positions, ())
                self.assertEqual(decision.budgets, ())
                self.assertEqual(
                    decision.diagnostics["strategy_stop_evaluation"]["outcome"],
                    "TRIGGERED",
                )
                self.assertEqual(
                    decision.diagnostics["strategy_stop_evaluation"]["action_result"],
                    "BLOCKED",
                )
        for decision in (missing, *invalid_atr):
            self.assertIn("rejected:strategy_stop_input", decision.risk_flags)
        self.assertEqual(len(buy_plan_calls), buy_plan_calls_after_valid_stop)

    def test_crypto_live_pool_rotation_blocked_stop_skips_buy_planning(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_live_pool_rotation")
        buy_plan_calls: list[object] = []

        def plan_trend_buys(*args, **kwargs):
            buy_plan_calls.append((args, kwargs))
            raise AssertionError("buy planning must not run after blocked stop input")

        fake_core = SimpleNamespace(
            compute_allocation_budgets=lambda *_args: {
                "btc_target_ratio": 0.1,
                "trend_target_ratio": 0.1,
                "trend_usdt_pool": 10.0,
                "dca_usdt_pool": 10.0,
            },
            select_rotation_weights=lambda *_args, **_kwargs: {
                "ETHUSDT": {"weight": 1.0, "relative_score": 1.0, "abs_momentum": 0.1}
            },
            get_dynamic_btc_base_order=lambda _total_equity: 1.0,
            allocate_trend_buy_budget=lambda *_args, **_kwargs: {},
        )
        fake_rotation = SimpleNamespace(
            resolve_authoritative_rotation_pool=lambda *_args, **_kwargs: ["ETHUSDT"],
            plan_trend_buys=plan_trend_buys,
        )
        now = _fresh_as_of()

        with patch(
            "crypto_strategies.entrypoints._load_legacy_modules",
            return_value=(fake_core, fake_rotation),
        ):
            decision = _evaluate_once(
                entrypoint,
                StrategyContext(
                    as_of=now,
                    market_data={
                        "market_prices": {},
                        "derived_indicators": {
                            "ETHUSDT": {"atr14": 100.0, "sma60": 2600.0}
                        },
                        "benchmark_snapshot": {"regime_on": True},
                        "portfolio_snapshot": PortfolioSnapshot(
                            as_of=now,
                            total_equity=1000.0,
                            positions=(
                                Position(symbol="ETHUSDT", quantity=1.0, market_value=3000.0),
                            ),
                            metadata={
                                "account_metrics": {
                                    "total_equity": 1000.0,
                                    "cash_usdt": 1000.0,
                                    "trend_value": 0.0,
                                    "dca_value": 0.0,
                                },
                                "observed_effective_exposure": 0.0,
                            },
                        ),
                        "universe_snapshot": ("ETHUSDT",),
                    },
                    state={},
                    artifacts={
                        **_synthetic_risk_artifacts(
                            "crypto_live_pool_rotation",
                            "BTCUSDT",
                            "ETHUSDT",
                        )
                    },
                )
            )

        self.assertEqual(buy_plan_calls, [])
        self.assertEqual(decision.positions, ())
        self.assertEqual(decision.budgets, ())
        self.assertIn("rejected:strategy_stop_input", decision.risk_flags)
        self.assertEqual(decision.diagnostics["eligible_buy_symbols"], ())
        self.assertEqual(decision.diagnostics["planned_trend_buys"], {})
        self.assertEqual(
            decision.diagnostics["strategy_stop_evaluation"]["outcome"],
            "TRIGGERED",
        )
        self.assertEqual(
            decision.diagnostics["strategy_stop_evaluation"]["action_result"],
            "BLOCKED",
        )

    def test_crypto_live_pool_rotation_discovers_nested_custom_held_state(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_live_pool_rotation")
        state_get_calls: list[str] = []

        def get_symbol_trade_state(state, symbol):
            state_get_calls.append(symbol)
            symbol_state = state.get("trade_states", {}).get(symbol)
            if not isinstance(symbol_state, dict):
                return {"is_holding": False, "entry_price": 0.0, "highest_price": 0.0}
            return dict(symbol_state)

        def set_symbol_trade_state(state, symbol, symbol_state):
            state.setdefault("trade_states", {})[symbol] = dict(symbol_state)

        fake_core = SimpleNamespace(
            compute_allocation_budgets=lambda *_args: {
                "btc_target_ratio": 0.1,
                "trend_target_ratio": 0.1,
                "trend_usdt_pool": 10.0,
                "dca_usdt_pool": 10.0,
            },
            select_rotation_weights=lambda *_args, **_kwargs: {
                "ETHUSDT": {"weight": 1.0, "relative_score": 1.0, "abs_momentum": 0.1}
            },
            get_dynamic_btc_base_order=lambda _total_equity: 1.0,
            allocate_trend_buy_budget=lambda *_args, **_kwargs: {},
        )
        fake_rotation = SimpleNamespace(
            resolve_authoritative_rotation_pool=lambda *_args, **_kwargs: ["ETHUSDT"],
            plan_trend_buys=lambda *_args, **_kwargs: ([], {}),
        )
        now = _fresh_as_of()

        with patch(
            "crypto_strategies.entrypoints._load_legacy_modules",
            return_value=(fake_core, fake_rotation),
        ):
            decision = _evaluate_once(
                entrypoint,
                StrategyContext(
                    as_of=now,
                    market_data={
                        "market_prices": {"ETHUSDT": 3000.0},
                        "derived_indicators": {
                            "ETHUSDT": {"atr14": 100.0, "sma60": 2600.0}
                        },
                        "benchmark_snapshot": {"regime_on": True},
                        "portfolio_snapshot": PortfolioSnapshot(
                            as_of=now,
                            total_equity=1000.0,
                            metadata={
                                "account_metrics": {
                                    "total_equity": 1000.0,
                                    "cash_usdt": 1000.0,
                                    "trend_value": 0.0,
                                    "dca_value": 0.0,
                                },
                                "observed_effective_exposure": 0.0,
                            },
                        ),
                        "universe_snapshot": ("ETHUSDT",),
                    },
                    state={
                        "trade_states": {
                            "ETHUSDT": {
                                "is_holding": True,
                                "entry_price": 2800.0,
                            }
                        }
                    },
                    runtime_config={
                        "get_symbol_trade_state_fn": get_symbol_trade_state,
                        "set_symbol_trade_state_fn": set_symbol_trade_state,
                    },
                    artifacts={
                        **_synthetic_risk_artifacts(
                            "crypto_live_pool_rotation",
                            "BTCUSDT",
                            "ETHUSDT",
                        )
                    },
                )
            )

        self.assertGreaterEqual(state_get_calls.count("ETHUSDT"), 2)
        self.assertEqual(decision.positions, ())
        self.assertEqual(decision.budgets, ())
        self.assertIn("rejected:strategy_stop_input", decision.risk_flags)
        self.assertIn("ETHUSDT", decision.diagnostics["sell_reasons"])
        self.assertEqual(
            decision.diagnostics["strategy_stop_evaluation"]["outcome"],
            "TRIGGERED",
        )
        self.assertEqual(
            decision.diagnostics["strategy_stop_evaluation"]["action_result"],
            "BLOCKED",
        )

    def test_crypto_live_pool_rotation_invalid_held_highest_price_is_no_order(self) -> None:
        entrypoint = get_strategy_entrypoint("crypto_live_pool_rotation")
        fake_core = SimpleNamespace(
            compute_allocation_budgets=lambda *_args: {
                "btc_target_ratio": 0.1,
                "trend_target_ratio": 0.1,
                "trend_usdt_pool": 10.0,
                "dca_usdt_pool": 10.0,
            },
            select_rotation_weights=lambda *_args, **_kwargs: {
                "ETHUSDT": {"weight": 1.0, "relative_score": 1.0, "abs_momentum": 0.1}
            },
            get_dynamic_btc_base_order=lambda _total_equity: 1.0,
            allocate_trend_buy_budget=lambda *_args, **_kwargs: {},
        )
        fake_rotation = SimpleNamespace(
            resolve_authoritative_rotation_pool=lambda *_args, **_kwargs: ["ETHUSDT"],
            plan_trend_buys=lambda *_args, **_kwargs: ([], {}),
        )
        missing = object()

        def evaluate(highest_price):
            now = _fresh_as_of()
            symbol_state = {"is_holding": True, "entry_price": 2800.0}
            if highest_price is not missing:
                symbol_state["highest_price"] = highest_price
            return _evaluate_once(
                entrypoint,
                StrategyContext(
                    as_of=now,
                    market_data={
                        "market_prices": {"ETHUSDT": 3000.0},
                        "derived_indicators": {
                            "ETHUSDT": {"atr14": 100.0, "sma60": 2600.0}
                        },
                        "benchmark_snapshot": {"regime_on": True},
                        "portfolio_snapshot": PortfolioSnapshot(
                            as_of=now,
                            total_equity=1000.0,
                            positions=(
                                Position(symbol="ETHUSDT", quantity=1.0, market_value=3000.0),
                            ),
                            metadata={
                                "account_metrics": {
                                    "total_equity": 1000.0,
                                    "cash_usdt": 1000.0,
                                    "trend_value": 0.0,
                                    "dca_value": 0.0,
                                },
                                "observed_effective_exposure": 0.0,
                            },
                        ),
                        "universe_snapshot": (),
                    },
                    state={"ETHUSDT": symbol_state},
                    artifacts={
                        **_synthetic_risk_artifacts(
                            "crypto_live_pool_rotation",
                            "BTCUSDT",
                            "ETHUSDT",
                        )
                    },
                )
            )

        with patch(
            "crypto_strategies.entrypoints._load_legacy_modules",
            return_value=(fake_core, fake_rotation),
        ):
            for highest_price in (
                missing,
                None,
                float("nan"),
                float("inf"),
                0.0,
                -1.0,
                2700.0,
            ):
                with self.subTest(highest_price=highest_price):
                    decision = evaluate(highest_price)
                    self.assertEqual(decision.positions, ())
                    self.assertEqual(decision.budgets, ())
                    self.assertIn("rejected:strategy_stop_input", decision.risk_flags)
                    self.assertEqual(
                        decision.diagnostics["strategy_stop_evaluation"]["outcome"],
                        "TRIGGERED",
                    )
                    self.assertEqual(
                        decision.diagnostics["strategy_stop_evaluation"]["action_result"],
                        "BLOCKED",
                    )


if __name__ == "__main__":
    unittest.main()

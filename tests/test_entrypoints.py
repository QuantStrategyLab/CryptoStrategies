from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quant_platform_kit import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from crypto_strategies import get_strategy_entrypoint


def _synthetic_member_mandate(*symbols: str) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "mandate_id": "synthetic_algorithm_equivalence_only",
        "mandate_version": "test-v1",
        "authority_receipt_sha256": "a" * 64,
        "authority_scope": "RESEARCH_ONLY",
        "strategy_profile": "synthetic_test_fixture",
        "account_mode": "synthetic_test_fixture",
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


def _fresh_as_of() -> datetime:
    return datetime.now(timezone.utc)


class CryptoStrategyEntrypointTests(unittest.TestCase):
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
            decision = entrypoint.evaluate(
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

        decision = entrypoint.evaluate(
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
                    "mandate_provenance": _synthetic_member_mandate(
                        "BTCUSDT", "BNBUSDT", "ETHUSDT", "SOLUSDT"
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

        decision = entrypoint.evaluate(
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
                    "mandate_provenance": _synthetic_member_mandate(
                        "BTCUSDT", "ETHUSDT", "SOLUSDT"
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
            decision = entrypoint.evaluate(
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
        def evaluate(prices, indicators):
            now = _fresh_as_of()
            return entrypoint.evaluate(
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
                        "mandate_provenance": _synthetic_member_mandate(
                            "BTCUSDT", "ETHUSDT"
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

        for decision in (missing, triggered):
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
        self.assertIn("rejected:strategy_stop_input", missing.risk_flags)

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
            decision = entrypoint.evaluate(
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
                        "mandate_provenance": _synthetic_member_mandate(
                            "BTCUSDT", "ETHUSDT"
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
            decision = entrypoint.evaluate(
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
                        "mandate_provenance": _synthetic_member_mandate(
                            "BTCUSDT", "ETHUSDT"
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
            return entrypoint.evaluate(
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
                        "mandate_provenance": _synthetic_member_mandate(
                            "BTCUSDT", "ETHUSDT"
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

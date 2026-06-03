from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quant_platform_kit import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from crypto_strategies import get_strategy_entrypoint


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
                        as_of="2026-04-06",
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
                        },
                    ),
                    "universe_snapshot": upstream_pool,
                },
                state=state,
                artifacts={"trend_pool_contract": {"source": "explicit_artifact"}},
            )
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
        self.assertAlmostEqual(
            decision.diagnostics["btc_base_order_usdt"],
            legacy_core.get_dynamic_btc_base_order(account_metrics["total_equity"]),
        )

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


if __name__ == "__main__":
    unittest.main()

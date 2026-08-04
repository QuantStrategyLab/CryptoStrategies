from __future__ import annotations

from datetime import datetime, timezone
import unittest

from crypto_strategies.strategies.crypto_live_pool_rotation.rotation import (
    build_strategy_stop_evaluation,
    evaluate_held_trend_stops,
    resolve_authoritative_rotation_pool,
)


class RotationAuthorityTests(unittest.TestCase):
    def test_held_symbol_missing_price_or_atr_blocks_stop_clear(self) -> None:
        state = {
            "ETHUSDT": {
                "is_holding": True,
                "entry_price": 2800.0,
                "highest_price": 3200.0,
            }
        }

        for prices, indicators in (
            ({}, {"ETHUSDT": {"atr14": 100.0, "sma60": 2600.0}}),
            ({"ETHUSDT": 3000.0}, {"ETHUSDT": {"atr14": float("nan"), "sma60": 2600.0}}),
        ):
            with self.subTest(prices=prices, indicators=indicators):
                reasons, input_blocked = evaluate_held_trend_stops(
                    state,
                    held_symbols=("ETHUSDT",),
                    prices=prices,
                    indicators_map=indicators,
                    selected_candidates={"ETHUSDT": {}},
                    atr_multiplier=2.5,
                    get_symbol_trade_state_fn=lambda current_state, symbol: current_state[symbol],
                    set_symbol_trade_state_fn=lambda *_args: None,
                    translate_fn=lambda key, **_kwargs: key,
                )

                self.assertTrue(input_blocked)
                self.assertEqual(reasons, {"ETHUSDT": "trend_sell_reason_missing_stop_input"})

    def test_invalid_atr_multiplier_blocks_stop_clear(self) -> None:
        state = {
            "ETHUSDT": {
                "is_holding": True,
                "entry_price": 2800.0,
                "highest_price": 3200.0,
            }
        }

        for atr_multiplier in (
            True,
            False,
            None,
            "2.5",
            float("nan"),
            float("inf"),
            float("-inf"),
            0.0,
            -1.0,
        ):
            with self.subTest(atr_multiplier=atr_multiplier):
                reasons, input_blocked = evaluate_held_trend_stops(
                    state,
                    held_symbols=("ETHUSDT",),
                    prices={"ETHUSDT": 3150.0},
                    indicators_map={"ETHUSDT": {"atr14": 100.0, "sma60": 2600.0}},
                    selected_candidates={"ETHUSDT": {}},
                    atr_multiplier=atr_multiplier,
                    get_symbol_trade_state_fn=lambda current_state, symbol: current_state[symbol],
                    set_symbol_trade_state_fn=lambda *_args: None,
                    translate_fn=lambda key, **_kwargs: key,
                )

                self.assertTrue(input_blocked)
                self.assertEqual(reasons, {"ETHUSDT": "trend_sell_reason_missing_stop_input"})

    def test_held_stop_reads_persisted_state_through_custom_helper(self) -> None:
        state = {
            "trade_states": {
                "ETHUSDT": {
                    "is_holding": True,
                    "entry_price": 2800.0,
                    "highest_price": 3200.0,
                }
            }
        }

        reasons, input_blocked = evaluate_held_trend_stops(
            state,
            held_symbols=("ETHUSDT",),
            prices={"ETHUSDT": 3150.0},
            indicators_map={"ETHUSDT": {"atr14": 100.0, "sma60": 2600.0}},
            selected_candidates={"ETHUSDT": {}},
            atr_multiplier=2.5,
            get_symbol_trade_state_fn=lambda current_state, symbol: current_state[
                "trade_states"
            ][symbol],
            set_symbol_trade_state_fn=lambda *_args: None,
            translate_fn=lambda key, **_kwargs: key,
        )

        self.assertFalse(input_blocked)
        self.assertEqual(reasons, {})

    def test_strategy_stop_evaluation_is_versioned_and_digest_bound(self) -> None:
        evaluation = build_strategy_stop_evaluation(
            evaluated_at="2026-08-04T08:00:00Z",
            decision_digest_sha256="b" * 64,
            outcome="TRIGGERED",
            action_result="BLOCKED",
        )

        self.assertEqual(
            evaluation,
            {
                "evaluated": True,
                "policy_id": "crypto_live_pool_rotation.executable_stop",
                "policy_version": "v1",
                "evaluated_at": "2026-08-04T08:00:00Z",
                "decision_digest_sha256": "b" * 64,
                "outcome": "TRIGGERED",
                "action_result": "BLOCKED",
            },
        )

    def test_resolve_authoritative_rotation_pool_uses_ordered_upstream_symbols(self) -> None:
        state = {
            "trend_pool_version": "2026-03-15-core_major",
            "trend_pool_as_of_date": "2026-03-15",
            "rotation_pool_symbols": ["ADAUSDT"],
        }

        selected = resolve_authoritative_rotation_pool(
            state,
            trend_universe_symbols=[" ethusdt ", "SOLUSDT", "ETHUSDT", "BNBUSDT"],
            trend_pool_size=2,
            now_utc=datetime(2026, 4, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(selected, ["ETHUSDT", "SOLUSDT", "BNBUSDT"])
        self.assertEqual(state["rotation_pool_symbols"], selected)
        self.assertEqual(state["rotation_pool_source_version"], "2026-03-15-core_major")
        self.assertEqual(state["rotation_pool_source_as_of_date"], "2026-03-15")
        self.assertEqual(state["rotation_pool_last_month"], "2026-03")

    def test_resolve_authoritative_rotation_pool_uses_cached_pool_when_refresh_disabled(self) -> None:
        state = {
            "rotation_pool_symbols": ["SOLUSDT", "ADAUSDT", "ETHUSDT"],
            "trend_pool_version": "2026-03-15-core_major",
            "trend_pool_as_of_date": "2026-03-15",
        }

        selected = resolve_authoritative_rotation_pool(
            state,
            trend_universe_symbols=["ETHUSDT", "SOLUSDT", "BNBUSDT"],
            trend_pool_size=2,
            allow_refresh=False,
            now_utc=datetime(2026, 4, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(selected, ["SOLUSDT", "ETHUSDT"])
        self.assertEqual(state["rotation_pool_symbols"], selected)

    def test_resolve_authoritative_rotation_pool_caps_fallback_when_refresh_disabled(self) -> None:
        state: dict[str, object] = {}

        selected = resolve_authoritative_rotation_pool(
            state,
            trend_universe_symbols=["ETHUSDT", "SOLUSDT", "BNBUSDT"],
            trend_pool_size=2,
            allow_refresh=False,
            now_utc=datetime(2026, 4, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(selected, ["ETHUSDT", "SOLUSDT"])
        self.assertEqual(state["rotation_pool_symbols"], selected)


if __name__ == "__main__":
    unittest.main()

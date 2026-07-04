"""Tests for the crypto_equity_combo strategy module."""

from __future__ import annotations

import unittest

from crypto_strategies.strategies.crypto_equity_combo import (
    DEFAULT_BTC_WEIGHT,
    DEFAULT_TREND_WEIGHT,
    PROFILE_NAME,
    SIGNAL_SOURCE,
    build_target_weights,
    compute_signals,
    extract_managed_symbols,
)


class CryptoEquityComboModuleTest(unittest.TestCase):
    """Verify the module-level constants and top-level API."""

    def test_profile_name(self) -> None:
        self.assertEqual(PROFILE_NAME, "crypto_equity_combo")

    def test_default_btc_weight(self) -> None:
        self.assertAlmostEqual(DEFAULT_BTC_WEIGHT, 0.30)

    def test_default_trend_weight(self) -> None:
        self.assertAlmostEqual(DEFAULT_TREND_WEIGHT, 0.70)

    def test_default_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(DEFAULT_BTC_WEIGHT + DEFAULT_TREND_WEIGHT, 1.0)

    def test_extract_managed_symbols(self) -> None:
        """extract_managed_symbols returns BTCUSDT for the combo."""
        symbols = extract_managed_symbols()
        self.assertEqual(symbols, ("BTCUSDT",))


    def test_zscore_exit_reduces_final_btc_leg_target(self) -> None:
        """Z-Score exit should reduce the combo BTC target, not stay in metadata only."""
        base_weights, _ = build_target_weights(
            prices={"BTCUSDT": 60000.0},
            indicators_map={},
            universe_snapshot=[],
            benchmark_snapshot={"regime_on": True},
            portfolio={"total_equity": 100000.0, "buying_power": 1000.0},
            btc_weight=1.0,
            trend_weight=0.0,
            smart_multiplier_enabled=False,
            as_of="2026-05-26",
        )
        reduced_weights, _ = build_target_weights(
            prices={"BTCUSDT": 60000.0},
            indicators_map={},
            universe_snapshot=[],
            benchmark_snapshot={"regime_on": True},
            portfolio={"total_equity": 100000.0, "buying_power": 1000.0},
            btc_weight=1.0,
            trend_weight=0.0,
            smart_multiplier_enabled=False,
            as_of="2026-05-26",
            zscore_exit_context={
                "plugin": "btc_zscore_exit",
                "canonical_route": "risk_reduced",
                "position_control": {
                    "final_route": "risk_reduced",
                    "target_allocations": {"BTCUSDT": 0.50, "USDT": 0.50},
                },
            },
        )

        self.assertGreater(base_weights["BTCUSDT"], 0.0)
        self.assertAlmostEqual(reduced_weights["BTCUSDT"], base_weights["BTCUSDT"] * 0.50)

    def test_trend_leg_honors_rotation_refresh_lock(self) -> None:
        """Combo trend leg should pass allow_rotation_refresh into pool resolution."""
        indicators_map = {
            "BTCUSDT": {
                "close": 100000.0,
                "sma200": 80000.0,
                "regime_on": True,
                "roc20": 0.05,
                "roc60": 0.10,
                "roc120": 0.20,
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
                "roc20": 0.48,
                "roc60": 0.65,
                "roc120": 0.95,
                "vol20": 0.30,
            },
        }

        weights, metadata = build_target_weights(
            prices={"BTCUSDT": 100000.0, "ETHUSDT": 3000.0, "SOLUSDT": 180.0},
            indicators_map=indicators_map,
            universe_snapshot=["ETHUSDT", "SOLUSDT"],
            benchmark_snapshot={"regime_on": True},
            portfolio={"total_equity": 100000.0, "buying_power": 1000.0},
            state={"rotation_pool_symbols": ["ETHUSDT"]},
            btc_weight=0.0,
            trend_weight=1.0,
            dynamic_mode=False,
            allow_rotation_refresh=False,
            rotation_top_n=1,
            weight_mode="equal",
            vol_scaling_enabled=False,
        )

        positive_weights = {symbol for symbol, weight in weights.items() if weight > 0.0}
        self.assertEqual(positive_weights, {"ETHUSDT"})
        self.assertEqual(set(metadata["trend_leg"]["weights"]), {"ETHUSDT"})

    def test_dynamic_regime_off_cut_is_configurable(self) -> None:
        """Regime-off cut should be configurable while keeping the 50% default."""
        _, default_metadata = build_target_weights(
            prices={"BTCUSDT": 60000.0},
            indicators_map={},
            universe_snapshot=[],
            benchmark_snapshot={"regime_on": False},
            portfolio={"total_equity": 100000.0, "buying_power": 1000.0},
            btc_weight=0.30,
            trend_weight=0.70,
            smart_multiplier_enabled=False,
        )
        _, custom_metadata = build_target_weights(
            prices={"BTCUSDT": 60000.0},
            indicators_map={},
            universe_snapshot=[],
            benchmark_snapshot={"regime_on": False},
            portfolio={"total_equity": 100000.0, "buying_power": 1000.0},
            btc_weight=0.30,
            trend_weight=0.70,
            dynamic_regime_off_cut=0.30,
            smart_multiplier_enabled=False,
        )

        self.assertAlmostEqual(default_metadata["combo"]["btc_weight"], 0.65)
        self.assertAlmostEqual(default_metadata["combo"]["trend_weight"], 0.35)
        self.assertAlmostEqual(default_metadata["combo"]["dynamic_regime_off_cut"], 0.50)
        self.assertAlmostEqual(custom_metadata["combo"]["btc_weight"], 0.51)
        self.assertAlmostEqual(custom_metadata["combo"]["trend_weight"], 0.49)
        self.assertAlmostEqual(custom_metadata["combo"]["dynamic_regime_off_cut"], 0.30)

    def test_compute_signals_returns_tuple(self) -> None:
        """compute_signals should return a 5-tuple with weights, signal_desc, cash_residual, status_desc, metadata."""
        prices = {"BTCUSDT": 60000.0}
        indicators_map = {}
        universe_snapshot: list[str] = []
        benchmark_snapshot = {"regime_on": True}
        portfolio: dict = {}

        result = compute_signals(
            prices=prices,
            indicators_map=indicators_map,
            universe_snapshot=universe_snapshot,
            benchmark_snapshot=benchmark_snapshot,
            portfolio=portfolio,
        )

        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 5)

        weights, signal_desc, has_cash_residual, status_desc, metadata = result

        self.assertIsInstance(weights, dict)
        self.assertIsInstance(signal_desc, str)
        self.assertIsInstance(has_cash_residual, bool)
        self.assertIsInstance(status_desc, str)
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata.get("signal_source"), SIGNAL_SOURCE)
        self.assertIsInstance(metadata.get("combo"), dict)

    def test_compute_signals_default_weights_in_metadata(self) -> None:
        """Metadata should include default BTC/trend weights."""
        result = compute_signals(
            prices={"BTCUSDT": 60000.0},
            indicators_map={},
            universe_snapshot=[],
            benchmark_snapshot={"regime_on": True},
            portfolio={},
        )

        metadata = result[4]
        combo = metadata.get("combo", {})
        self.assertAlmostEqual(combo["btc_weight"], DEFAULT_BTC_WEIGHT)
        self.assertAlmostEqual(combo["trend_weight"], DEFAULT_TREND_WEIGHT)


if __name__ == "__main__":
    unittest.main()

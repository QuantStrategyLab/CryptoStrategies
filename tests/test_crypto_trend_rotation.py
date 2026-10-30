"""Tests for the crypto_trend_rotation standalone strategy module."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from crypto_strategies.strategies.crypto_trend_rotation import (
    PROFILE_NAME,
    SIGNAL_SOURCE,
    REQUIRED_FEATURE_COLUMNS,
    compute_signals,
    extract_managed_symbols,
)


def _benchmark_row():
    return {"symbol": "BTCUSDT", "close": 60000.0, "sma20": 58000.0,
            "sma60": 55000.0, "sma200": 50000.0, "roc20": 0.01,
            "roc60": 0.02, "roc120": 0.03, "vol20": 0.4,
            "avg_quote_vol_30": 1e9, "avg_quote_vol_90": 1e9,
            "avg_quote_vol_180": 1e9, "trend_persist_90": 0.8, "age_days": 1000}


class CryptoTrendRotationModuleTest(unittest.TestCase):
    """Verify the module-level constants and signal computation."""

    def test_profile_name(self) -> None:
        self.assertEqual(PROFILE_NAME, "crypto_trend_rotation")

    def test_signal_source(self) -> None:
        self.assertEqual(SIGNAL_SOURCE, "feature_snapshot")

    def test_module_imports(self) -> None:
        """Verify the module can be imported and exposes expected names."""
        import crypto_strategies.strategies.crypto_trend_rotation as mod

        self.assertTrue(hasattr(mod, "compute_signals"))
        self.assertTrue(hasattr(mod, "extract_managed_symbols"))
        self.assertTrue(hasattr(mod, "build_target_weights"))
        self.assertTrue(hasattr(mod, "PROFILE_NAME"))

    def test_required_feature_columns_defined(self) -> None:
        """REQUIRED_FEATURE_COLUMNS should be a non-empty frozenset."""
        self.assertIsInstance(REQUIRED_FEATURE_COLUMNS, frozenset)
        self.assertGreater(len(REQUIRED_FEATURE_COLUMNS), 0)
        self.assertIn("symbol", REQUIRED_FEATURE_COLUMNS)
        self.assertIn("close", REQUIRED_FEATURE_COLUMNS)
        self.assertIn("sma20", REQUIRED_FEATURE_COLUMNS)

    def test_compute_signals_with_valid_data(self) -> None:
        """compute_signals should return a valid 5-tuple with altcoin weights."""
        feature_snapshot = pd.DataFrame(
            [
                {
                    "symbol": "SOLUSDT",
                    "close": 180.0,
                    "sma20": 170.0,
                    "sma60": 160.0,
                    "sma200": 120.0,
                    "roc20": 0.28,
                    "roc60": 0.45,
                    "roc120": 0.75,
                    "vol20": 0.30,
                    "avg_quote_vol_30": 42_000_000.0,
                    "avg_quote_vol_90": 39_000_000.0,
                    "avg_quote_vol_180": 36_000_000.0,
                    "trend_persist_90": 0.76,
                    "age_days": 450,
                },
                {
                    "symbol": "ETHUSDT",
                    "close": 3000.0,
                    "sma20": 2800.0,
                    "sma60": 2600.0,
                    "sma200": 2200.0,
                    "roc20": 0.20,
                    "roc60": 0.35,
                    "roc120": 0.60,
                    "vol20": 0.25,
                    "avg_quote_vol_30": 60_000_000.0,
                    "avg_quote_vol_90": 50_000_000.0,
                    "avg_quote_vol_180": 45_000_000.0,
                    "trend_persist_90": 0.80,
                    "age_days": 500,
                },
            ]
        )

        feature_snapshot = pd.concat([feature_snapshot, pd.DataFrame([_benchmark_row()])], ignore_index=True)
        weights, signal_desc, is_emergency, debug_str, metadata = compute_signals(
            feature_snapshot=feature_snapshot,
            current_holdings=[],
        )

        # The verified BTC benchmark is present. Both altcoins have:
        #   price > sma20, price > sma60, price > sma200,
        #   positive rel_20/60/120, positive abs_momentum => valid candidates.
        self.assertIsNotNone(weights)
        self.assertTrue(len(weights) > 0)
        self.assertFalse(is_emergency)
        self.assertEqual(debug_str, "ok")
        self.assertEqual(metadata["profile"], PROFILE_NAME)
        self.assertIn("managed_symbols", metadata)
        self.assertIn("selected_candidates", metadata)

    def test_compute_signals_honors_low_volatility_target_and_leverage_cap(self) -> None:
        feature_snapshot = pd.DataFrame([
            {
                "symbol": symbol,
                "close": close,
                "sma20": close * 0.95,
                "sma60": close * 0.90,
                "sma200": close * 0.80,
                "roc20": 0.20,
                "roc60": 0.35,
                "roc120": 0.60,
                "vol20": 2.0,
                "avg_quote_vol_30": 60_000_000.0,
                "avg_quote_vol_90": 50_000_000.0,
                "avg_quote_vol_180": 45_000_000.0,
                "trend_persist_90": 0.80,
                "age_days": 500,
            }
            for symbol, close in (("SOLUSDT", 180.0), ("ETHUSDT", 3000.0))
        ])
        feature_snapshot = pd.concat([feature_snapshot, pd.DataFrame([_benchmark_row()])], ignore_index=True)
        weights, _, _, _, metadata = compute_signals(
            feature_snapshot,
            [],
            target_vol=0.40,
            max_leverage=0.20,
        )
        self.assertIsNotNone(weights)
        self.assertAlmostEqual(sum(weights.values()), 0.20)
        self.assertTrue(all(
            payload["volatility_scaling_status"] == "proxy_scaled"
            for payload in metadata["selected_candidates"].values()
        ))
        self.assertTrue(all(
            payload["volatility_scaling_status"] == "proxy_scaled"
            for payload in metadata["selected_candidates"].values()
        ))

    def test_invalid_btc_benchmark_rejects_without_target_weights(self) -> None:
        from crypto_strategies.strategies.crypto_trend_rotation import build_target_weights

        valid = _benchmark_row()
        for field, value in (("close", 0), ("sma200", -1), ("close", float("nan")),
                             ("sma200", float("inf")), ("roc20", float("nan")),
                             ("roc60", float("inf")), ("roc120", None),
                             ("regime_on", "invalid"), ("regime_on", None),
                             ("regime_on", float("nan")), ("close", True),
                             ("sma200", np.bool_(True)), ("roc20", True)):
            row = {**valid, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_target_weights({"BTCUSDT": row}, [], {}, {}, {})
        with self.assertRaises(ValueError):
            build_target_weights({}, [], {}, {}, {})
        with self.assertRaises(ValueError):
            compute_signals(pd.DataFrame([{**valid, "symbol": "SOLUSDT"}]), [])
        for field in ("close", "sma200", "roc20", "roc60", "roc120"):
            with self.subTest(dataframe_field=field), self.assertRaises(ValueError):
                compute_signals(pd.DataFrame([{**valid, field: True}, {**valid, "symbol": "SOLUSDT"}]), [])
        for missing_regime in (None, float("nan")):
            with self.subTest(dataframe_regime=missing_regime), self.assertRaises(ValueError):
                compute_signals(pd.DataFrame([{**valid, "regime_on": missing_regime}]), [])

    def test_valid_btc_fields_and_explicit_regime_drive_circuit_breaker(self) -> None:
        from crypto_strategies.strategies.crypto_trend_rotation import (
            _check_circuit_breaker, _extract_btc_snapshot,
        )

        row = _benchmark_row()
        extracted = _extract_btc_snapshot({"BTCUSDT": row})
        self.assertEqual(extracted["close"], row["close"])
        self.assertEqual(extracted["sma200"], row["sma200"])
        self.assertEqual(extracted["btc_roc20"], row["roc20"])
        self.assertEqual(_check_circuit_breaker(extracted), (False, ""))
        forced = _extract_btc_snapshot({"BTCUSDT": {**row, "close": 30000.0, "regime_on": True}})
        self.assertEqual(_check_circuit_breaker(forced), (True, "btc_drawdown_40%_exceeds_30%"))
        off = _extract_btc_snapshot({"BTCUSDT": {**row, "regime_on": False}})
        self.assertEqual(_check_circuit_breaker(off), (True, "btc_below_sma200"))
        for value, expected in ((np.bool_(True), True), (np.bool_(False), False),
                                (np.int64(1), True), (np.int64(0), False),
                                (1.0, True), (0.0, False), ("yes", True), ("off", False)):
            with self.subTest(regime=value):
                result = _extract_btc_snapshot({"BTCUSDT": {**row, "regime_on": value}})
                self.assertIs(result["regime_on"], expected)

    def test_authoritative_pool_excludes_btc_and_outside_features_but_keeps_held_visible(self) -> None:
        btc = _benchmark_row()
        alt = {**btc, "symbol": "ETHUSDT", "close": 3000.0, "sma20": 2800.0,
               "sma60": 2600.0, "sma200": 2200.0, "roc20": 0.3, "roc60": 0.4, "roc120": 0.6}
        extra = {**alt, "symbol": "SOLUSDT", "roc20": 0.8, "roc60": 0.9, "roc120": 1.0}
        frame = pd.DataFrame([btc, alt, extra])
        weights, _, _, _, metadata = compute_signals(
            frame, ["OLDUSDT", "BTCUSDT"], universe_snapshot=["ETHUSDT", "BTC-USD", "BTCUSDT.P"]
        )
        self.assertEqual(set(weights), {"ETHUSDT"})
        self.assertEqual(set(metadata["managed_symbols"]), {"ETHUSDT", "OLDUSDT"})
        from crypto_strategies.strategies.crypto_trend_rotation import build_target_weights
        self.assertEqual(build_target_weights({"BTCUSDT": btc}, ["BTCUSDT"], {},
                         {"rotation_pool_symbols": ["BTCUSDT"]}, {}), {})
        for invalid in ([], ["BTCUSDT"], ["UNKNOWNUSDT"], "ETHUSDT", [None]):
            with self.subTest(universe=invalid), self.assertRaises(ValueError):
                compute_signals(frame, [], universe_snapshot=invalid)
        # Research callers can still omit the explicit pool; BTC stays a benchmark.
        _, _, _, _, research = compute_signals(frame, [])
        self.assertNotIn("BTCUSDT", research["managed_symbols"])

    def test_scaling_caps_gross_exposure_for_large_weights_and_unknown_volatility(self) -> None:
        from crypto_strategies.strategies.crypto_trend_rotation import _apply_volatility_scaling

        for weights in ({"A": 1.0}, {"A": 1.2, "B": 0.8}):
            scaled = _apply_volatility_scaling(
                weights,
                {symbol: {"vol20": 2.0} for symbol in weights},
                target_vol=0.40,
                max_leverage=0.20,
            )
            self.assertAlmostEqual(sum(scaled.values()), 0.20)

        capped_without_vol_scaling = _apply_volatility_scaling(
            {"A": 0.8, "B": 0.7},
            {"A": {"vol20": 0.2}, "B": {"vol20": 0.3}},
            vol_scaling_enabled=False,
            max_leverage=0.20,
        )
        self.assertAlmostEqual(sum(capped_without_vol_scaling.values()), 0.20)

        blocked_unknown = _apply_volatility_scaling(
            {"A": 1.0}, {"A": {"vol20": float("nan")}}, max_leverage=0.20
        )
        self.assertEqual(blocked_unknown, {"A": 0.0})

        zero_cap = _apply_volatility_scaling(
            {"A": 1.0}, {"A": {"vol20": 0.2}}, max_leverage=0.0
        )
        self.assertEqual(zero_cap, {"A": 0.0})

    def test_compute_signals_empty_snapshot(self) -> None:
        """An empty feature snapshot should return None weights."""
        feature_snapshot = pd.DataFrame()
        with self.assertRaises(ValueError):
            compute_signals(
                feature_snapshot=feature_snapshot,
                current_holdings=[],
            )

    def test_compute_signals_missing_columns(self) -> None:
        """A snapshot missing required columns should raise."""
        feature_snapshot = pd.DataFrame(
            [{"symbol": "SOLUSDT", "close": 180.0}]  # missing sma20, etc.
        )
        with self.assertRaises(ValueError):
            compute_signals(
                feature_snapshot=feature_snapshot,
                current_holdings=[],
            )

    def test_extract_managed_symbols(self) -> None:
        """extract_managed_symbols should return sorted uppercased symbols."""
        feature_snapshot = pd.DataFrame(
            [
                {"symbol": "solusdt", "close": 180.0},
                {"symbol": " ethusdt ", "close": 3000.0},
            ]
        )
        symbols = extract_managed_symbols(feature_snapshot)
        self.assertIn("SOLUSDT", symbols)
        self.assertIn("ETHUSDT", symbols)
        # BNBUSDT is not in the data
        self.assertNotIn("BNBUSDT", symbols)
        with_benchmark = pd.concat([feature_snapshot, pd.DataFrame([
            {"symbol": symbol} for symbol in ("BTC", "BTC-USD", "BTCUSDT", "BTCUSDT.P")
        ])], ignore_index=True)
        self.assertEqual(extract_managed_symbols(with_benchmark), symbols)

    def test_extract_managed_symbols_empty(self) -> None:
        """An empty snapshot should return an empty tuple."""
        feature_snapshot = pd.DataFrame()
        symbols = extract_managed_symbols(feature_snapshot)
        self.assertEqual(symbols, ())


if __name__ == "__main__":
    unittest.main()

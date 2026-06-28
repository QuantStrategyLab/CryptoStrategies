"""Tests for the crypto_trend_rotation standalone strategy module."""

from __future__ import annotations

import unittest

import pandas as pd

from crypto_strategies.strategies.crypto_trend_rotation import (
    PROFILE_NAME,
    SIGNAL_SOURCE,
    REQUIRED_FEATURE_COLUMNS,
    compute_signals,
    extract_managed_symbols,
)


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

        weights, signal_desc, is_emergency, debug_str, metadata = compute_signals(
            feature_snapshot=feature_snapshot,
            current_holdings=[],
        )

        # After enhancement: _extract_btc_snapshot provides default BTC benchmark
        # data (regime_on=True) when no BTCUSDT row is present, so rotation now
        # finds valid candidates from the test data. Both altcoins have:
        #   price > sma20, price > sma60, price > sma200,
        #   positive rel_20/60/120, positive abs_momentum => valid candidates.
        self.assertIsNotNone(weights)
        self.assertTrue(len(weights) > 0)
        self.assertFalse(is_emergency)
        self.assertEqual(debug_str, "ok")
        self.assertEqual(metadata["profile"], PROFILE_NAME)
        self.assertIn("managed_symbols", metadata)
        self.assertIn("selected_candidates", metadata)

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

    def test_extract_managed_symbols_empty(self) -> None:
        """An empty snapshot should return an empty tuple."""
        feature_snapshot = pd.DataFrame()
        symbols = extract_managed_symbols(feature_snapshot)
        self.assertEqual(symbols, ())


if __name__ == "__main__":
    unittest.main()

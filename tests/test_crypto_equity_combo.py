"""Tests for the crypto_equity_combo strategy module."""

from __future__ import annotations

import unittest

from crypto_strategies.strategies.crypto_equity_combo import (
    DEFAULT_BTC_WEIGHT,
    DEFAULT_TREND_WEIGHT,
    PROFILE_NAME,
    SIGNAL_SOURCE,
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

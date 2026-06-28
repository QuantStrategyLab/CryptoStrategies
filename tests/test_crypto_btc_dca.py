"""Tests for the crypto_btc_dca standalone strategy module."""

from __future__ import annotations

import math
import unittest

from crypto_strategies.strategies.crypto_btc_dca import (
    PROFILE_NAME,
    compute_signals,
    extract_managed_symbols,
    get_dynamic_btc_target_ratio,
)


class CryptoBtcDcaModuleTest(unittest.TestCase):
    """Verify the module-level constants and standalone helpers."""

    def test_profile_name(self) -> None:
        self.assertEqual(PROFILE_NAME, "crypto_btc_dca")

    def test_get_dynamic_btc_target_ratio_at_minimum_equity(self) -> None:
        """At very low equity the ratio should be at its base value ~0.14."""
        ratio = get_dynamic_btc_target_ratio(0.0)
        # safe_equity is clamped to 1.0, so ratio = 0.14 + 0.16 * log1p(1/10000)
        expected = 0.14 + 0.16 * math.log1p(1.0 / 10000.0)
        self.assertAlmostEqual(ratio, expected)

    def test_get_dynamic_btc_target_ratio_at_maximum_equity(self) -> None:
        """At very high equity the ratio should be clamped to 0.65."""
        ratio = get_dynamic_btc_target_ratio(1_000_000_000.0)
        self.assertAlmostEqual(ratio, 0.65)

    def test_get_dynamic_btc_target_ratio_mid_equity(self) -> None:
        """At 10000 equity: 0.14 + 0.16 * log1p(1) = 0.14 + 0.16*ln(2) ~ 0.2509."""
        ratio = get_dynamic_btc_target_ratio(10_000.0)
        expected = 0.14 + 0.16 * math.log1p(1.0)
        self.assertAlmostEqual(ratio, expected, places=6)

    def test_get_dynamic_btc_target_ratio_negative_equity(self) -> None:
        """Negative equity is internally clamped to 1.0, giving the base ratio ~0.14."""
        ratio = get_dynamic_btc_target_ratio(-1.0)
        expected = 0.14 + 0.16 * math.log1p(1.0 / 10000.0)
        self.assertAlmostEqual(ratio, expected)

    def test_get_dynamic_btc_target_ratio_monotonic(self) -> None:
        """Ratio should be non-decreasing with equity."""
        ratios = [get_dynamic_btc_target_ratio(e) for e in [0, 1_000, 10_000, 100_000, 1_000_000]]
        for prev, curr in zip(ratios, ratios[1:]):
            self.assertGreaterEqual(curr, prev)

    def test_compute_signals_returns_btcusdt(self) -> None:
        """compute_signals should produce a signal for BTCUSDT."""
        prices = {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0}
        portfolio = None
        total_equity = 50_000.0

        result = compute_signals(
            prices=prices,
            portfolio=portfolio,
            total_equity=total_equity,
        )

        signals = result.get("signals", {})
        self.assertIn("BTCUSDT", signals)
        self.assertAlmostEqual(signals["BTCUSDT"]["target_weight"], 1.0)
        self.assertIn("btc_target_ratio", result)
        self.assertIn("profile", result)
        self.assertIn("total_equity", result)
        self.assertEqual(result["profile"], PROFILE_NAME)
        self.assertEqual(result["total_equity"], total_equity)

    def test_compute_signals_with_state(self) -> None:
        """compute_signals should accept an optional state dict."""
        prices = {"BTCUSDT": 60000.0}
        state: dict = {"some_key": "value"}
        result = compute_signals(
            prices=prices,
            portfolio=None,
            total_equity=10_000.0,
            state=state,
        )
        self.assertIn("BTCUSDT", result["signals"])

    def test_extract_managed_symbols(self) -> None:
        """Managed symbols should only include BTCUSDT."""
        symbols = extract_managed_symbols()
        self.assertEqual(symbols, ("BTCUSDT",))

    def test_compute_signals_btc_target_ratio_matches_function(self) -> None:
        """The btc_target_ratio in compute_signals output should match get_dynamic_btc_target_ratio."""
        equity = 75_000.0
        result = compute_signals(
            prices={"BTCUSDT": 60000.0},
            portfolio=None,
            total_equity=equity,
        )
        expected_ratio = get_dynamic_btc_target_ratio(equity)
        self.assertAlmostEqual(result["btc_target_ratio"], expected_ratio)


if __name__ == "__main__":
    unittest.main()

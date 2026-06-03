from __future__ import annotations

from datetime import datetime, timezone
import unittest

from crypto_strategies.strategies.crypto_live_pool_rotation.rotation import resolve_authoritative_rotation_pool


class RotationAuthorityTests(unittest.TestCase):
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

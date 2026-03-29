import unittest

from crypto_strategies import get_strategy_definitions
from crypto_strategies.catalog import (
    CRYPTO_LEADER_ROTATION_PROFILE,
    get_strategy_definition,
)


class CatalogTest(unittest.TestCase):
    def test_catalog_contains_crypto_leader_rotation(self):
        catalog = get_strategy_definitions()
        self.assertIn(CRYPTO_LEADER_ROTATION_PROFILE, catalog)
        self.assertEqual(catalog[CRYPTO_LEADER_ROTATION_PROFILE].domain, "crypto")
        self.assertEqual(catalog[CRYPTO_LEADER_ROTATION_PROFILE].supported_platforms, frozenset({"binance"}))

    def test_known_profile_resolves(self):
        definition = get_strategy_definition("crypto_leader_rotation")
        self.assertEqual(definition.profile, CRYPTO_LEADER_ROTATION_PROFILE)


if __name__ == "__main__":
    unittest.main()

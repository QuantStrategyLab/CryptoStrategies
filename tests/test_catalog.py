import unittest

from crypto_strategies import get_strategy_definitions
from crypto_strategies.catalog import get_strategy_definition


class CatalogTest(unittest.TestCase):
    def test_catalog_starts_empty(self):
        self.assertEqual(get_strategy_definitions(), {})

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            get_strategy_definition("crypto_leader_rotation")


if __name__ == "__main__":
    unittest.main()

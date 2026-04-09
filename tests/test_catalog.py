import unittest

from quant_platform_kit.common.strategies import get_strategy_component_map
from crypto_strategies import get_strategy_definitions
from crypto_strategies.catalog import (
    CRYPTO_CANONICAL_REQUIRED_INPUTS,
    CRYPTO_LEADER_ROTATION_PROFILE,
    get_strategy_definition,
)
from crypto_strategies.runtime_adapters import BINANCE_PLATFORM, get_platform_runtime_adapter


class CatalogTest(unittest.TestCase):
    def test_catalog_contains_crypto_leader_rotation(self):
        catalog = get_strategy_definitions()
        self.assertIn(CRYPTO_LEADER_ROTATION_PROFILE, catalog)
        self.assertEqual(catalog[CRYPTO_LEADER_ROTATION_PROFILE].domain, "crypto")
        self.assertEqual(catalog[CRYPTO_LEADER_ROTATION_PROFILE].supported_platforms, frozenset({"binance"}))
        self.assertEqual(catalog[CRYPTO_LEADER_ROTATION_PROFILE].target_mode, "weight")
        self.assertEqual(catalog[CRYPTO_LEADER_ROTATION_PROFILE].required_inputs, CRYPTO_CANONICAL_REQUIRED_INPUTS)

    def test_known_profile_resolves(self):
        definition = get_strategy_definition("crypto_leader_rotation")
        self.assertEqual(definition.profile, CRYPTO_LEADER_ROTATION_PROFILE)
        component_map = get_strategy_component_map(definition)
        core_module = component_map["core"]
        self.assertEqual(
            core_module.module_path,
            "crypto_strategies.strategies.crypto_leader_rotation.core",
        )
        rotation_module = component_map["rotation"]
        self.assertEqual(
            rotation_module.module_path,
            "crypto_strategies.strategies.crypto_leader_rotation.rotation",
        )

    def test_runtime_adapter_covers_canonical_inputs(self):
        definition = get_strategy_definition(CRYPTO_LEADER_ROTATION_PROFILE)
        adapter = get_platform_runtime_adapter(CRYPTO_LEADER_ROTATION_PROFILE, platform_id=BINANCE_PLATFORM)
        self.assertLessEqual(definition.required_inputs, adapter.available_inputs)
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")


if __name__ == "__main__":
    unittest.main()

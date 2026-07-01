from __future__ import annotations

import unittest
import tomllib
from pathlib import Path

from crypto_strategies import (
    get_strategy_definition,
    get_strategy_definitions,
    get_strategy_entrypoint,
    get_strategy_index_rows,
    get_strategy_metadata,
)
from crypto_strategies.manifests import get_strategy_manifest
from crypto_strategies.runtime_adapters import (
    BINANCE_PLATFORM,
    CRYPTO_CANONICAL_REQUIRED_INPUTS,
    CRYPTO_LIVE_POOL_ROTATION_ARTIFACT_CONTRACT,
    PLATFORM_RUNTIME_ADAPTERS,
    get_platform_runtime_adapter,
)
from quant_platform_kit.strategy_contracts import resolve_strategy_artifact_contract


ALLOWED_TARGET_MODES = frozenset({"weight", "value"})
PLATFORM_NATIVE_TARGET_MODES = {BINANCE_PLATFORM: "weight"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOVERNED_SOURCE_ROOTS = (
    PROJECT_ROOT / "src" / "crypto_strategies" / "strategies",
    PROJECT_ROOT / "src" / "crypto_strategies" / "entrypoints",
)
BANNED_SOURCE_SNIPPETS = (
    "os.getenv(",
    "os.environ",
    "binance",
    "BINANCE_",
)
QPK_HEALTH_COMMIT = "6d3675914eb5d3fe072d3212a90dfb55fe1c1df4"
QPK_OLD_HEALTHLESS_COMMIT = "86f03fb8e83c0d372f4e1c64cccf3e6da50b8dd4"


class ContractGovernanceTests(unittest.TestCase):
    def test_required_inputs_are_canonical(self) -> None:
        for profile, definition in get_strategy_definitions().items():
            with self.subTest(profile=profile):
                self.assertTrue(definition.required_inputs)
                self.assertLessEqual(definition.required_inputs, CRYPTO_CANONICAL_REQUIRED_INPUTS)

    def test_target_modes_are_explicit_and_supported(self) -> None:
        for profile, definition in get_strategy_definitions().items():
            with self.subTest(profile=profile):
                self.assertIn(definition.target_mode, ALLOWED_TARGET_MODES)

    def test_every_compatible_platform_has_runtime_adapter_coverage(self) -> None:
        for profile, definition in get_strategy_definitions().items():
            for platform_id in definition.supported_platforms:
                with self.subTest(profile=profile, platform_id=platform_id):
                    adapter = get_platform_runtime_adapter(profile, platform_id=platform_id)
                    self.assertLessEqual(definition.required_inputs, adapter.available_inputs)
                    if "portfolio_snapshot" in definition.required_inputs:
                        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")
                    elif definition.target_mode != PLATFORM_NATIVE_TARGET_MODES[platform_id]:
                        self.assertTrue(adapter.portfolio_input_name)

    def test_live_profiles_declare_explicit_artifact_contract(self) -> None:
        adapter = get_platform_runtime_adapter("crypto_live_pool_rotation", platform_id=BINANCE_PLATFORM)
        contract = resolve_strategy_artifact_contract(adapter)

        self.assertEqual(contract, CRYPTO_LIVE_POOL_ROTATION_ARTIFACT_CONTRACT)
        self.assertTrue(contract.requires_snapshot_artifacts)
        self.assertTrue(contract.requires_snapshot_manifest_path)
        self.assertEqual(contract.snapshot_contract_version, "crypto_live_pool_rotation.live_pool.v1")
        self.assertEqual(contract.config_source_policy, "none")

    def test_runtime_adapter_map_matches_catalog_compatibility(self) -> None:
        compatibility_map = {
            profile: frozenset(definition.supported_platforms)
            for profile, definition in get_strategy_definitions().items()
        }
        for platform_id, adapters in PLATFORM_RUNTIME_ADAPTERS.items():
            supported_profiles = frozenset(
                profile for profile, platforms in compatibility_map.items() if platform_id in platforms
            )
            with self.subTest(platform_id=platform_id):
                self.assertEqual(frozenset(adapters), supported_profiles)

    def test_manifest_matches_catalog_definition_and_metadata(self) -> None:
        for profile in get_strategy_definitions():
            definition = get_strategy_definition(profile)
            metadata = get_strategy_metadata(profile)
            manifest = get_strategy_manifest(profile)
            with self.subTest(profile=profile):
                self.assertEqual(manifest.profile, definition.profile)
                self.assertEqual(manifest.domain, definition.domain)
                self.assertEqual(manifest.display_name, metadata.display_name)
                self.assertEqual(manifest.aliases, metadata.aliases)
                self.assertEqual(manifest.required_inputs, definition.required_inputs)
                self.assertEqual(dict(manifest.default_config), dict(definition.default_config))

    def test_entrypoint_manifest_matches_catalog_when_importable(self) -> None:
        for profile in get_strategy_definitions():
            try:
                entrypoint = get_strategy_entrypoint(profile)
            except ModuleNotFoundError as exc:
                if exc.name == "pandas":
                    self.skipTest("pandas is not installed")
                raise
            definition = get_strategy_definition(profile)
            metadata = get_strategy_metadata(profile)
            with self.subTest(profile=profile):
                self.assertEqual(entrypoint.manifest.profile, definition.profile)
                self.assertEqual(entrypoint.manifest.domain, definition.domain)
                self.assertEqual(entrypoint.manifest.display_name, metadata.display_name)
                self.assertEqual(entrypoint.manifest.aliases, metadata.aliases)
                self.assertEqual(entrypoint.manifest.required_inputs, definition.required_inputs)

    def test_strategy_index_rows_expose_expected_contract_fields(self) -> None:
        rows = {row["canonical_profile"]: row for row in get_strategy_index_rows()}
        for profile, definition in get_strategy_definitions().items():
            row = rows[profile]
            with self.subTest(profile=profile):
                self.assertEqual(row["required_inputs"], definition.required_inputs)
                self.assertEqual(row["target_mode"], definition.target_mode)
                self.assertEqual(row["compatible_platforms"], definition.supported_platforms)

    def test_strategy_and_entrypoint_sources_do_not_branch_on_platform_or_env(self) -> None:
        for root in GOVERNED_SOURCE_ROOTS:
            for path in sorted(root.rglob("*.py")):
                text = path.read_text(encoding="utf-8").lower()
                for snippet in BANNED_SOURCE_SNIPPETS:
                    with self.subTest(path=str(path.relative_to(root.parent.parent)), snippet=snippet):
                        self.assertNotIn(snippet.lower(), text)

    def test_qpk_dependency_includes_shared_health_module_release(self) -> None:
        pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = pyproject["project"]["dependencies"]
        qpk_dependencies = [dep for dep in dependencies if dep.startswith("quant-platform-kit @ ")]
        expected_dependency = (
            "quant-platform-kit @ "
            f"git+https://github.com/QuantStrategyLab/QuantPlatformKit.git@{QPK_HEALTH_COMMIT}"
        )

        self.assertEqual(qpk_dependencies, [expected_dependency])
        self.assertNotIn(QPK_OLD_HEALTHLESS_COMMIT, qpk_dependencies[0])


if __name__ == "__main__":
    unittest.main()

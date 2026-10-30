from crypto_strategies.catalog import get_runtime_enabled_profiles
from crypto_strategies.runtime_allowlist import (
    DRY_RUN_RUNTIME_ALLOWLIST,
    get_runtime_selectable_profiles,
)


def test_legacy_runtime_entrypoint_reads_explicit_allowlist():
    assert get_runtime_enabled_profiles() == get_runtime_selectable_profiles()


def test_crypto_profiles_remain_out_of_runtime_until_evidence_grant():
    assert not get_runtime_selectable_profiles()


def test_full_cycle_no_submit_grant_does_not_enable_production_profiles():
    assert DRY_RUN_RUNTIME_ALLOWLIST == frozenset({"crypto_live_pool_rotation", "crypto_btc_dca", "crypto_trend_rotation"})
    assert DRY_RUN_RUNTIME_ALLOWLIST.isdisjoint(get_runtime_enabled_profiles())
    assert get_runtime_selectable_profiles() == frozenset()


def test_validation_profiles_remain_crypto_binance_only_without_production_selection():
    from crypto_strategies.catalog import get_strategy_definition, get_strategy_metadata
    from crypto_strategies.manifests import get_strategy_manifest
    from crypto_strategies.runtime_adapters import get_platform_runtime_adapter

    for profile in ("crypto_btc_dca", "crypto_trend_rotation"):
        definition = get_strategy_definition(profile)
        metadata = get_strategy_metadata(profile)
        manifest = get_strategy_manifest(profile)
        assert definition.domain == manifest.domain == "crypto"
        assert definition.supported_platforms == frozenset({"binance"})
        assert manifest.profile == metadata.canonical_profile == profile
        assert metadata.status in {"shadow_candidate", "research_backtest_only"}
        adapter = get_platform_runtime_adapter(profile, platform_id="binance")
        if profile == "crypto_trend_rotation":
            assert adapter.artifact_contract.requires_snapshot_artifacts is True
            assert adapter.artifact_contract.requires_snapshot_manifest_path is True
            assert adapter.artifact_contract.snapshot_contract_version == "crypto_trend_rotation.v1"
        assert profile not in get_runtime_selectable_profiles()
        assert profile in DRY_RUN_RUNTIME_ALLOWLIST

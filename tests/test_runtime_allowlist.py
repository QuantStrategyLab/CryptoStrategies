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
    assert DRY_RUN_RUNTIME_ALLOWLIST == frozenset({"crypto_live_pool_rotation"})
    assert DRY_RUN_RUNTIME_ALLOWLIST.isdisjoint(get_runtime_enabled_profiles())
    assert get_runtime_selectable_profiles() == frozenset()

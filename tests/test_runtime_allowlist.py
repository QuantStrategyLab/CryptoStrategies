from crypto_strategies.catalog import get_runtime_enabled_profiles
from crypto_strategies.runtime_allowlist import get_runtime_selectable_profiles


def test_legacy_runtime_entrypoint_reads_explicit_allowlist():
    assert get_runtime_enabled_profiles() == get_runtime_selectable_profiles()


def test_crypto_profiles_remain_out_of_runtime_until_evidence_grant():
    assert not get_runtime_selectable_profiles()

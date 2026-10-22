"""Explicit runtime-selectable profiles for crypto strategies."""

# No crypto profile currently has a canonical runtime-selectable grant.
RUNTIME_SELECTABLE_ALLOWLIST_V1 = frozenset()

# Only for full_cycle_no_submit validation; this is not production authorization.
DRY_RUN_RUNTIME_ALLOWLIST = frozenset({"crypto_live_pool_rotation"})


def get_runtime_selectable_profiles() -> frozenset[str]:
    return RUNTIME_SELECTABLE_ALLOWLIST_V1

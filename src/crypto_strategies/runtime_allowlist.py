"""Explicit runtime-selectable profiles for crypto strategies."""

# No crypto profile currently has a canonical runtime-selectable grant.
RUNTIME_SELECTABLE_ALLOWLIST_V1 = frozenset()


def get_runtime_selectable_profiles() -> frozenset[str]:
    return RUNTIME_SELECTABLE_ALLOWLIST_V1

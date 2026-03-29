from __future__ import annotations

from quant_platform_kit.common.strategies import (
    CRYPTO_DOMAIN,
    StrategyComponentDefinition,
    StrategyDefinition,
)

CRYPTO_LEADER_ROTATION_PROFILE = "crypto_leader_rotation"

STRATEGY_DEFINITIONS: dict[str, StrategyDefinition] = {
    CRYPTO_LEADER_ROTATION_PROFILE: StrategyDefinition(
        profile=CRYPTO_LEADER_ROTATION_PROFILE,
        domain=CRYPTO_DOMAIN,
        supported_platforms=frozenset({"binance"}),
        components=(
            StrategyComponentDefinition(
                name="core",
                module_path="crypto_strategies.strategies.crypto_leader_rotation.core",
            ),
            StrategyComponentDefinition(
                name="rotation",
                module_path="crypto_strategies.strategies.crypto_leader_rotation.rotation",
            ),
        ),
    ),
}


def get_strategy_definitions() -> dict[str, StrategyDefinition]:
    return dict(STRATEGY_DEFINITIONS)


def get_strategy_definition(profile: str) -> StrategyDefinition:
    normalized = str(profile or "").strip().lower()
    if normalized not in STRATEGY_DEFINITIONS:
        supported = ", ".join(sorted(STRATEGY_DEFINITIONS)) or "<none>"
        raise ValueError(
            f"Unknown crypto strategy profile={profile!r}; supported values: {supported}"
        )
    return STRATEGY_DEFINITIONS[normalized]

from __future__ import annotations

from quant_platform_kit.common.strategies import (
    CRYPTO_DOMAIN,
    StrategyCatalog,
    StrategyComponentDefinition,
    StrategyDefinition,
    StrategyMetadata,
    build_strategy_catalog,
    build_strategy_index_rows,
    get_catalog_strategy_definition,
    get_catalog_strategy_metadata,
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

STRATEGY_METADATA: dict[str, StrategyMetadata] = {
    CRYPTO_LEADER_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=CRYPTO_LEADER_ROTATION_PROFILE,
        display_name="Crypto Leader Rotation",
        description="Trend-following crypto rotation with staged entries, degradation controls, and cash parking.",
        aliases=(),
        cadence="daily",
        asset_scope="liquid_crypto_assets",
        benchmark="BTC",
        role="crypto_offensive_rotation",
        status="runtime_enabled",
    ),
}

STRATEGY_CATALOG: StrategyCatalog = build_strategy_catalog(
    strategy_definitions=STRATEGY_DEFINITIONS,
    metadata=STRATEGY_METADATA,
)


def get_strategy_definitions() -> dict[str, StrategyDefinition]:
    return dict(STRATEGY_DEFINITIONS)


def get_strategy_catalog() -> StrategyCatalog:
    return STRATEGY_CATALOG


def get_strategy_definition(profile: str) -> StrategyDefinition:
    return get_catalog_strategy_definition(STRATEGY_CATALOG, profile)


def get_strategy_metadata(profile: str) -> StrategyMetadata:
    return get_catalog_strategy_metadata(STRATEGY_CATALOG, profile)


def get_strategy_index_rows() -> list[dict[str, object]]:
    return build_strategy_index_rows(STRATEGY_CATALOG)

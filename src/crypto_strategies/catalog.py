from __future__ import annotations

from quant_platform_kit.common.strategies import (
    CRYPTO_DOMAIN,
    StrategyCatalog,
    StrategyComponentDefinition,
    StrategyDefinition,
    StrategyEntrypointDefinition,
    StrategyMetadata,
    build_strategy_catalog,
    build_strategy_index_rows,
    get_catalog_strategy_definition,
    get_catalog_strategy_metadata,
    load_strategy_entrypoint,
)

CRYPTO_LEADER_ROTATION_PROFILE = "crypto_leader_rotation"

CRYPTO_CANONICAL_REQUIRED_INPUTS = frozenset(
    {
        "market_prices",
        "derived_indicators",
        "benchmark_snapshot",
        "portfolio_snapshot",
        "universe_snapshot",
    }
)

STRATEGY_TARGET_MODES: dict[str, str] = {
    CRYPTO_LEADER_ROTATION_PROFILE: "weight",
}

CRYPTO_LEADER_ROTATION_DEFAULT_CONFIG = {
    "trend_pool_size": 5,
    "rotation_top_n": 2,
    "min_history_days": 365,
    "min_avg_quote_vol_180": 8000000.0,
    "membership_bonus": 0.10,
    "weight_mode": "inverse_vol",
    "allow_rotation_refresh": True,
    "atr_multiplier": 2.5,
    "artifact_contract_version": "crypto_leader_rotation.live_pool.v1",
    "artifact_max_age_days": 45,
    "artifact_acceptable_modes": ("core_major",),
}

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
        entrypoint=StrategyEntrypointDefinition(
            module_path="crypto_strategies.entrypoints",
            attribute_name="crypto_leader_rotation_entrypoint",
        ),
        required_inputs=CRYPTO_CANONICAL_REQUIRED_INPUTS,
        default_config=CRYPTO_LEADER_ROTATION_DEFAULT_CONFIG,
        target_mode=STRATEGY_TARGET_MODES[CRYPTO_LEADER_ROTATION_PROFILE],
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


def get_strategy_entrypoint(profile: str):
    definition = get_strategy_definition(profile)
    metadata = get_strategy_metadata(profile)
    return load_strategy_entrypoint(definition, metadata=metadata)


def get_strategy_metadata(profile: str) -> StrategyMetadata:
    return get_catalog_strategy_metadata(STRATEGY_CATALOG, profile)


def get_strategy_index_rows() -> list[dict[str, object]]:
    return build_strategy_index_rows(STRATEGY_CATALOG)

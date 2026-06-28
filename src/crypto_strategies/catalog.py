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

CRYPTO_LIVE_POOL_ROTATION_PROFILE = "crypto_live_pool_rotation"
CRYPTO_LEADER_ROTATION_PROFILE = "crypto_leader_rotation"
CRYPTO_LIVE_POOL_ROTATION_ALIASES = (CRYPTO_LEADER_ROTATION_PROFILE,)

CRYPTO_BTC_DCA_PROFILE = "crypto_btc_dca"
CRYPTO_TREND_ROTATION_PROFILE = "crypto_trend_rotation"
CRYPTO_EQUITY_COMBO_PROFILE = "crypto_equity_combo"

CRYPTO_CANONICAL_REQUIRED_INPUTS = frozenset(
    {
        "market_prices",
        "derived_indicators",
        "benchmark_snapshot",
        "portfolio_snapshot",
        "universe_snapshot",
    }
)

CRYPTO_BTC_DCA_REQUIRED_INPUTS = frozenset(
    {
        "market_prices",
        "portfolio_snapshot",
    }
)

CRYPTO_EQUITY_COMBO_REQUIRED_INPUTS = frozenset(
    {
        "market_prices",
        "derived_indicators",
        "benchmark_snapshot",
        "portfolio_snapshot",
        "universe_snapshot",
    }
)

STRATEGY_TARGET_MODES: dict[str, str] = {
    CRYPTO_LIVE_POOL_ROTATION_PROFILE: "weight",
    CRYPTO_BTC_DCA_PROFILE: "weight",
    CRYPTO_TREND_ROTATION_PROFILE: "weight",
    CRYPTO_EQUITY_COMBO_PROFILE: "weight",
}

CRYPTO_LIVE_POOL_ROTATION_DEFAULT_CONFIG = {
    "trend_pool_size": 5,
    "rotation_top_n": 2,
    "min_history_days": 365,
    "min_avg_quote_vol_180": 8000000.0,
    "membership_bonus": 0.10,
    "weight_mode": "inverse_vol",
    "allow_rotation_refresh": True,
    "atr_multiplier": 2.5,
    "artifact_contract_version": "crypto_live_pool_rotation.live_pool.v1",
    "artifact_max_age_days": 45,
    "artifact_acceptable_modes": ("core_major",),
}

CRYPTO_BTC_DCA_DEFAULT_CONFIG = {
    "target_ratio_min": 0.0,
    "target_ratio_max": 0.65,
    "ratio_base": 0.14,
    "ratio_scale": 0.16,
    "equity_normalizer": 10000.0,
}

CRYPTO_TREND_ROTATION_DEFAULT_CONFIG = {
    "trend_pool_size": 5,
    "rotation_top_n": 2,
    "min_history_days": 365,
    "min_avg_quote_vol_180": 8000000.0,
    "membership_bonus": 0.10,
    "weight_mode": "inverse_vol",
    "allow_rotation_refresh": True,
    "atr_multiplier": 2.5,
}

CRYPTO_EQUITY_COMBO_DEFAULT_CONFIG = {
    "btc_weight": 0.30,
    "trend_weight": 0.70,
    "dynamic_mode": True,
}

STRATEGY_DEFINITIONS: dict[str, StrategyDefinition] = {
    CRYPTO_LIVE_POOL_ROTATION_PROFILE: StrategyDefinition(
        profile=CRYPTO_LIVE_POOL_ROTATION_PROFILE,
        domain=CRYPTO_DOMAIN,
        supported_platforms=frozenset({"binance"}),
        components=(
            StrategyComponentDefinition(
                name="core",
                module_path="crypto_strategies.strategies.crypto_live_pool_rotation.core",
            ),
            StrategyComponentDefinition(
                name="rotation",
                module_path="crypto_strategies.strategies.crypto_live_pool_rotation.rotation",
            ),
        ),
        entrypoint=StrategyEntrypointDefinition(
            module_path="crypto_strategies.entrypoints",
            attribute_name="crypto_live_pool_rotation_entrypoint",
        ),
        required_inputs=CRYPTO_CANONICAL_REQUIRED_INPUTS,
        default_config=CRYPTO_LIVE_POOL_ROTATION_DEFAULT_CONFIG,
        target_mode=STRATEGY_TARGET_MODES[CRYPTO_LIVE_POOL_ROTATION_PROFILE],
    ),
    CRYPTO_BTC_DCA_PROFILE: StrategyDefinition(
        profile=CRYPTO_BTC_DCA_PROFILE,
        domain=CRYPTO_DOMAIN,
        supported_platforms=frozenset({"binance"}),
        components=(
            StrategyComponentDefinition(
                name="core",
                module_path="crypto_strategies.strategies.crypto_btc_dca",
            ),
        ),
        entrypoint=StrategyEntrypointDefinition(
            module_path="crypto_strategies.entrypoints",
            attribute_name="crypto_btc_dca_entrypoint",
        ),
        required_inputs=CRYPTO_BTC_DCA_REQUIRED_INPUTS,
        default_config=CRYPTO_BTC_DCA_DEFAULT_CONFIG,
        target_mode=STRATEGY_TARGET_MODES[CRYPTO_BTC_DCA_PROFILE],
    ),
    CRYPTO_TREND_ROTATION_PROFILE: StrategyDefinition(
        profile=CRYPTO_TREND_ROTATION_PROFILE,
        domain=CRYPTO_DOMAIN,
        supported_platforms=frozenset({"binance"}),
        components=(
            StrategyComponentDefinition(
                name="core",
                module_path="crypto_strategies.strategies.crypto_trend_rotation",
            ),
        ),
        entrypoint=StrategyEntrypointDefinition(
            module_path="crypto_strategies.entrypoints",
            attribute_name="crypto_trend_rotation_entrypoint",
        ),
        required_inputs=CRYPTO_CANONICAL_REQUIRED_INPUTS,
        default_config=CRYPTO_TREND_ROTATION_DEFAULT_CONFIG,
        target_mode=STRATEGY_TARGET_MODES[CRYPTO_TREND_ROTATION_PROFILE],
    ),
    CRYPTO_EQUITY_COMBO_PROFILE: StrategyDefinition(
        profile=CRYPTO_EQUITY_COMBO_PROFILE,
        domain=CRYPTO_DOMAIN,
        supported_platforms=frozenset({"binance"}),
        components=(
            StrategyComponentDefinition(
                name="core",
                module_path="crypto_strategies.strategies.crypto_equity_combo",
            ),
        ),
        entrypoint=StrategyEntrypointDefinition(
            module_path="crypto_strategies.entrypoints",
            attribute_name="crypto_equity_combo_entrypoint",
        ),
        required_inputs=CRYPTO_EQUITY_COMBO_REQUIRED_INPUTS,
        default_config=CRYPTO_EQUITY_COMBO_DEFAULT_CONFIG,
        target_mode=STRATEGY_TARGET_MODES[CRYPTO_EQUITY_COMBO_PROFILE],
    ),
}

STRATEGY_METADATA: dict[str, StrategyMetadata] = {
    CRYPTO_LIVE_POOL_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=CRYPTO_LIVE_POOL_ROTATION_PROFILE,
        display_name="Crypto Live Pool Rotation",
        description="Trend-following crypto rotation with staged entries, degradation controls, and cash parking.",
        aliases=CRYPTO_LIVE_POOL_ROTATION_ALIASES,
        cadence="daily",
        asset_scope="liquid_crypto_assets",
        benchmark="BTC",
        role="crypto_offensive_rotation",
        status="runtime_enabled",
    ),
    CRYPTO_BTC_DCA_PROFILE: StrategyMetadata(
        canonical_profile=CRYPTO_BTC_DCA_PROFILE,
        display_name="Crypto BTC DCA",
        description="Dynamic BTC DCA strategy that targets a single BTCUSDT position with equity-scaled allocation.",
        aliases=(),
        cadence="daily",
        asset_scope="btc_only",
        benchmark="BTC",
        role="crypto_core_accumulation",
        status="runtime_enabled",
    ),
    CRYPTO_TREND_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=CRYPTO_TREND_ROTATION_PROFILE,
        display_name="Crypto Trend Rotation",
        description="Pure altcoin trend-following rotation with no BTC allocation.",
        aliases=(),
        cadence="daily",
        asset_scope="liquid_crypto_assets",
        benchmark="BTC",
        role="crypto_offensive_rotation",
        status="runtime_enabled",
    ),
    CRYPTO_EQUITY_COMBO_PROFILE: StrategyMetadata(
        canonical_profile=CRYPTO_EQUITY_COMBO_PROFILE,
        display_name="Crypto Equity Combo",
        description="Combined BTC DCA and trend rotation strategy with dynamic regime-based allocation.",
        aliases=(),
        cadence="daily",
        asset_scope="crypto_equity",
        benchmark="BTC",
        role="crypto_combined",
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

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
    "base_investment_usd": 100.0,
    "max_investment_usd": None,
    "cash_reserve_usd": 0.0,
    "min_investment_usd": 5.0,
    "smart_multiplier_enabled": True,
    "cycle_indicator_enabled": True,
    "cadence": "monthly",
    "monthly_day": 25,
    "monthly_window_calendar_days": 5,
    "weekly_day": 4,
    "weekly_window_calendar_days": 4,
    "quarterly_months": (1, 4, 7, 10),
    "quarterly_day": 25,
    "quarterly_window_calendar_days": 5,
    # Drawdown thresholds
    "mild_drawdown_threshold": 0.12,
    "deep_drawdown_threshold": 0.25,
    "severe_drawdown_threshold": 0.40,
    "mild_discount_gap": 0.08,
    "deep_discount_gap": 0.18,
    "expensive_gap": 0.30,
    "very_expensive_gap": 0.60,
    "shallow_drawdown_threshold": 0.05,
    "overbought_rsi": 75.0,
    "base_multiplier": 1.0,
    "mild_pullback_multiplier": 1.50,
    "deep_pullback_multiplier": 2.25,
    "severe_pullback_multiplier": 3.0,
    "expensive_multiplier": 1.0,
    "very_expensive_multiplier": 1.0,
    # AHR999 thresholds
    "ahr999_bottom_threshold": 0.45,
    "ahr999_accumulation_threshold": 0.80,
    "ahr999_dca_threshold": 1.20,
    "ahr999_bottom_multiplier": 3.0,
    "ahr999_accumulation_multiplier": 2.25,
    "ahr999_dca_multiplier": 1.50,
    "ahr999_expensive_multiplier": 0.0,
    # Z-score exit
    "zscore_exit_enabled": True,
    "zscore_exit_parking_symbol": "USDT",
    "zscore_exit_risk_reduced_exposure": 0.50,
    "zscore_exit_risk_off_exposure": 0.25,
    "zscore_exit_allow_outside_execution_window": True,
    # Legacy params (kept for backward compat)
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
    "circuit_breaker_enabled": True,
    "btc_drawdown_threshold": 0.30,
    "vol_scaling_enabled": True,
    "target_vol": 0.40,
    "max_leverage": 1.0,
}

CRYPTO_EQUITY_COMBO_DEFAULT_CONFIG = {
    "btc_weight": 0.30,
    "trend_weight": 0.70,
    "dynamic_mode": True,
    "circuit_breaker_enabled": True,
    "btc_drawdown_threshold": 0.30,
    "vol_scaling_enabled": True,
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
        localized_display_names={"zh": "加密实时池轮动"},
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
        localized_display_names={"zh": "BTC定投"},
        description="Dynamic BTC DCA strategy that targets a single BTCUSDT position with equity-scaled allocation.",
        aliases=(),
        cadence="daily_check_monthly_execution",
        asset_scope="btc_only",
        benchmark="BTC",
        role="crypto_core_accumulation",
        status="runtime_enabled",
    ),
    CRYPTO_TREND_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=CRYPTO_TREND_ROTATION_PROFILE,
        display_name="Crypto Trend Rotation",
        localized_display_names={"zh": "山寨趋势轮动"},
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
        localized_display_names={"zh": "加密动量组合"},
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


def get_runtime_enabled_profiles() -> frozenset[str]:
    """Return the set of strategy profiles allowed to run on this platform.

    This defines the rollout allowlist — the upper bound of what profiles
    may be enabled.  By default all defined profiles are allowed.
    """
    return frozenset(STRATEGY_DEFINITIONS)


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

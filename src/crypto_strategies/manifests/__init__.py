from __future__ import annotations

from quant_platform_kit.strategy_contracts import StrategyManifest


crypto_live_pool_rotation_manifest = StrategyManifest(
    profile="crypto_live_pool_rotation",
    domain="crypto",
    display_name="Crypto Live Pool Rotation",
    description="Trend-following crypto rotation with staged entries, degradation controls, and cash parking.",
    aliases=("crypto_leader_rotation",),
    required_inputs=frozenset(
        {
            "market_prices",
            "derived_indicators",
            "benchmark_snapshot",
            "portfolio_snapshot",
            "universe_snapshot",
        }
    ),
    default_config={
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
    },
)

crypto_btc_dca_manifest = StrategyManifest(
    profile="crypto_btc_dca",
    domain="crypto",
    display_name="Crypto BTC DCA",
    description="Dynamic BTC DCA strategy that targets a single BTCUSDT position with equity-scaled allocation.",
    aliases=(),
    required_inputs=frozenset(
        {
            "market_prices",
            "portfolio_snapshot",
        }
    ),
    default_config={
        "target_ratio_min": 0.0,
        "target_ratio_max": 0.65,
        "ratio_base": 0.14,
        "ratio_scale": 0.16,
        "equity_normalizer": 10000.0,
    },
)

crypto_trend_rotation_manifest = StrategyManifest(
    profile="crypto_trend_rotation",
    domain="crypto",
    display_name="Crypto Trend Rotation",
    description="Pure altcoin trend-following rotation with no BTC allocation.",
    aliases=(),
    required_inputs=frozenset(
        {
            "market_prices",
            "derived_indicators",
            "benchmark_snapshot",
            "portfolio_snapshot",
            "universe_snapshot",
        }
    ),
    default_config={
        "trend_pool_size": 5,
        "rotation_top_n": 2,
        "min_history_days": 365,
        "min_avg_quote_vol_180": 8000000.0,
        "membership_bonus": 0.10,
        "weight_mode": "inverse_vol",
        "allow_rotation_refresh": True,
        "atr_multiplier": 2.5,
    },
)

crypto_equity_combo_manifest = StrategyManifest(
    profile="crypto_equity_combo",
    domain="crypto",
    display_name="Crypto Equity Combo",
    description="Combined BTC DCA and trend rotation strategy with dynamic regime-based allocation.",
    aliases=(),
    required_inputs=frozenset(
        {
            "market_prices",
            "derived_indicators",
            "benchmark_snapshot",
            "portfolio_snapshot",
            "universe_snapshot",
        }
    ),
    default_config={
        "btc_weight": 0.30,
        "trend_weight": 0.70,
        "dynamic_mode": True,
    },
)

MANIFESTS = {
    crypto_live_pool_rotation_manifest.profile: crypto_live_pool_rotation_manifest,
    crypto_btc_dca_manifest.profile: crypto_btc_dca_manifest,
    crypto_trend_rotation_manifest.profile: crypto_trend_rotation_manifest,
    crypto_equity_combo_manifest.profile: crypto_equity_combo_manifest,
}


def get_strategy_manifest(profile: str) -> StrategyManifest:
    return MANIFESTS[profile]


__all__ = [
    "MANIFESTS",
    "crypto_live_pool_rotation_manifest",
    "crypto_btc_dca_manifest",
    "crypto_trend_rotation_manifest",
    "crypto_equity_combo_manifest",
    "get_strategy_manifest",
]

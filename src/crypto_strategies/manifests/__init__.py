from __future__ import annotations

from quant_platform_kit.strategy_contracts import StrategyManifest


crypto_leader_rotation_manifest = StrategyManifest(
    profile="crypto_leader_rotation",
    domain="crypto",
    display_name="Crypto Leader Rotation",
    description="Trend-following crypto rotation with staged entries, degradation controls, and cash parking.",
    required_inputs=frozenset(
        {
            "prices",
            "trend_indicators",
            "btc_snapshot",
            "account_metrics",
            "trend_universe_symbols",
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
        "artifact_contract_version": "crypto_leader_rotation.live_pool.v1",
        "artifact_max_age_days": 45,
        "artifact_acceptable_modes": ("core_major",),
    },
)

MANIFESTS = {crypto_leader_rotation_manifest.profile: crypto_leader_rotation_manifest}


def get_strategy_manifest(profile: str) -> StrategyManifest:
    return MANIFESTS[profile]


__all__ = ["MANIFESTS", "crypto_leader_rotation_manifest", "get_strategy_manifest"]

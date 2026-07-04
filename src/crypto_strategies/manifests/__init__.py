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
        "ahr999_bottom_threshold": 0.45,
        "ahr999_accumulation_threshold": 0.80,
        "ahr999_dca_threshold": 1.20,
        "ahr999_bottom_multiplier": 3.0,
        "ahr999_accumulation_multiplier": 2.25,
        "ahr999_dca_multiplier": 1.50,
        "ahr999_expensive_multiplier": 0.0,
        "zscore_exit_enabled": True,
        "zscore_exit_parking_symbol": "USDT",
        "zscore_exit_risk_reduced_exposure": 0.50,
        "zscore_exit_risk_off_exposure": 0.25,
        "zscore_exit_allow_outside_execution_window": True,
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
        "circuit_breaker_enabled": True,
        "btc_drawdown_threshold": 0.30,
        "vol_scaling_enabled": True,
        "target_vol": 0.40,
        "max_leverage": 1.0,
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
        "dynamic_regime_mode": "legacy",
        "dynamic_regime_off_cut": 0.50,
        "dynamic_hard_sma200_ratio": 0.97,
        "dynamic_hard_ma200_slope": -0.015,
        "dynamic_soft_sma200_ratio": 1.05,
        "dynamic_hard_btc_weight": 0.30,
        "dynamic_hard_trend_weight": 0.0,
        "dynamic_soft_btc_weight": 0.45,
        "dynamic_soft_trend_weight": 0.15,
        "smart_multiplier_enabled": True,
        "cycle_indicator_enabled": True,
        "zscore_exit_enabled": True,
        "zscore_exit_parking_symbol": "USDT",
        "zscore_exit_risk_reduced_exposure": 0.50,
        "zscore_exit_risk_off_exposure": 0.25,
        "zscore_exit_allow_outside_execution_window": True,
        "circuit_breaker_enabled": True,
        "btc_drawdown_threshold": 0.30,
        "vol_scaling_enabled": True,
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

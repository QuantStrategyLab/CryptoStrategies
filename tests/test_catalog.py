from __future__ import annotations

from crypto_strategies.catalog import (
    CRYPTO_BTC_DCA_PROFILE,
    CRYPTO_EQUITY_COMBO_PROFILE,
    CRYPTO_LIVE_POOL_ROTATION_PROFILE,
    CRYPTO_TREND_ROTATION_PROFILE,
    get_runtime_enabled_profiles,
    get_strategy_metadata,
)


def test_only_live_pool_rotation_remains_runtime_enabled() -> None:
    assert get_runtime_enabled_profiles() == frozenset({CRYPTO_LIVE_POOL_ROTATION_PROFILE})
    assert get_strategy_metadata(CRYPTO_LIVE_POOL_ROTATION_PROFILE).status == "runtime_enabled"


def test_non_live_crypto_profiles_are_not_runtime_enabled() -> None:
    assert get_strategy_metadata(CRYPTO_BTC_DCA_PROFILE).status == "shadow_candidate"
    assert get_strategy_metadata(CRYPTO_TREND_ROTATION_PROFILE).status == "research_backtest_only"
    assert get_strategy_metadata(CRYPTO_EQUITY_COMBO_PROFILE).status == "research_backtest_only"

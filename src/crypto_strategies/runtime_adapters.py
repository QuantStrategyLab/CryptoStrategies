from __future__ import annotations

from quant_platform_kit.common.strategies import resolve_catalog_profile
from quant_platform_kit.strategy_contracts import (
    StrategyRuntimeAdapter,
    validate_strategy_runtime_adapter,
)

from crypto_strategies.catalog import STRATEGY_CATALOG


BINANCE_PLATFORM = "binance"


CRYPTO_CANONICAL_REQUIRED_INPUTS = frozenset(
    {
        "market_prices",
        "derived_indicators",
        "benchmark_snapshot",
        "portfolio_snapshot",
        "universe_snapshot",
    }
)


PLATFORM_RUNTIME_ADAPTERS: dict[str, dict[str, StrategyRuntimeAdapter]] = {
    BINANCE_PLATFORM: {
        "crypto_leader_rotation": StrategyRuntimeAdapter(
            status_icon="🪙",
            available_inputs=CRYPTO_CANONICAL_REQUIRED_INPUTS,
            portfolio_input_name="portfolio_snapshot",
        ),
    }
}


def resolve_canonical_profile(profile: str | None) -> str:
    return resolve_catalog_profile(profile, strategy_catalog=STRATEGY_CATALOG)


def get_platform_runtime_adapter(profile: str | None, *, platform_id: str) -> StrategyRuntimeAdapter:
    canonical_profile = resolve_canonical_profile(profile)
    adapters = PLATFORM_RUNTIME_ADAPTERS.get(str(platform_id).strip().lower())
    if adapters is None:
        raise ValueError(f"Unsupported platform runtime adapter lookup for {platform_id!r}")
    try:
        adapter = adapters[canonical_profile]
    except KeyError as exc:
        raise ValueError(
            f"Strategy profile {canonical_profile!r} has no runtime adapter for platform {platform_id!r}"
        ) from exc
    return validate_strategy_runtime_adapter(adapter)


__all__ = [
    "BINANCE_PLATFORM",
    "CRYPTO_CANONICAL_REQUIRED_INPUTS",
    "PLATFORM_RUNTIME_ADAPTERS",
    "get_platform_runtime_adapter",
    "resolve_canonical_profile",
]

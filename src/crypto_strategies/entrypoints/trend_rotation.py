"""Entrypoint for the ``crypto_trend_rotation`` strategy profile."""

from __future__ import annotations

import math

import pandas as pd
from quant_platform_kit.common.strategy_contracts import (
    BudgetIntent,
    CallableStrategyEntrypoint,
    PositionTarget,
    StrategyContext,
    StrategyDecision,
)

from crypto_strategies.manifests import crypto_trend_rotation_manifest
from crypto_strategies.strategies import crypto_trend_rotation as _crypto_trend_rotation

from ._metrics import (
    _merge_runtime_config,
    _require_market_data,
    _resolve_portfolio_snapshot,
    _resolve_translator,
)


def evaluate_crypto_trend_rotation(ctx: StrategyContext) -> StrategyDecision:
    import crypto_strategies.entrypoints as entrypoints_pkg

    config = _merge_runtime_config(ctx, crypto_trend_rotation_manifest.default_config)
    feature_snapshot = _require_market_data(ctx, "derived_indicators")
    _require_market_data(ctx, "market_prices")
    universe_snapshot = _require_market_data(ctx, "universe_snapshot")
    portfolio = _resolve_portfolio_snapshot(ctx)
    held_symbols = []
    for position in getattr(portfolio, "positions", ()) or ():
        symbol = getattr(position, "symbol", None)
        try:
            quantity = float(getattr(position, "quantity"))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("portfolio position quantity is invalid") from None
        if not isinstance(symbol, str) or not symbol.strip() or not math.isfinite(quantity) or quantity < 0:
            raise ValueError("portfolio position is invalid")
        if quantity > 0:
            held_symbols.append(symbol)
    translator = _resolve_translator(config)

    rows = []
    for symbol, indicators in feature_snapshot.items():
        row = dict(indicators)
        row["symbol"] = symbol
        rows.append(row)
    feature_frame = pd.DataFrame(rows) if rows else pd.DataFrame()

    weights, signal_desc, is_emergency, debug_str, metadata = _crypto_trend_rotation.compute_signals(
        feature_snapshot=feature_frame,
        current_holdings=held_symbols,
        translator=translator,
        universe_snapshot=universe_snapshot,
        trend_pool_size=int(config.get("trend_pool_size", 5)),
        rotation_top_n=int(config.get("rotation_top_n", 2)),
        weight_mode=str(config.get("weight_mode", "inverse_vol")),
        allow_rotation_refresh=bool(config.get("allow_rotation_refresh", True)),
        atr_multiplier=float(config.get("atr_multiplier", 2.5)),
        circuit_breaker_enabled=bool(config.get("circuit_breaker_enabled", True)),
        btc_drawdown_threshold=float(config.get("btc_drawdown_threshold", 0.30)),
        vol_scaling_enabled=bool(config.get("vol_scaling_enabled", True)),
        target_vol=float(config.get("target_vol", 0.40)),
        max_leverage=float(config.get("max_leverage", 1.0)),
    )

    positions: list[PositionTarget] = []
    if weights is not None:
        for symbol, weight in sorted(weights.items()):
            positions.append(
                PositionTarget(
                    symbol=symbol,
                    target_weight=float(weight),
                    role="trend_rotation",
                )
            )

    budget_intents = (
        BudgetIntent(
            name="trend_rotation_pool",
            amount=0.0,
            purpose="trend_rotation",
        ),
    )

    risk_flags: tuple[str, ...] = ()
    if is_emergency:
        risk_flags += ("emergency",)
    if not weights:
        risk_flags += ("no_trend_candidates",)

    diagnostics = {
        "signal_desc": signal_desc,
        "debug_str": debug_str,
        "metadata": metadata,
        "managed_symbols": metadata.get("managed_symbols", ()),
        "profile": metadata.get("profile"),
    }

    decision = StrategyDecision(
        positions=tuple(positions),
        budgets=budget_intents,
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )
    decision = entrypoints_pkg.apply_risk_gate(decision, ctx=ctx, max_single_weight=0.30)
    entrypoints_pkg.record_strategy_decision(
        ctx,
        decision,
        profile_id=crypto_trend_rotation_manifest.profile,
        domain=crypto_trend_rotation_manifest.domain,
    )
    return decision


crypto_trend_rotation_entrypoint = CallableStrategyEntrypoint(
    manifest=crypto_trend_rotation_manifest,
    _evaluate=evaluate_crypto_trend_rotation,
)

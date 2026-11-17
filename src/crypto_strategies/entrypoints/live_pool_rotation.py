"""Entrypoint for the ``crypto_live_pool_rotation`` strategy profile."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math

from quant_platform_kit.common.strategy_contracts import (
    BudgetIntent,
    CallableStrategyEntrypoint,
    PositionTarget,
    StrategyContext,
    StrategyDecision,
)

from crypto_strategies.manifests import crypto_live_pool_rotation_manifest
from crypto_strategies.strategies.crypto_live_pool_rotation.rotation import (
    build_strategy_stop_evaluation,
    evaluate_held_trend_stops,
)

from ._metrics import (
    _merge_runtime_config,
    _require_market_data,
    _resolve_portfolio_snapshot,
    _resolve_state_helpers,
    _resolve_translator,
)


def _resolve_held_risk_symbols(
    ctx: StrategyContext,
    state: Mapping[str, object],
    *,
    trend_universe_symbols,
    get_symbol_trade_state_fn,
):
    held = {
        str(symbol).strip().upper()
        for symbol, payload in state.items()
        if isinstance(payload, Mapping)
        and payload.get("is_holding")
        and str(symbol).strip().upper() != "BTCUSDT"
    }
    snapshot = _resolve_portfolio_snapshot(ctx)
    candidate_symbols = {
        str(symbol).strip().upper()
        for symbol in trend_universe_symbols
        if str(symbol).strip().upper() != "BTCUSDT"
    }
    for position in getattr(snapshot, "positions", ()) or ():
        symbol = str(getattr(position, "symbol", "")).strip().upper()
        if symbol and symbol != "BTCUSDT":
            candidate_symbols.add(symbol)
        quantity = getattr(position, "quantity", 0.0)
        market_value = getattr(position, "market_value", 0.0)
        values = (quantity, market_value)
        if symbol and symbol != "BTCUSDT" and any(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) != 0.0
            for value in values
        ):
            held.add(symbol)
    for symbol in candidate_symbols:
        symbol_state = get_symbol_trade_state_fn(state, symbol)
        if isinstance(symbol_state, Mapping) and symbol_state.get("is_holding"):
            held.add(symbol)
    return tuple(sorted(held))


def evaluate_crypto_live_pool_rotation(ctx: StrategyContext) -> StrategyDecision:
    import crypto_strategies.entrypoints as entrypoints_pkg

    legacy_core, legacy_rotation = entrypoints_pkg._load_legacy_modules()
    config = _merge_runtime_config(ctx, crypto_live_pool_rotation_manifest.default_config)
    prices = _require_market_data(ctx, "market_prices")
    indicators_map = _require_market_data(ctx, "derived_indicators")
    btc_snapshot = _require_market_data(ctx, "benchmark_snapshot")
    account_metrics = entrypoints_pkg._resolve_account_metrics(ctx)
    trend_universe_symbols = list(_require_market_data(ctx, "universe_snapshot"))
    state = dict(ctx.state)
    working_state = deepcopy(state)
    translator = _resolve_translator(config)
    get_symbol_trade_state_fn, set_symbol_trade_state_fn = _resolve_state_helpers(config)

    budgets = legacy_core.compute_allocation_budgets(
        account_metrics["total_equity"],
        account_metrics["cash_usdt"],
        account_metrics.get("trend_value", 0.0),
        account_metrics.get("dca_value", 0.0),
    )

    selected_pool = legacy_rotation.resolve_authoritative_rotation_pool(
        working_state,
        trend_universe_symbols=trend_universe_symbols,
        trend_pool_size=config["trend_pool_size"],
        allow_refresh=bool(config.get("allow_rotation_refresh", True)),
        now_utc=config.get("now_utc"),
    )
    selected_candidates = legacy_core.select_rotation_weights(
        indicators_map,
        prices,
        btc_snapshot,
        selected_pool,
        config["rotation_top_n"],
        weight_mode=str(config.get("weight_mode", "inverse_vol")),
    )

    atr_multiplier = config.get("atr_multiplier", 2.5)
    held_risk_symbols = _resolve_held_risk_symbols(
        ctx,
        working_state,
        trend_universe_symbols=trend_universe_symbols,
        get_symbol_trade_state_fn=get_symbol_trade_state_fn,
    )
    sell_reasons, stop_input_blocked = evaluate_held_trend_stops(
        working_state,
        held_symbols=held_risk_symbols,
        prices=prices,
        indicators_map=indicators_map,
        selected_candidates=selected_candidates,
        atr_multiplier=atr_multiplier,
        get_symbol_trade_state_fn=get_symbol_trade_state_fn,
        set_symbol_trade_state_fn=set_symbol_trade_state_fn,
        translate_fn=translator,
    )

    if stop_input_blocked:
        eligible_buy_symbols, planned_trend_buys = (), {}
        positions = []
        budget_intents = ()
    else:
        eligible_buy_symbols, planned_trend_buys = legacy_rotation.plan_trend_buys(
            working_state,
            runtime_trend_universe={
                symbol: {"base_asset": symbol[:-4]} for symbol in trend_universe_symbols
            },
            selected_candidates=selected_candidates,
            trend_indicators=indicators_map,
            prices=prices,
            available_trend_buy_budget=float(budgets["trend_usdt_pool"]),
            allow_new_trend_entries=bool(config.get("allow_new_trend_entries", True)),
            get_symbol_trade_state_fn=get_symbol_trade_state_fn,
            allocate_trend_buy_budget_fn=legacy_core.allocate_trend_buy_budget,
        )
        positions = [
            PositionTarget(
                symbol="BTCUSDT",
                target_weight=float(budgets["btc_target_ratio"]),
                role="core",
            )
        ]
        trend_target_ratio = float(budgets["trend_target_ratio"])
        for symbol, payload in sorted(selected_candidates.items()):
            if symbol in sell_reasons:
                continue
            positions.append(
                PositionTarget(
                    symbol=symbol,
                    target_weight=trend_target_ratio * float(payload["weight"]),
                    role="trend_rotation",
                )
            )

        budget_intents = (
            BudgetIntent(
                name="btc_core_dca_pool",
                symbol="BTCUSDT",
                amount=float(budgets["dca_usdt_pool"]),
                purpose="btc_core_accumulation",
            ),
            BudgetIntent(
                name="trend_rotation_pool",
                amount=float(budgets["trend_usdt_pool"]),
                purpose="trend_rotation",
            ),
        )

    risk_flags: tuple[str, ...] = ()
    if not btc_snapshot.get("regime_on"):
        risk_flags += ("regime_off",)
    if not selected_candidates:
        risk_flags += ("no_trend_candidates",)

    diagnostics = {
        "trend_pool": tuple(selected_pool),
        "rotation_candidates": {
            symbol: {
                "weight": float(payload["weight"]),
                "relative_score": float(payload["relative_score"]),
                "abs_momentum": float(payload["abs_momentum"]),
            }
            for symbol, payload in selected_candidates.items()
        },
        "ranking_preview": tuple(selected_pool[: int(config["trend_pool_size"])]),
        "rotation_pool_source_version": working_state.get("rotation_pool_source_version"),
        "rotation_pool_source_as_of_date": working_state.get("rotation_pool_source_as_of_date"),
        "rotation_pool_last_month": working_state.get("rotation_pool_last_month"),
        "sell_reasons": sell_reasons,
        "eligible_buy_symbols": tuple(eligible_buy_symbols),
        "planned_trend_buys": {symbol: float(amount) for symbol, amount in planned_trend_buys.items()},
        "btc_base_order_usdt": float(legacy_core.get_dynamic_btc_base_order(account_metrics["total_equity"])),
        "btc_target_ratio": float(budgets["btc_target_ratio"]),
        "trend_target_ratio": float(budgets["trend_target_ratio"]),
        "artifact_contract": {
            "version": config.get("artifact_contract_version"),
            "max_age_days": config.get("artifact_max_age_days"),
            "acceptable_modes": tuple(config.get("artifact_acceptable_modes", ())),
            **dict(ctx.artifacts.get("trend_pool_contract", {})),
        },
    }
    decision = StrategyDecision(
        positions=tuple(positions),
        budgets=() if sell_reasons else budget_intents,
        risk_flags=risk_flags + (("rejected:strategy_stop_input",) if stop_input_blocked else ()),
        diagnostics=diagnostics,
    )
    if sell_reasons:
        decision = StrategyDecision(
            positions=(),
            budgets=(),
            risk_flags=decision.risk_flags,
            diagnostics=decision.diagnostics,
        )
    decision = entrypoints_pkg.apply_risk_gate(decision, ctx=ctx)
    member_assessment = decision.diagnostics["member_risk_assessment"]
    stop_outcome = "TRIGGERED" if sell_reasons else "CLEAR"
    stop_action_result = "BLOCKED" if stop_outcome == "TRIGGERED" else "NOT_REQUIRED"
    decision = StrategyDecision(
        positions=decision.positions,
        budgets=decision.budgets,
        risk_flags=decision.risk_flags,
        diagnostics={
            **dict(decision.diagnostics),
            "strategy_stop_evaluation": build_strategy_stop_evaluation(
                evaluated_at=member_assessment["evaluated_at"],
                decision_digest_sha256=member_assessment["decision_digest_sha256"],
                outcome=stop_outcome,
                action_result=stop_action_result,
            ),
        },
    )
    entrypoints_pkg.record_strategy_decision(
        ctx,
        decision,
        profile_id=crypto_live_pool_rotation_manifest.profile,
        domain=crypto_live_pool_rotation_manifest.domain,
    )
    return decision


crypto_live_pool_rotation_entrypoint = CallableStrategyEntrypoint(
    manifest=crypto_live_pool_rotation_manifest,
    _evaluate=evaluate_crypto_live_pool_rotation,
)

"""Entrypoint for the ``crypto_equity_combo`` strategy profile."""

from __future__ import annotations

from collections.abc import Mapping

from quant_platform_kit.common.strategy_contracts import (
    BudgetIntent,
    CallableStrategyEntrypoint,
    PositionTarget,
    StrategyContext,
    StrategyDecision,
)

from crypto_strategies.manifests import crypto_equity_combo_manifest
from crypto_strategies.strategies import crypto_equity_combo as _crypto_equity_combo

from ._metrics import (
    _merge_runtime_config,
    _require_market_data,
    _resolve_portfolio_snapshot,
    _resolve_state_helpers,
    _resolve_translator,
)


def evaluate_crypto_equity_combo(ctx: StrategyContext) -> StrategyDecision:
    import crypto_strategies.entrypoints as entrypoints_pkg

    legacy_core, legacy_rotation = entrypoints_pkg._load_legacy_modules()
    config = _merge_runtime_config(ctx, crypto_equity_combo_manifest.default_config)
    prices = _require_market_data(ctx, "market_prices")
    indicators_map = _require_market_data(ctx, "derived_indicators")
    benchmark_snapshot = _require_market_data(ctx, "benchmark_snapshot")
    portfolio = _resolve_portfolio_snapshot(ctx)
    account_metrics = entrypoints_pkg._resolve_account_metrics(ctx)
    universe_snapshot = list(_require_market_data(ctx, "universe_snapshot"))
    state = dict(ctx.state)
    translator = _resolve_translator(config)
    get_symbol_trade_state_fn, set_symbol_trade_state_fn = _resolve_state_helpers(config)

    weights, signal_desc, has_cash_residual, status_desc, metadata = _crypto_equity_combo.compute_signals(
        prices=prices,
        indicators_map=indicators_map,
        universe_snapshot=universe_snapshot,
        benchmark_snapshot=benchmark_snapshot,
        portfolio=portfolio,
        state=state,
        translator=translator,
        btc_weight=float(config.get("btc_weight", 0.30)),
        trend_weight=float(config.get("trend_weight", 0.70)),
        dynamic_mode=bool(config.get("dynamic_mode", True)),
        dynamic_regime_mode=str(config.get("dynamic_regime_mode", "legacy")),
        dynamic_regime_off_cut=float(config.get("dynamic_regime_off_cut", 0.50)),
        dynamic_hard_sma200_ratio=float(config.get("dynamic_hard_sma200_ratio", 0.97)),
        dynamic_hard_ma200_slope=float(config.get("dynamic_hard_ma200_slope", -0.015)),
        dynamic_soft_sma200_ratio=float(config.get("dynamic_soft_sma200_ratio", 1.05)),
        dynamic_hard_btc_weight=float(config.get("dynamic_hard_btc_weight", 0.30)),
        dynamic_hard_trend_weight=float(config.get("dynamic_hard_trend_weight", 0.0)),
        dynamic_soft_btc_weight=float(config.get("dynamic_soft_btc_weight", 0.45)),
        dynamic_soft_trend_weight=float(config.get("dynamic_soft_trend_weight", 0.15)),
        smart_multiplier_enabled=bool(config.get("smart_multiplier_enabled", True)),
        cycle_indicator_enabled=bool(config.get("cycle_indicator_enabled", True)),
        zscore_exit_enabled=bool(config.get("zscore_exit_enabled", True)),
        zscore_exit_parking_symbol=str(config.get("zscore_exit_parking_symbol", "USDT")),
        zscore_exit_risk_reduced_exposure=float(
            config.get("zscore_exit_risk_reduced_exposure", 0.50)
        ),
        zscore_exit_risk_off_exposure=float(config.get("zscore_exit_risk_off_exposure", 0.25)),
        zscore_exit_allow_outside_execution_window=bool(
            config.get("zscore_exit_allow_outside_execution_window", True)
        ),
        trend_pool_size=int(config.get("trend_pool_size", 5)),
        rotation_top_n=int(config.get("rotation_top_n", 2)),
        weight_mode=str(config.get("weight_mode", "inverse_vol")),
        allow_rotation_refresh=bool(config.get("allow_rotation_refresh", True)),
        circuit_breaker_enabled=bool(config.get("circuit_breaker_enabled", True)),
        btc_drawdown_threshold=float(config.get("btc_drawdown_threshold", 0.30)),
        vol_scaling_enabled=bool(config.get("vol_scaling_enabled", True)),
        target_vol=float(config.get("target_vol", 0.40)),
        max_leverage=float(config.get("max_leverage", 1.0)),
    )

    positions: list[PositionTarget] = []
    if weights:
        for symbol, weight in sorted(weights.items()):
            positions.append(
                PositionTarget(
                    symbol=symbol,
                    target_weight=float(weight),
                    role=("core" if symbol == "BTCUSDT" else "trend_rotation"),
                )
            )

    btc_target_ratio = float(weights.get("BTCUSDT", 0.0))
    trend_target_ratio = float(sum(weight for symbol, weight in weights.items() if symbol != "BTCUSDT"))
    total_equity = float(account_metrics["total_equity"])
    cash_usdt = max(0.0, float(account_metrics["cash_usdt"]))
    trend_value = max(0.0, float(account_metrics.get("trend_value", 0.0)))
    dca_value = max(0.0, float(account_metrics.get("dca_value", 0.0)))
    trend_usdt_pool = max(0.0, min(cash_usdt, (total_equity * trend_target_ratio) - trend_value))
    remaining_cash = max(0.0, cash_usdt - trend_usdt_pool)
    dca_usdt_pool = max(0.0, min(remaining_cash, (total_equity * btc_target_ratio) - dca_value))
    btc_base_order_usdt = float(legacy_core.get_dynamic_btc_base_order(total_equity))
    trend_metadata = metadata.get("trend_leg", {}) if isinstance(metadata.get("trend_leg"), Mapping) else {}
    selected_candidates = {
        str(symbol): {
            "weight": float(payload.get("weight", 0.0)),
            "relative_score": float(payload.get("relative_score", 0.0)),
            "abs_momentum": float(payload.get("abs_momentum", 0.0)),
        }
        for symbol, payload in dict(trend_metadata.get("rotation_candidates", {})).items()
    }
    runtime_trend_universe = {symbol: {"base_asset": symbol[:-4]} for symbol in universe_snapshot}
    sell_reasons: dict[str, str] = {}
    atr_multiplier = float(config.get("atr_multiplier", 2.5))
    for symbol in universe_snapshot:
        curr_price = prices.get(symbol)
        if curr_price is None:
            continue
        reason = legacy_rotation.get_trend_sell_reason(
            state,
            symbol,
            curr_price,
            indicators_map.get(symbol),
            selected_candidates,
            atr_multiplier,
            get_symbol_trade_state_fn=get_symbol_trade_state_fn,
            set_symbol_trade_state_fn=set_symbol_trade_state_fn,
            translate_fn=translator,
        )
        if reason:
            sell_reasons[symbol] = str(reason)

    eligible_buy_symbols, planned_trend_buys = legacy_rotation.plan_trend_buys(
        state,
        runtime_trend_universe=runtime_trend_universe,
        selected_candidates=selected_candidates,
        trend_indicators=indicators_map,
        prices=prices,
        available_trend_buy_budget=trend_usdt_pool,
        allow_new_trend_entries=bool(config.get("allow_new_trend_entries", True)),
        get_symbol_trade_state_fn=get_symbol_trade_state_fn,
        allocate_trend_buy_budget_fn=legacy_core.allocate_trend_buy_budget,
    )

    budget_intents = (
        BudgetIntent(
            name="btc_core_dca_pool",
            symbol="BTCUSDT",
            amount=dca_usdt_pool,
            purpose="btc_core_accumulation",
        ),
        BudgetIntent(
            name="trend_rotation_pool",
            amount=trend_usdt_pool,
            purpose="trend_rotation",
        ),
    )

    risk_flags: tuple[str, ...] = ()
    if metadata.get("regime_off"):
        risk_flags += ("regime_off",)
    if has_cash_residual:
        risk_flags += ("cash_residual",)
    if not selected_candidates:
        risk_flags += ("no_trend_candidates",)
    if not weights:
        risk_flags += ("no_positions",)

    diagnostics = {
        "signal_desc": signal_desc,
        "status_desc": status_desc,
        "metadata": metadata,
        "managed_symbols": metadata.get("managed_symbols", ()),
        "profile": metadata.get("profile"),
        "trend_pool": tuple(trend_metadata.get("trend_pool", ())),
        "rotation_candidates": selected_candidates,
        "ranking_preview": tuple(trend_metadata.get("ranking_preview", ())),
        "rotation_pool_source_version": trend_metadata.get("rotation_pool_source_version"),
        "rotation_pool_source_as_of_date": trend_metadata.get("rotation_pool_source_as_of_date"),
        "rotation_pool_last_month": trend_metadata.get("rotation_pool_last_month"),
        "sell_reasons": sell_reasons,
        "eligible_buy_symbols": tuple(eligible_buy_symbols),
        "planned_trend_buys": {symbol: float(amount) for symbol, amount in planned_trend_buys.items()},
        "btc_base_order_usdt": btc_base_order_usdt,
        "btc_target_ratio": btc_target_ratio,
        "trend_target_ratio": trend_target_ratio,
        "artifact_contract": {
            "version": config.get("artifact_contract_version"),
            "max_age_days": config.get("artifact_max_age_days"),
            "acceptable_modes": tuple(config.get("artifact_acceptable_modes", ())),
            **dict(ctx.artifacts.get("trend_pool_contract", {})),
        },
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
        profile_id=crypto_equity_combo_manifest.profile,
        domain=crypto_equity_combo_manifest.domain,
    )
    return decision


crypto_equity_combo_entrypoint = CallableStrategyEntrypoint(
    manifest=crypto_equity_combo_manifest,
    _evaluate=evaluate_crypto_equity_combo,
)

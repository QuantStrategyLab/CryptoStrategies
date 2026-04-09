from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy

from quant_platform_kit.strategy_contracts import (
    BudgetIntent,
    CallableStrategyEntrypoint,
    PositionTarget,
    StrategyContext,
    StrategyDecision,
)

from crypto_strategies.manifests import crypto_leader_rotation_manifest


"""Unified crypto strategy entrypoints built on top of legacy core/rotation modules."""


def _merge_runtime_config(ctx: StrategyContext) -> dict[str, object]:
    config = dict(crypto_leader_rotation_manifest.default_config)
    config.update(dict(ctx.runtime_config))
    return config


def _require_market_data(ctx: StrategyContext, key: str):
    if key not in ctx.market_data:
        raise ValueError(f"StrategyContext.market_data missing required key: {key}")
    return ctx.market_data[key]


def _resolve_portfolio_snapshot(ctx: StrategyContext):
    if ctx.portfolio is not None:
        return ctx.portfolio
    return _require_market_data(ctx, "portfolio_snapshot")


def _resolve_account_metrics(ctx: StrategyContext) -> dict[str, float]:
    snapshot = _resolve_portfolio_snapshot(ctx)
    metadata = dict(getattr(snapshot, "metadata", {}) or {})
    embedded_metrics = metadata.get("account_metrics")
    if isinstance(embedded_metrics, Mapping):
        return {
            "total_equity": float(embedded_metrics["total_equity"]),
            "cash_usdt": float(embedded_metrics["cash_usdt"]),
            "trend_value": float(embedded_metrics["trend_value"]),
            "dca_value": float(embedded_metrics["dca_value"]),
        }

    cash_usdt = metadata.get("cash_available_for_trading")
    if cash_usdt is None:
        cash_usdt = getattr(snapshot, "buying_power", None)
    if cash_usdt is None:
        cash_usdt = getattr(snapshot, "cash_balance", None)
    if cash_usdt is None:
        cash_usdt = 0.0

    dca_value = 0.0
    trend_value = 0.0
    for position in getattr(snapshot, "positions", ()) or ():
        symbol = str(getattr(position, "symbol", "")).strip().upper()
        market_value = float(getattr(position, "market_value", 0.0) or 0.0)
        if symbol == "BTCUSDT":
            dca_value += market_value
        else:
            trend_value += market_value

    return {
        "total_equity": float(snapshot.total_equity),
        "cash_usdt": float(cash_usdt),
        "trend_value": float(metadata.get("trend_value", trend_value)),
        "dca_value": float(metadata.get("dca_value", dca_value)),
    }


def _resolve_translator(config: Mapping[str, object]) -> Callable[[str], str]:
    translator = config.get("translator")
    if callable(translator):
        return translator
    return lambda key, **_kwargs: str(key)


def _default_symbol_state() -> dict[str, object]:
    return {
        "is_holding": False,
        "entry_price": 0.0,
        "highest_price": 0.0,
    }


def _resolve_state_helpers(config: Mapping[str, object]):
    get_symbol_trade_state_fn = config.get("get_symbol_trade_state_fn")
    set_symbol_trade_state_fn = config.get("set_symbol_trade_state_fn")

    if callable(get_symbol_trade_state_fn) and callable(set_symbol_trade_state_fn):
        return get_symbol_trade_state_fn, set_symbol_trade_state_fn

    def _get_symbol_trade_state(state, symbol):
        value = state.get(symbol)
        if not isinstance(value, Mapping):
            return _default_symbol_state()
        merged = _default_symbol_state()
        merged.update(dict(value))
        return merged

    def _set_symbol_trade_state(state, symbol, symbol_state):
        state[symbol] = dict(symbol_state)

    return _get_symbol_trade_state, _set_symbol_trade_state


def _load_legacy_modules():
    from crypto_strategies.strategies.crypto_leader_rotation import core as legacy_core
    from crypto_strategies.strategies.crypto_leader_rotation import rotation as legacy_rotation

    return legacy_core, legacy_rotation


def evaluate_crypto_leader_rotation(ctx: StrategyContext) -> StrategyDecision:
    legacy_core, legacy_rotation = _load_legacy_modules()
    config = _merge_runtime_config(ctx)
    prices = _require_market_data(ctx, "market_prices")
    indicators_map = _require_market_data(ctx, "derived_indicators")
    btc_snapshot = _require_market_data(ctx, "benchmark_snapshot")
    account_metrics = _resolve_account_metrics(ctx)
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

    selected_pool, ranking = legacy_rotation.refresh_rotation_pool(
        working_state,
        indicators_map,
        btc_snapshot,
        trend_universe_symbols=trend_universe_symbols,
        trend_pool_size=config["trend_pool_size"],
        build_stable_quality_pool_fn=lambda indicators, btc, previous_pool: legacy_core.build_stable_quality_pool(
            indicators,
            btc,
            previous_pool,
            pool_size=config["trend_pool_size"],
            min_history_days=config["min_history_days"],
            min_avg_quote_vol_180=config["min_avg_quote_vol_180"],
            membership_bonus=config["membership_bonus"],
        ),
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

    sell_reasons: dict[str, str] = {}
    atr_multiplier = float(config.get("atr_multiplier", 2.5))
    for symbol in trend_universe_symbols:
        curr_price = prices.get(symbol)
        if curr_price is None:
            continue
        reason = legacy_rotation.get_trend_sell_reason(
            working_state,
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
        working_state,
        runtime_trend_universe={symbol: {"base_asset": symbol[:-4]} for symbol in trend_universe_symbols},
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
        "ranking_preview": tuple(item["symbol"] for item in ranking[: int(config["trend_pool_size"])]),
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
    return StrategyDecision(
        positions=tuple(positions),
        budgets=budget_intents,
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )


crypto_leader_rotation_entrypoint = CallableStrategyEntrypoint(
    manifest=crypto_leader_rotation_manifest,
    _evaluate=evaluate_crypto_leader_rotation,
)


__all__ = ["crypto_leader_rotation_entrypoint", "evaluate_crypto_leader_rotation"]

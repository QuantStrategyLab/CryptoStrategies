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

from crypto_strategies.manifests import (
    crypto_btc_dca_manifest,
    crypto_equity_combo_manifest,
    crypto_live_pool_rotation_manifest,
    crypto_trend_rotation_manifest,
)

from ._common import apply_risk_gate, record_strategy_decision


"""Unified crypto strategy entrypoints built on top of legacy core/rotation modules."""


def _merge_runtime_config(ctx: StrategyContext, manifest_config: Mapping[str, object]) -> dict[str, object]:
    config = dict(manifest_config)
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
    from crypto_strategies.strategies.crypto_live_pool_rotation import core as legacy_core
    from crypto_strategies.strategies.crypto_live_pool_rotation import rotation as legacy_rotation

    return legacy_core, legacy_rotation


def evaluate_crypto_live_pool_rotation(ctx: StrategyContext) -> StrategyDecision:
    legacy_core, legacy_rotation = _load_legacy_modules()
    config = _merge_runtime_config(ctx, crypto_live_pool_rotation_manifest.default_config)
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
        budgets=budget_intents,
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision)
    record_strategy_decision(
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


# ---------------------------------------------------------------------------
# crypto_btc_dca entrypoint
# ---------------------------------------------------------------------------

def evaluate_crypto_btc_dca(ctx: StrategyContext) -> StrategyDecision:
    from crypto_strategies.strategies.crypto_btc_dca import compute_signals

    config = _merge_runtime_config(ctx, crypto_btc_dca_manifest.default_config)
    prices = _require_market_data(ctx, "market_prices")
    portfolio = _resolve_portfolio_snapshot(ctx)
    account_metrics = _resolve_account_metrics(ctx)
    total_equity = account_metrics["total_equity"]
    derived_indicators = ctx.market_data.get("derived_indicators")
    translator = _resolve_translator(config)

    result = compute_signals(
        prices=prices,
        portfolio=portfolio,
        total_equity=total_equity,
        state=dict(ctx.state),
        derived_indicators=derived_indicators,
        translator=translator,
        **config,
    )

    btc_target_ratio = float(result.get("btc_target_ratio", 0.0))
    metadata = result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {}

    positions = [
        PositionTarget(
            symbol="BTCUSDT",
            target_weight=btc_target_ratio,
            role="core",
        ),
    ]

    budget_intents = (
        BudgetIntent(
            name="btc_dca_pool",
            symbol="BTCUSDT",
            amount=total_equity * btc_target_ratio,
            purpose="btc_core_accumulation",
        ),
    )

    risk_flags: tuple[str, ...] = ()
    if btc_target_ratio <= 0.0:
        risk_flags += ("no_btc_allocation",)
    if not metadata.get("actionable", True):
        risk_flags += ("no_execute",)

    diagnostics = {
        "btc_target_ratio": btc_target_ratio,
        "total_equity": total_equity,
        "profile": result.get("profile"),
        "signal_description": metadata.get("signal_description", ""),
        "status_description": metadata.get("status_description", ""),
        "regime": metadata.get("regime", "ordinary_dca"),
        "multiplier": metadata.get("multiplier", 1.0),
        "smart_multiplier_enabled": metadata.get("smart_multiplier_enabled", True),
        "planned_investment_usd": metadata.get("planned_investment_usd", 0.0),
        "in_execution_window": metadata.get("in_execution_window", True),
        "zscore_exit": metadata.get("zscore_exit", {}),
        "ahr999": metadata.get("ahr999", float("nan")),
        "mayer_multiple": metadata.get("mayer_multiple", float("nan")),
    }

    decision = StrategyDecision(
        positions=tuple(positions),
        budgets=budget_intents,
        risk_flags=risk_flags,
        diagnostics=diagnostics,
    )
    decision = apply_risk_gate(decision, max_single_weight=0.50)
    record_strategy_decision(
        ctx,
        decision,
        profile_id=crypto_btc_dca_manifest.profile,
        domain=crypto_btc_dca_manifest.domain,
    )
    return decision


crypto_btc_dca_entrypoint = CallableStrategyEntrypoint(
    manifest=crypto_btc_dca_manifest,
    _evaluate=evaluate_crypto_btc_dca,
)


# ---------------------------------------------------------------------------
# crypto_trend_rotation entrypoint
# ---------------------------------------------------------------------------

def evaluate_crypto_trend_rotation(ctx: StrategyContext) -> StrategyDecision:
    from crypto_strategies.strategies.crypto_trend_rotation import compute_signals

    config = _merge_runtime_config(ctx, crypto_trend_rotation_manifest.default_config)
    feature_snapshot = _require_market_data(ctx, "derived_indicators")
    prices = _require_market_data(ctx, "market_prices")
    translator = _resolve_translator(config)

    # Build a feature_snapshot from indicators_map
    import pandas as pd
    rows = []
    for symbol, indicators in feature_snapshot.items():
        row = dict(indicators)
        row["symbol"] = symbol
        rows.append(row)
    feature_frame = pd.DataFrame(rows) if rows else pd.DataFrame()

    weights, signal_desc, is_emergency, debug_str, metadata = compute_signals(
        feature_snapshot=feature_frame,
        current_holdings=list(prices.keys()),
        translator=translator,
        trend_pool_size=int(config.get("trend_pool_size", 5)),
        rotation_top_n=int(config.get("rotation_top_n", 2)),
        weight_mode=str(config.get("weight_mode", "inverse_vol")),
        allow_rotation_refresh=bool(config.get("allow_rotation_refresh", True)),
        atr_multiplier=float(config.get("atr_multiplier", 2.5)),
        circuit_breaker_enabled=bool(config.get("circuit_breaker_enabled", True)),
        btc_drawdown_threshold=float(config.get("btc_drawdown_threshold", 0.30)),
        vol_scaling_enabled=bool(config.get("vol_scaling_enabled", True)),
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
    decision = apply_risk_gate(decision, max_single_weight=0.30)
    record_strategy_decision(
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


# ---------------------------------------------------------------------------
# crypto_equity_combo entrypoint
# ---------------------------------------------------------------------------

def evaluate_crypto_equity_combo(ctx: StrategyContext) -> StrategyDecision:
    from crypto_strategies.strategies.crypto_equity_combo import compute_signals

    legacy_core, legacy_rotation = _load_legacy_modules()
    config = _merge_runtime_config(ctx, crypto_equity_combo_manifest.default_config)
    prices = _require_market_data(ctx, "market_prices")
    indicators_map = _require_market_data(ctx, "derived_indicators")
    benchmark_snapshot = _require_market_data(ctx, "benchmark_snapshot")
    portfolio = _resolve_portfolio_snapshot(ctx)
    account_metrics = _resolve_account_metrics(ctx)
    universe_snapshot = list(_require_market_data(ctx, "universe_snapshot"))
    state = dict(ctx.state)
    translator = _resolve_translator(config)
    get_symbol_trade_state_fn, set_symbol_trade_state_fn = _resolve_state_helpers(config)

    weights, signal_desc, has_cash_residual, status_desc, metadata = compute_signals(
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
    decision = apply_risk_gate(decision, max_single_weight=0.30)
    record_strategy_decision(
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


__all__ = [
    "crypto_live_pool_rotation_entrypoint",
    "evaluate_crypto_live_pool_rotation",
    "crypto_btc_dca_entrypoint",
    "evaluate_crypto_btc_dca",
    "crypto_trend_rotation_entrypoint",
    "evaluate_crypto_trend_rotation",
    "crypto_equity_combo_entrypoint",
    "evaluate_crypto_equity_combo",
]

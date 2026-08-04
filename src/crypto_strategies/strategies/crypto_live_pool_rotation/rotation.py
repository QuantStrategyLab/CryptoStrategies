"""Strategy-level trend rotation rules for the crypto rotation profile."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import math


_STRATEGY_STOP_POLICY_ID = "crypto_live_pool_rotation.executable_stop"
_STRATEGY_STOP_POLICY_VERSION = "v1"


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def evaluate_held_trend_stops(
    state,
    *,
    held_symbols,
    prices,
    indicators_map,
    selected_candidates,
    atr_multiplier,
    get_symbol_trade_state_fn,
    set_symbol_trade_state_fn,
    translate_fn,
):
    """Evaluate every held risk symbol; incomplete inputs block CLEAR."""
    sell_reasons = {}
    input_blocked = False
    for symbol in _normalize_symbol_list(held_symbols):
        symbol_state = get_symbol_trade_state_fn(state, symbol)
        persisted_symbol_state = state.get(symbol) if isinstance(state, Mapping) else None
        indicators = indicators_map.get(symbol)
        curr_price = _finite_number(prices.get(symbol))
        atr = _finite_number(indicators.get("atr14")) if isinstance(indicators, Mapping) else None
        sma60 = _finite_number(indicators.get("sma60")) if isinstance(indicators, Mapping) else None
        entry_price = _finite_number(symbol_state.get("entry_price"))
        highest_price = (
            _finite_number(persisted_symbol_state.get("highest_price"))
            if isinstance(persisted_symbol_state, Mapping)
            and "highest_price" in persisted_symbol_state
            else None
        )
        if (
            not symbol_state.get("is_holding")
            or curr_price is None
            or atr is None
            or sma60 is None
            or entry_price is None
            or entry_price <= 0.0
            or highest_price is None
            or highest_price <= 0.0
            or highest_price < entry_price
        ):
            input_blocked = True
            sell_reasons[symbol] = translate_fn("trend_sell_reason_missing_stop_input")
            continue
        reason = get_trend_sell_reason(
            state,
            symbol,
            curr_price,
            indicators,
            selected_candidates,
            atr_multiplier,
            get_symbol_trade_state_fn=get_symbol_trade_state_fn,
            set_symbol_trade_state_fn=set_symbol_trade_state_fn,
            translate_fn=translate_fn,
        )
        if reason:
            sell_reasons[symbol] = str(reason)
    return sell_reasons, input_blocked


def build_strategy_stop_evaluation(
    *,
    evaluated_at,
    decision_digest_sha256,
    outcome,
    action_result,
):
    return {
        "evaluated": True,
        "policy_id": _STRATEGY_STOP_POLICY_ID,
        "policy_version": _STRATEGY_STOP_POLICY_VERSION,
        "evaluated_at": evaluated_at,
        "decision_digest_sha256": decision_digest_sha256,
        "outcome": outcome,
        "action_result": action_result,
    }


def _normalize_symbol_list(symbols):
    normalized = []
    seen = set()
    for value in symbols or ():
        symbol = str(value or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        normalized.append(symbol)
        seen.add(symbol)
    return normalized


def _set_rotation_pool_lock(state, *, source_version, source_as_of_date, now_utc):
    locked_version = str(source_version or "").strip()
    locked_as_of_date = str(source_as_of_date or "").strip()
    state["rotation_pool_source_version"] = locked_version
    state["rotation_pool_source_as_of_date"] = locked_as_of_date
    if locked_as_of_date:
        state["rotation_pool_last_month"] = locked_as_of_date[:7]
    else:
        state["rotation_pool_last_month"] = (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m")


def resolve_authoritative_rotation_pool(
    state,
    *,
    trend_universe_symbols,
    trend_pool_size,
    allow_refresh=True,
    now_utc=None,
):
    now_utc = now_utc or datetime.now(timezone.utc)
    upstream_pool = _normalize_symbol_list(trend_universe_symbols)
    available_symbols = set(upstream_pool)
    cached_pool = [
        symbol
        for symbol in _normalize_symbol_list(state.get("rotation_pool_symbols", []))
        if not available_symbols or symbol in available_symbols
    ]
    current_source_version = str(state.get("trend_pool_version", "")).strip()
    current_source_as_of_date = str(state.get("trend_pool_as_of_date", "")).strip()

    if not allow_refresh:
        try:
            fallback_size = max(0, int(trend_pool_size))
        except Exception:
            fallback_size = len(upstream_pool)
        selected_pool = cached_pool or upstream_pool[:fallback_size]
    elif upstream_pool:
        selected_pool = upstream_pool
    else:
        selected_pool = cached_pool

    _set_rotation_pool_lock(
        state,
        source_version=current_source_version,
        source_as_of_date=current_source_as_of_date,
        now_utc=now_utc,
    )
    state["rotation_pool_symbols"] = selected_pool
    return selected_pool


def refresh_rotation_pool(
    state,
    indicators_map,
    btc_snapshot,
    *,
    trend_universe_symbols,
    trend_pool_size,
    build_stable_quality_pool_fn,
    allow_refresh=True,
    now_utc=None,
):
    now_utc = now_utc or datetime.now(timezone.utc)
    trend_universe_symbols = list(trend_universe_symbols)
    available_symbols = set(trend_universe_symbols)
    cached_pool = [symbol for symbol in state.get("rotation_pool_symbols", []) if symbol in available_symbols]
    current_source_version = str(state.get("trend_pool_version", "")).strip()
    current_source_as_of_date = str(state.get("trend_pool_as_of_date", "")).strip()
    locked_source_version = str(state.get("rotation_pool_source_version", "")).strip()
    locked_source_as_of_date = str(state.get("rotation_pool_source_as_of_date", "")).strip()
    current_source_month = current_source_as_of_date[:7] if current_source_as_of_date else ""
    legacy_locked_month = str(state.get("rotation_pool_last_month", "")).strip()

    if not allow_refresh and cached_pool:
        _set_rotation_pool_lock(
            state,
            source_version=current_source_version,
            source_as_of_date=current_source_as_of_date,
            now_utc=now_utc,
        )
        state["rotation_pool_symbols"] = cached_pool
        return cached_pool, []

    if (
        cached_pool
        and (locked_source_version or locked_source_as_of_date)
        and locked_source_version == current_source_version
        and locked_source_as_of_date == current_source_as_of_date
    ):
        return cached_pool, []

    if (
        cached_pool
        and not locked_source_version
        and not locked_source_as_of_date
        and legacy_locked_month
        and current_source_month
        and legacy_locked_month == current_source_month
    ):
        _set_rotation_pool_lock(
            state,
            source_version=current_source_version,
            source_as_of_date=current_source_as_of_date,
            now_utc=now_utc,
        )
        state["rotation_pool_symbols"] = cached_pool
        return cached_pool, []

    selected_pool, ranking = build_stable_quality_pool_fn(
        indicators_map,
        btc_snapshot,
        set(cached_pool),
    )
    if selected_pool:
        _set_rotation_pool_lock(
            state,
            source_version=current_source_version,
            source_as_of_date=current_source_as_of_date,
            now_utc=now_utc,
        )
        state["rotation_pool_symbols"] = selected_pool
        return selected_pool, ranking

    fallback_pool = cached_pool if cached_pool else trend_universe_symbols[:trend_pool_size]
    _set_rotation_pool_lock(
        state,
        source_version=current_source_version,
        source_as_of_date=current_source_as_of_date,
        now_utc=now_utc,
    )
    state["rotation_pool_symbols"] = fallback_pool
    return fallback_pool, []


def get_trend_sell_reason(
    state,
    symbol,
    curr_price,
    indicators,
    selected_candidates,
    atr_multiplier,
    *,
    get_symbol_trade_state_fn,
    set_symbol_trade_state_fn,
    translate_fn,
):
    symbol_state = get_symbol_trade_state_fn(state, symbol)
    if not symbol_state["is_holding"]:
        return ""

    sell_reason = ""
    stop_price = None
    if not indicators:
        sell_reason = translate_fn("trend_sell_reason_missing_indicators")
    else:
        symbol_state["highest_price"] = max(symbol_state["highest_price"], curr_price)
        set_symbol_trade_state_fn(state, symbol, symbol_state)
        stop_price = symbol_state["highest_price"] - (atr_multiplier * indicators["atr14"])

    if symbol not in selected_candidates and not sell_reason:
        sell_reason = translate_fn("trend_sell_reason_rotated_out")
    elif indicators and curr_price < indicators["sma60"]:
        sell_reason = translate_fn("trend_sell_reason_below_sma60")
    elif stop_price is not None and curr_price < stop_price:
        sell_reason = translate_fn("trend_sell_reason_atr_stop", stop_price=stop_price)
    return sell_reason


def plan_trend_buys(
    state,
    runtime_trend_universe,
    selected_candidates,
    trend_indicators,
    prices,
    available_trend_buy_budget,
    allow_new_trend_entries,
    *,
    get_symbol_trade_state_fn,
    allocate_trend_buy_budget_fn,
):
    eligible_buy_symbols = []
    for symbol in runtime_trend_universe:
        if get_symbol_trade_state_fn(state, symbol)["is_holding"]:
            continue
        curr_price = prices[symbol]
        indicators = trend_indicators.get(symbol)
        candidate_meta = selected_candidates.get(symbol)
        can_open_new_position = (
            allow_new_trend_entries
            and indicators
            and candidate_meta
            and curr_price > indicators["sma20"]
            and curr_price > indicators["sma60"]
            and curr_price > indicators["sma200"]
        )
        if can_open_new_position:
            eligible_buy_symbols.append(symbol)

    planned_trend_buys = allocate_trend_buy_budget_fn(
        selected_candidates,
        eligible_buy_symbols,
        available_trend_buy_budget,
    )
    return eligible_buy_symbols, planned_trend_buys

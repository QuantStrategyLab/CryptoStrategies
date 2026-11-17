"""Entrypoint for the ``crypto_btc_dca`` strategy profile."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from pandas.api.types import is_bool
from quant_platform_kit.common.strategy_contracts import (
    BudgetIntent,
    CallableStrategyEntrypoint,
    PositionTarget,
    StrategyContext,
    StrategyDecision,
)

from crypto_strategies.manifests import crypto_btc_dca_manifest
from crypto_strategies.strategies import crypto_btc_dca as _crypto_btc_dca
from crypto_strategies.strategies.crypto_btc_dca import (
    BtcDcaPlanConfig,
    _cycle_metrics_from_payload,
    _find_zscore_exit_payload,
    _indicator_from_payload,
    _resolve_indicator_payload,
)

from ._metrics import (
    _merge_runtime_config,
    _require_market_data,
    _resolve_portfolio_snapshot,
    _resolve_translator,
)

_LEGACY_RATIO_DEFAULTS: dict[str, float] = {
    "target_ratio_min": 0.0,
    "target_ratio_max": 0.65,
    "ratio_base": 0.14,
    "ratio_scale": 0.16,
    "equity_normalizer": 10000.0,
}

_PLATFORM_FIELDS: frozenset[str] = frozenset({
    "translator",
    "now_utc",
    "allow_new_trend_entries",
    "allow_rotation_refresh",
    "get_symbol_trade_state_fn",
    "set_symbol_trade_state_fn",
})

_SCHEDULE_BOUNDS: tuple[tuple[str, int, int], ...] = (
    ("monthly_day", 1, 31),
    ("monthly_window_calendar_days", 1, 31),
    ("weekly_day", 0, 6),
    ("weekly_window_calendar_days", 1, 7),
    ("quarterly_day", 1, 31),
    ("quarterly_window_calendar_days", 1, 31),
)


def _finite_nonnegative(value: object, *, error_message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(error_message)
    return float(value)


def _validate_btc_runtime_config(
    config: Mapping[str, object],
    *,
    as_of: object,
    total_equity: float,
) -> BtcDcaPlanConfig:
    for key, expected in _LEGACY_RATIO_DEFAULTS.items():
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != expected:
            raise ValueError(f"unsupported BTC legacy parameter: {key}")

    plan_kwarg_names = BtcDcaPlanConfig.supported_kwarg_names()
    supported_fields = set(plan_kwarg_names) | {"zscore_exit_context"}
    if set(config) - supported_fields - set(_LEGACY_RATIO_DEFAULTS) - _PLATFORM_FIELDS:
        raise ValueError("unsupported BTC runtime configuration")
    if as_of is None:
        raise ValueError("BTC strategy as_of is required")
    if not math.isfinite(total_equity) or total_equity <= 0:
        raise ValueError("BTC strategy total_equity must be positive and finite")

    for key in ("base_investment_usd", "max_investment_usd", "cash_reserve_usd", "min_investment_usd"):
        value = config.get(key)
        if value is None and key == "max_investment_usd":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid BTC investment configuration: {key}")

    for key, default in crypto_btc_dca_manifest.default_config.items():
        if key in _LEGACY_RATIO_DEFAULTS or isinstance(default, bool) or not isinstance(default, (int, float)):
            continue
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid BTC numeric configuration: {key}")

    for key, low, high in _SCHEDULE_BOUNDS:
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"invalid BTC schedule configuration: {key}")

    months = config.get("quarterly_months")
    if isinstance(months, str):
        months = months.replace(";", ",").split(",")
    if not isinstance(months, (list, tuple)) or not months:
        raise ValueError("invalid BTC quarterly months")
    parsed_months = []
    for month in months:
        if isinstance(month, bool) or not (
            isinstance(month, int) or (isinstance(month, str) and month.strip().isdigit())
        ):
            raise ValueError("invalid BTC quarterly months")
        parsed_months.append(int(month))
    if any(not 1 <= month <= 12 for month in parsed_months) or len(set(parsed_months)) != len(parsed_months):
        raise ValueError("invalid BTC quarterly months")

    for key in ("zscore_exit_risk_reduced_exposure", "zscore_exit_risk_off_exposure"):
        if not 0.0 <= config[key] <= 1.0:
            raise ValueError(f"invalid BTC exit exposure: {key}")
    if not isinstance(config.get("zscore_exit_allow_outside_execution_window"), bool):
        raise ValueError("invalid BTC exit window configuration")
    if str(config.get("signal_symbol", "BTCUSDT")).strip().upper() != "BTCUSDT":
        raise ValueError("unsupported BTC signal symbol")
    for key in ("smart_multiplier_enabled", "cycle_indicator_enabled", "zscore_exit_enabled"):
        if not isinstance(config.get(key), bool):
            raise ValueError(f"invalid BTC boolean configuration: {key}")

    return BtcDcaPlanConfig.from_kwargs(
        **{key: value for key, value in config.items() if key in plan_kwarg_names}
    )


def _validate_btc_portfolio_and_prices(
    portfolio: Any,
    prices: object,
    *,
    total_equity: float,
) -> Mapping[str, object]:
    snapshot_equity = _finite_nonnegative(
        getattr(portfolio, "total_equity", None), error_message="invalid BTC portfolio snapshot"
    )
    available = _finite_nonnegative(
        getattr(portfolio, "buying_power", None), error_message="invalid BTC portfolio snapshot"
    )
    cash_balance = _finite_nonnegative(
        getattr(portfolio, "cash_balance", None), error_message="invalid BTC portfolio snapshot"
    )
    if not math.isclose(snapshot_equity, total_equity, rel_tol=1e-9, abs_tol=1e-6) or available > cash_balance:
        raise ValueError("inconsistent BTC portfolio snapshot")
    if not isinstance(prices, Mapping):
        raise ValueError("invalid BTC market prices")
    btc_price = _finite_nonnegative(prices.get("BTCUSDT"), error_message="invalid BTC market prices")
    if btc_price <= 0:
        raise ValueError("invalid BTC market prices")

    btc_market_value = 0.0
    btc_positions = 0
    for position in getattr(portfolio, "positions", ()) or ():
        if str(getattr(position, "symbol", "")).strip().upper() != "BTCUSDT":
            continue
        btc_positions += 1
        quantity = _finite_nonnegative(
            getattr(position, "quantity", None), error_message="invalid BTC portfolio snapshot"
        )
        value = _finite_nonnegative(
            getattr(position, "market_value", None), error_message="invalid BTC portfolio snapshot"
        )
        if (quantity == 0) != (value == 0):
            raise ValueError("inconsistent BTC portfolio position")
        if not math.isclose(quantity * btc_price, value, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("inconsistent BTC portfolio position")
        btc_market_value += value
    if btc_positions > 1 or btc_market_value > total_equity:
        raise ValueError("inconsistent BTC portfolio position")

    portfolio_metadata = getattr(portfolio, "metadata", {}) or {}
    if not isinstance(portfolio_metadata, Mapping):
        raise ValueError("invalid BTC portfolio metadata")
    account_metadata = portfolio_metadata.get("account_metrics")
    for source in (portfolio_metadata, account_metadata):
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise ValueError("invalid BTC portfolio metadata")
        if "dca_value" in source and not math.isclose(
            _finite_nonnegative(source["dca_value"], error_message="invalid BTC portfolio snapshot"),
            btc_market_value,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise ValueError("inconsistent BTC portfolio position")
    return portfolio_metadata


def _validate_btc_smart_inputs(
    config: Mapping[str, object],
    derived_indicators: object,
    *,
    as_of: object,
) -> None:
    if config.get("smart_multiplier_enabled") is not True:
        return
    payload = _resolve_indicator_payload(derived_indicators, "BTCUSDT")
    if not isinstance(payload, Mapping) or any(
        is_bool(value)
        for key, value in payload.items()
        if str(key).strip().lower()
        in {
            "close",
            "price",
            "last",
            "last_price",
            "sma200",
            "ma200",
            "sma_200",
            "ahr999",
            "ahr_999",
            "ahr999_gma",
            "ahr999_index",
        }
    ):
        raise ValueError("BTC smart cycle input is unavailable")
    indicator = _indicator_from_payload("BTCUSDT", payload)
    if not indicator or any(
        not math.isfinite(float(indicator.get(key) or 0.0)) or float(indicator.get(key) or 0.0) <= 0.0
        for key in ("price", "sma200")
    ):
        raise ValueError("BTC smart indicator input is unavailable")

    if config.get("cycle_indicator_enabled") is False:
        normalized_payload = {str(key).strip().lower(): value for key, value in payload.items()}

        def first_numeric(keys: tuple[str, ...]) -> float | None:
            for key in keys:
                if key in normalized_payload:
                    value = normalized_payload[key]
                    if is_bool(value):
                        return None
                    try:
                        numeric = float(value)
                    except (TypeError, ValueError):
                        return None
                    return numeric if math.isfinite(numeric) else None
            return None

        high = first_numeric(("high252", "high_252", "high252d", "high_252d"))
        drawdown = first_numeric(("drawdown_252d", "drawdown252", "drawdown"))
        rsi = first_numeric(("rsi14", "rsi_14", "rsi"))
        high_supplied = any(key in normalized_payload for key in ("high252", "high_252", "high252d", "high_252d"))
        drawdown_supplied = any(key in normalized_payload for key in ("drawdown_252d", "drawdown252", "drawdown"))
        if (
            (not high_supplied and not drawdown_supplied)
            or (high_supplied and (high is None or high <= 0))
            or (drawdown_supplied and (drawdown is None or not 0 <= drawdown <= 1))
            or rsi is None
            or not 0 <= rsi <= 100
        ):
            raise ValueError("BTC drawdown or RSI input is unavailable")

    if config.get("cycle_indicator_enabled") is True:
        # Do not substitute an estimated price-derived value for missing AHR input.
        direct_ahr = next(
            (
                payload.get(key)
                for key in ("ahr999_gma", "ahr999", "ahr_999", "ahr999_index")
                if payload.get(key) is not None
            ),
            None,
        )
        try:
            direct_ahr_num = float(direct_ahr)
        except (TypeError, ValueError):
            raise ValueError("BTC smart cycle input is unavailable") from None
        metrics = _cycle_metrics_from_payload(payload, as_of=as_of)
        ahr = metrics.get("ahr999")
        if (
            not math.isfinite(direct_ahr_num)
            or direct_ahr_num <= 0
            or not isinstance(ahr, (int, float))
            or not math.isfinite(ahr)
            or ahr <= 0
        ):
            raise ValueError("BTC smart cycle input is unavailable")


def _validate_btc_zscore_exit(
    zscore: object,
    portfolio_metadata: Mapping[str, object],
    config: Mapping[str, object],
) -> None:
    if config.get("zscore_exit_enabled") is not True:
        return
    if not isinstance(zscore, Mapping) or zscore.get("found") is not True or zscore.get("active") is not True:
        raise ValueError("BTC z-score exit input is unavailable")
    raw_exit, _ = _find_zscore_exit_payload(portfolio_metadata, config.get("zscore_exit_context"))
    control = raw_exit.get("position_control") if isinstance(raw_exit, Mapping) else None
    if not isinstance(control, Mapping):
        control = {}
    raw_exposures = []
    for source in (control, raw_exit):
        if not isinstance(source, Mapping):
            continue
        if "target_btc_exposure" in source:
            raw_exposures.append(source["target_btc_exposure"])
        for name in ("target_allocations", "target_exposure"):
            allocations = source.get(name)
            if name in source and not isinstance(allocations, Mapping):
                raise ValueError("invalid BTC z-score target exposure")
            if isinstance(allocations, Mapping) and "BTCUSDT" in allocations:
                raw_exposures.append(allocations["BTCUSDT"])
    validated_exposures = []
    for value in raw_exposures:
        if is_bool(value):
            raise ValueError("invalid BTC z-score target exposure")
        try:
            exposure = float(value)
        except (TypeError, ValueError):
            raise ValueError("invalid BTC z-score target exposure") from None
        if not math.isfinite(exposure) or not 0 <= exposure <= 1:
            raise ValueError("invalid BTC z-score target exposure")
        validated_exposures.append(exposure)
    if validated_exposures and any(value != validated_exposures[0] for value in validated_exposures[1:]):
        raise ValueError("conflicting BTC z-score target exposure")
    max_exit_exposure = {
        "risk_off": config["zscore_exit_risk_off_exposure"],
        "risk_reduced": config["zscore_exit_risk_reduced_exposure"],
    }.get(zscore.get("route"))
    if max_exit_exposure is not None and zscore.get("target_btc_exposure", 1.0) > max_exit_exposure:
        raise ValueError("BTC z-score target contradicts risk route")


def evaluate_crypto_btc_dca(ctx: StrategyContext) -> StrategyDecision:
    import crypto_strategies.entrypoints as entrypoints_pkg

    config = _merge_runtime_config(ctx, crypto_btc_dca_manifest.default_config)
    prices = _require_market_data(ctx, "market_prices")
    portfolio = _resolve_portfolio_snapshot(ctx)
    account_metrics = entrypoints_pkg._resolve_account_metrics(ctx)
    total_equity = account_metrics["total_equity"]
    derived_indicators = ctx.market_data.get("derived_indicators")
    translator = _resolve_translator(config)

    plan_config = _validate_btc_runtime_config(config, as_of=ctx.as_of, total_equity=total_equity)
    portfolio_metadata = _validate_btc_portfolio_and_prices(
        portfolio, prices, total_equity=total_equity
    )
    _validate_btc_smart_inputs(config, derived_indicators, as_of=ctx.as_of)

    compute_kwargs: dict[str, Any] = {
        "prices": prices,
        "portfolio": portfolio,
        "total_equity": total_equity,
        "state": dict(ctx.state),
        "config": plan_config,
        "derived_indicators": derived_indicators,
        "translator": translator,
        "strict_plan": True,
        "as_of": ctx.as_of,
    }
    if "zscore_exit_context" in config:
        compute_kwargs["zscore_exit_context"] = config["zscore_exit_context"]
    result = _crypto_btc_dca.compute_signals(**compute_kwargs)

    metadata = result.get("metadata")
    if not isinstance(metadata, Mapping) or type(metadata.get("actionable")) is not bool:
        raise ValueError("BTC rebalance plan is unavailable")
    zscore = metadata.get("zscore_exit")
    _validate_btc_zscore_exit(zscore, portfolio_metadata, config)

    btc_value = metadata.get("btc_value")
    target_values = metadata.get("target_values")
    if not isinstance(btc_value, (int, float)) or not math.isfinite(btc_value) or btc_value < 0:
        raise ValueError("BTC rebalance plan has invalid holdings")
    if not isinstance(target_values, Mapping):
        raise ValueError("BTC rebalance plan has invalid targets")
    planned_target = target_values.get("BTCUSDT", btc_value)
    if not isinstance(planned_target, (int, float)) or not math.isfinite(planned_target) or planned_target < 0:
        raise ValueError("BTC rebalance plan has invalid target")

    # The pre-existing dynamic BTC ratio caps new accumulation; never force a
    # sale solely because an existing holding is already above that limit.
    previous_ratio = float(result["btc_target_ratio"])
    target_value = min(planned_target, max(btc_value, total_equity * min(previous_ratio, 0.50)))
    if isinstance(zscore, Mapping) and zscore.get("applied") is True and zscore.get("route") in {
        "risk_reduced",
        "risk_off",
    }:
        target_value = min(target_value, btc_value)
    btc_target_ratio = target_value / total_equity
    positions = [PositionTarget(symbol="BTCUSDT", target_weight=btc_target_ratio, role="core")]
    budget_intents = (
        BudgetIntent(
            name="btc_dca_pool",
            symbol="BTCUSDT",
            amount=max(0.0, target_value - btc_value),
            purpose="btc_core_accumulation",
        ),
    )

    risk_flags: tuple[str, ...] = ()
    if btc_target_ratio <= 0.0:
        risk_flags += ("no_btc_allocation",)
    if not metadata.get("actionable", True) or target_value == btc_value:
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
        "planned_investment_usd": max(0.0, target_value - btc_value),
        "btc_value_usd": btc_value,
        "target_btc_value_usd": target_value,
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
    decision = entrypoints_pkg.apply_risk_gate(decision, ctx=ctx, max_single_weight=0.50)
    entrypoints_pkg.record_strategy_decision(
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

"""Account metrics and shared runtime context helpers for strategy entrypoints."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
from typing import Any

from quant_platform_kit.common.strategy_contracts import StrategyContext


def _merge_runtime_config(
    ctx: StrategyContext,
    manifest_config: Mapping[str, object],
) -> dict[str, object]:
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


def _require_finite_number(
    value: object,
    *,
    field_name: str,
    nonnegative: bool = False,
) -> float:
    """Validate that a numeric snapshot field is present, non-boolean, and finite."""
    if value is None or isinstance(value, bool) or type(value).__name__ == "bool_":
        raise ValueError(f"Invalid {field_name}: expected finite number, got {value!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: expected finite number, got {value!r}") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"Invalid {field_name}: expected finite number, got {value!r}")
    if nonnegative and numeric < 0.0:
        raise ValueError(f"Invalid {field_name}: must be non-negative, got {numeric!r}")
    return numeric


def _resolve_account_metrics(ctx: StrategyContext) -> dict[str, float]:
    """Resolve explicit equity, cash, and bucket valuation metrics from the snapshot.

    Supported cash sources (checked in explicit priority order; missing all cash
    fields raises ``ValueError`` instead of defaulting to ``0.0``):
    1. ``snapshot.metadata["account_metrics"]`` — explicit pre-aggregated metrics
       mapping relied upon by existing rotation/combo tests where ``buying_power``
       and ``cash_balance`` may be omitted (``None``).
    2. ``snapshot.metadata["cash_available_for_trading"]`` — canonical metadata
       cash contract emitted by QPK ``runtime_inputs``.
    3. ``snapshot.buying_power`` — primary ``PortfolioSnapshot`` cash field.
    4. ``snapshot.cash_balance`` — ``PortfolioSnapshot`` fallback when
       ``buying_power`` is ``None``.
    """
    snapshot = _resolve_portfolio_snapshot(ctx)
    raw_metadata = getattr(snapshot, "metadata", None)
    if raw_metadata is None:
        metadata: Mapping[str, object] = {}
    elif isinstance(raw_metadata, Mapping):
        metadata = raw_metadata
    else:
        raise ValueError("PortfolioSnapshot metadata must be a mapping")

    embedded_metrics = metadata.get("account_metrics")
    if embedded_metrics is not None:
        if not isinstance(embedded_metrics, Mapping):
            raise ValueError("PortfolioSnapshot metadata['account_metrics'] must be a mapping")
        required = ("total_equity", "cash_usdt", "trend_value", "dca_value")
        missing = [key for key in required if key not in embedded_metrics]
        if missing:
            raise ValueError(
                "PortfolioSnapshot metadata['account_metrics'] missing required keys: "
                + ", ".join(missing)
            )
        return {
            "total_equity": _require_finite_number(
                embedded_metrics["total_equity"],
                field_name="account_metrics.total_equity",
            ),
            "cash_usdt": _require_finite_number(
                embedded_metrics["cash_usdt"],
                field_name="account_metrics.cash_usdt",
                nonnegative=True,
            ),
            "trend_value": _require_finite_number(
                embedded_metrics["trend_value"],
                field_name="account_metrics.trend_value",
                nonnegative=True,
            ),
            "dca_value": _require_finite_number(
                embedded_metrics["dca_value"],
                field_name="account_metrics.dca_value",
                nonnegative=True,
            ),
        }

    cash_usdt: float | None = None
    for source_name, raw_value in (
        ("metadata.cash_available_for_trading", metadata.get("cash_available_for_trading")),
        ("snapshot.buying_power", getattr(snapshot, "buying_power", None)),
        ("snapshot.cash_balance", getattr(snapshot, "cash_balance", None)),
    ):
        if raw_value is not None:
            cash_usdt = _require_finite_number(
                raw_value,
                field_name=source_name,
                nonnegative=True,
            )
            break
    if cash_usdt is None:
        raise ValueError(
            "PortfolioSnapshot missing required cash field "
            "(expected metadata.account_metrics, metadata.cash_available_for_trading, "
            "buying_power, or cash_balance)"
        )

    dca_value = 0.0
    trend_value = 0.0
    for position in getattr(snapshot, "positions", ()) or ():
        symbol = str(getattr(position, "symbol", "")).strip().upper()
        market_value = _require_finite_number(
            getattr(position, "market_value", None),
            field_name="position.market_value",
            nonnegative=True,
        )
        if symbol == "BTCUSDT":
            dca_value += market_value
        else:
            trend_value += market_value

    raw_trend_value = metadata.get("trend_value")
    raw_dca_value = metadata.get("dca_value")
    resolved_trend_value = (
        trend_value
        if raw_trend_value is None
        else _require_finite_number(
            raw_trend_value,
            field_name="metadata.trend_value",
            nonnegative=True,
        )
    )
    resolved_dca_value = (
        dca_value
        if raw_dca_value is None
        else _require_finite_number(
            raw_dca_value,
            field_name="metadata.dca_value",
            nonnegative=True,
        )
    )

    return {
        "total_equity": _require_finite_number(
            getattr(snapshot, "total_equity", None),
            field_name="snapshot.total_equity",
        ),
        "cash_usdt": cash_usdt,
        "trend_value": resolved_trend_value,
        "dca_value": resolved_dca_value,
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


def _load_legacy_modules() -> tuple[Any, Any]:
    from crypto_strategies.strategies.crypto_live_pool_rotation import core as legacy_core
    from crypto_strategies.strategies.crypto_live_pool_rotation import rotation as legacy_rotation

    return legacy_core, legacy_rotation

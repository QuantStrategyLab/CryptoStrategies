"""Point-in-time replay for the Crypto Equity Combo strategy.

This research-only runner consumes caller-supplied daily snapshots. It uses a
signal day's close to build targets, trades at the next calendar day's open,
and marks the resulting holdings at that day's close. Synthetic runs are
engineering checks, not promotion evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from crypto_strategies.backtest.live_pool_simulator import _rebalance_holdings
from crypto_strategies.strategies import crypto_equity_combo
from crypto_strategies.strategies.crypto_trend_rotation import REQUIRED_FEATURE_COLUMNS


@dataclass(frozen=True)
class ComboSignalSnapshot:
    """Explicit-date PIT inputs required to evaluate one close-time signal."""

    signal_date: Any
    indicators_as_of: Any
    indicators_available_at: Any
    indicators: Mapping[str, Mapping[str, Any]]
    universe_as_of: Any
    universe_available_at: Any
    universe_source_version: str
    universe: Sequence[str]
    benchmark_as_of: Any
    benchmark_available_at: Any
    benchmark: Mapping[str, Any]


@dataclass(frozen=True)
class ComboReplayResult:
    daily: pd.DataFrame
    trades: pd.DataFrame
    final_state: dict[str, Any]
    simulation_model: str = "actual_strategy_replay"
    evidence_kind: str = "synthetic"
    research_only: bool = True
    promotion_eligible: bool = False
    live_ready: bool = False
    size_zero_required: bool = True
    no_order: bool = True
    cost_status: str = "explicit_synthetic_assumption"


def _daily_date(value: Any, name: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid calendar date") from exc
    if pd.isna(timestamp) or timestamp.tz is not None or timestamp != timestamp.normalize():
        raise ValueError(f"{name} must be a timezone-naive midnight date")
    return timestamp.normalize()


def _normalise_ohlc(ohlc: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(ohlc, pd.DataFrame) or not isinstance(ohlc.index, pd.MultiIndex):
        raise ValueError("ohlc must be a DataFrame indexed by (date, symbol)")
    if ohlc.index.nlevels != 2 or set(ohlc.index.names) != {"date", "symbol"}:
        raise ValueError("ohlc index levels must be named date and symbol")
    if not {"open", "close"}.issubset(ohlc.columns):
        raise ValueError("ohlc must include open and close columns")
    frame = ohlc.reset_index().copy()
    frame["date"] = [_daily_date(value, "ohlc date") for value in frame["date"]]
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    if (frame["symbol"] == "").any():
        raise ValueError("ohlc symbols must be non-empty")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate date/symbol OHLC rows are not allowed")
    return frame.set_index(["date", "symbol"]).sort_index()


def _normalise_indicator_map(
    indicators: Mapping[str, Mapping[str, Any]],
    universe: tuple[str, ...],
    indicators_as_of: pd.Timestamp,
    close_prices: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    if not isinstance(indicators, Mapping):
        raise ValueError("indicators snapshot must be a mapping")
    normalised: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_payload in indicators.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in normalised or not isinstance(raw_payload, Mapping):
            raise ValueError("indicator symbols must be unique with mapping payloads")
        normalised[symbol] = dict(raw_payload)

    required_fields = REQUIRED_FEATURE_COLUMNS - {"symbol"}
    for symbol in ("BTCUSDT", *universe):
        payload = normalised.get(symbol)
        if payload is None:
            message = "BTCUSDT indicators are required" if symbol == "BTCUSDT" else f"missing indicators for {symbol}"
            raise ValueError(message)
        missing = required_fields - set(payload)
        if missing:
            raise ValueError(f"missing required indicators for {symbol}: {', '.join(sorted(missing))}")
        for field in required_fields:
            value = payload[field]
            if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)) or not math.isfinite(float(value)):
                raise ValueError(f"indicator {symbol}.{field} must be finite")
        if payload["close"] <= 0.0:
            raise ValueError(f"indicator {symbol}.close must be positive")
        if symbol == "BTCUSDT" and not isinstance(payload.get("regime_on"), bool):
            raise ValueError("BTCUSDT indicators require an explicit boolean regime_on")
        close = _required_price(close_prices, symbol, "close", indicators_as_of)
        if not math.isclose(float(payload["close"]), close, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"{symbol} indicator close must match OHLC close on indicators_as_of")
    return normalised


def _required_price(
    prices: pd.DataFrame,
    symbol: str,
    column: str,
    date: pd.Timestamp,
) -> float:
    try:
        value = float(prices.loc[(date, symbol), column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"missing required {column} for {symbol} on {date.date()}") from exc
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"required {column} for {symbol} must be finite and positive")
    return value


def _validate_snapshot(snapshot: ComboSignalSnapshot) -> tuple[pd.Timestamp, tuple[str, ...]]:
    if not isinstance(snapshot, ComboSignalSnapshot):
        raise ValueError("snapshots must contain ComboSignalSnapshot values")
    signal_date = _daily_date(snapshot.signal_date, "signal_date")
    for label, as_of_value, available_value in (
        ("indicators", snapshot.indicators_as_of, snapshot.indicators_available_at),
        ("universe", snapshot.universe_as_of, snapshot.universe_available_at),
        ("benchmark", snapshot.benchmark_as_of, snapshot.benchmark_available_at),
    ):
        as_of = _daily_date(as_of_value, f"{label}_as_of")
        available_at = _daily_date(available_value, f"{label}_available_at")
        if available_at < as_of:
            raise ValueError(f"{label} snapshot availability precedes its as_of date")
        if as_of > signal_date or available_at > signal_date:
            raise ValueError(f"{label} snapshot available after signal date")
    universe = tuple(str(symbol).strip().upper() for symbol in snapshot.universe)
    if not universe or any(not symbol for symbol in universe) or len(set(universe)) != len(universe):
        raise ValueError("universe must contain unique non-empty symbols")
    if "BTCUSDT" in universe:
        raise ValueError("universe must contain trend candidates only, not BTCUSDT")
    if not str(snapshot.universe_source_version).strip():
        raise ValueError("universe_source_version is required")
    if not isinstance(snapshot.benchmark, Mapping) or not isinstance(snapshot.benchmark.get("regime_on"), bool):
        raise ValueError("benchmark snapshot requires an explicit boolean regime_on")
    return signal_date, universe


def _validate_state(
    initial_state: Mapping[str, Any],
    *,
    first_signal_date: pd.Timestamp,
    first_universe: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(initial_state, Mapping) or not initial_state:
        raise ValueError("initial_state must be a non-empty rotation state")
    state = dict(initial_state)
    if not state.get("rotation_pool_symbols"):
        raise ValueError("initial_state requires non-empty rotation_pool_symbols")
    if not str(state.get("trend_pool_version", "")).strip():
        raise ValueError("initial_state requires trend_pool_version")
    state_as_of = _daily_date(state.get("trend_pool_as_of_date"), "initial trend_pool_as_of_date")
    if state_as_of >= first_signal_date:
        raise ValueError("initial rotation state must predate the first signal date")
    cached_pool = tuple(str(symbol).strip().upper() for symbol in state["rotation_pool_symbols"])
    if not cached_pool or len(set(cached_pool)) != len(cached_pool):
        raise ValueError("initial rotation pool must contain unique symbols")
    if not set(cached_pool).issubset(first_universe):
        raise ValueError("initial rotation pool must be within the first PIT universe")
    return state


def run_crypto_equity_combo_replay(
    snapshots: Sequence[ComboSignalSnapshot],
    ohlc: pd.DataFrame,
    *,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
    initial_state: Mapping[str, Any],
    strategy_kwargs: Mapping[str, Any] | None = None,
) -> ComboReplayResult:
    """Replay actual combo targets from a close signal at the following open.

    External flows are unsupported and therefore zero throughout the replay.
    Fee and slippage rates are explicit assumptions, not measured execution
    costs. The returned result is always synthetic research-only evidence.
    """
    if len(snapshots) == 0:
        raise ValueError("at least one signal snapshot is required")
    if isinstance(initial_cash, (bool, np.bool_)) or not isinstance(initial_cash, (int, float, np.number)):
        raise ValueError("initial_cash must be finite and positive")
    cash = float(initial_cash)
    if not math.isfinite(cash) or cash <= 0.0:
        raise ValueError("initial_cash must be finite and positive")
    if (
        isinstance(fee_bps, (bool, np.bool_))
        or isinstance(slippage_bps, (bool, np.bool_))
        or not isinstance(fee_bps, (int, float, np.number))
        or not isinstance(slippage_bps, (int, float, np.number))
        or not math.isfinite(float(fee_bps))
        or not math.isfinite(float(slippage_bps))
        or float(fee_bps) < 0.0
        or float(slippage_bps) < 0.0
    ):
        raise ValueError("fee_bps and slippage_bps must be finite and non-negative")
    fee_rate = float(fee_bps) / 10_000.0
    slippage_rate = float(slippage_bps) / 10_000.0
    cost_rate = fee_rate + slippage_rate
    if cost_rate >= 1.0:
        raise ValueError("combined transaction cost rate must be less than 1.0")

    allowed_strategy_kwargs = {
        "btc_weight", "trend_weight", "dynamic_mode", "dynamic_regime_mode",
        "dynamic_regime_off_cut", "rotation_top_n", "weight_mode",
        "allow_rotation_refresh", "circuit_breaker_enabled", "btc_drawdown_threshold",
        "vol_scaling_enabled", "target_vol", "max_leverage", "smart_multiplier_enabled",
    }
    forbidden_strategy_kwargs = {
        "prices", "indicators_map", "universe_snapshot", "benchmark_snapshot",
        "portfolio", "state", "as_of", "translator", "derived_indicators",
        "zscore_exit_context",
    }
    strategy_options = dict(strategy_kwargs or {})
    if forbidden_strategy_kwargs & strategy_options.keys():
        raise ValueError("strategy_kwargs cannot override point-in-time replay inputs")
    unknown_strategy_options = strategy_options.keys() - allowed_strategy_kwargs
    if unknown_strategy_options:
        raise ValueError(f"unsupported strategy configuration: {', '.join(sorted(unknown_strategy_options))}")
    for key, value in strategy_options.items():
        values = value if isinstance(value, (tuple, list)) else (value,)
        if any(
            isinstance(item, Mapping)
            or not isinstance(item, (str, bool, int, float, np.number))
            or (
                isinstance(item, (int, float, np.number))
                and not isinstance(item, (bool, np.bool_))
                and not math.isfinite(float(item))
            )
            for item in values
        ):
            raise ValueError(f"strategy configuration {key} must use finite scalar values")

    raw_signal_dates = [
        _daily_date(snapshot.signal_date, "signal_date")
        for snapshot in snapshots
        if isinstance(snapshot, ComboSignalSnapshot)
    ]
    if len(raw_signal_dates) != len(snapshots):
        raise ValueError("snapshots must contain ComboSignalSnapshot values")
    if len(set(raw_signal_dates)) != len(raw_signal_dates):
        raise ValueError("signal dates must be unique")
    prepared = [_validate_snapshot(snapshot) for snapshot in snapshots]
    signal_dates = [date for date, _ in prepared]
    if signal_dates != sorted(signal_dates):
        raise ValueError("signal snapshots must be chronological")
    if len(signal_dates) > 1 and not pd.DatetimeIndex(signal_dates).equals(
        pd.date_range(signal_dates[0], periods=len(signal_dates), freq="D")
    ):
        raise ValueError("signal snapshots must be consecutive calendar days")

    prices = _normalise_ohlc(ohlc)
    available_dates = set(prices.index.get_level_values("date"))
    symbols = tuple(sorted(set(prices.index.get_level_values("symbol"))))
    if "BTCUSDT" not in symbols:
        raise ValueError("OHLC must include BTCUSDT")
    if any(date not in available_dates or date + pd.Timedelta(days=1) not in available_dates for date in signal_dates):
        raise ValueError("OHLC must include each signal date and its next natural calendar day")

    holdings = pd.Series(0.0, index=symbols, dtype=float)
    state = _validate_state(
        initial_state,
        first_signal_date=signal_dates[0],
        first_universe=prepared[0][1],
    )
    daily_records: list[dict[str, Any]] = []
    trade_records: list[dict[str, Any]] = []

    for snapshot, (signal_date, universe) in zip(snapshots, prepared, strict=True):
        effective_date = signal_date + pd.Timedelta(days=1)
        close_prices = prices.loc[signal_date]
        if not isinstance(close_prices, pd.DataFrame):
            raise ValueError("OHLC signal-date rows must contain symbol prices")
        required_signal_symbols = {"BTCUSDT", *universe} | set(holdings[holdings > 0.0].index)
        signal_close = {
            symbol: _required_price(prices, symbol, "close", signal_date)
            for symbol in required_signal_symbols
        }
        indicators = _normalise_indicator_map(
            snapshot.indicators,
            universe,
            _daily_date(snapshot.indicators_as_of, "indicators_as_of"),
            prices,
        )
        held_values = {
            symbol: float(holdings.loc[symbol]) * signal_close[symbol]
            for symbol in holdings[holdings > 0.0].index
        }
        signal_equity = cash + sum(held_values.values())
        if not math.isfinite(signal_equity) or signal_equity <= 0.0:
            raise ValueError("signal-time portfolio equity must remain finite and positive")
        btc_value = held_values.get("BTCUSDT", 0.0)
        portfolio = {
            "total_equity": signal_equity,
            "buying_power": cash,
            "cash_balance": cash,
            "positions": [
                {"symbol": symbol, "market_value": market_value}
                for symbol, market_value in held_values.items()
            ],
            "metadata": {"dca_value": btc_value},
        }

        state["trend_pool_version"] = str(snapshot.universe_source_version).strip()
        state["trend_pool_as_of_date"] = _daily_date(
            snapshot.universe_as_of, "universe_as_of"
        ).strftime("%Y-%m-%d")
        targets, metadata = crypto_equity_combo.build_target_weights(
            prices=signal_close,
            indicators_map=indicators,
            universe_snapshot=universe,
            benchmark_snapshot=dict(snapshot.benchmark),
            portfolio=portfolio,
            state=state,
            as_of=signal_date.strftime("%Y-%m-%d"),
            **strategy_options,
        )
        if not isinstance(targets, Mapping):
            raise ValueError("strategy target weights must be a mapping")
        if not isinstance(metadata, Mapping) or metadata.get("error"):
            raise ValueError("strategy metadata error prevents successful replay")
        try:
            strategy_equity = float(metadata.get("total_equity"))
        except (TypeError, ValueError) as exc:
            raise ValueError("strategy metadata must include signal-time total_equity") from exc
        if not math.isfinite(strategy_equity) or not math.isclose(
            strategy_equity, signal_equity, rel_tol=1e-10, abs_tol=1e-8
        ):
            raise ValueError("strategy metadata total_equity must equal signal-time ledger equity")
        trend_metadata = metadata.get("trend_leg")
        if not isinstance(trend_metadata, Mapping) or trend_metadata.get("error"):
            raise ValueError("trend strategy metadata error prevents successful replay")

        target_values: dict[str, float] = {}
        allowed_symbols = {"BTCUSDT", *universe}
        for raw_symbol, raw_weight in targets.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or symbol not in allowed_symbols or symbol not in holdings.index:
                raise ValueError(f"target weights reference unknown symbol: {raw_symbol}")
            if isinstance(raw_weight, (bool, np.bool_)) or not isinstance(raw_weight, (int, float, np.number)):
                raise ValueError("target weights must be finite non-negative numbers")
            weight = float(raw_weight)
            if not math.isfinite(weight) or weight < 0.0:
                raise ValueError("target weights must be finite non-negative numbers")
            target_values[symbol] = weight
        if sum(target_values.values()) > 1.0 + 1e-10:
            raise ValueError("target weights gross exposure must not exceed 1.0")

        btc_metadata = metadata.get("btc_leg")
        dca_metadata = btc_metadata.get("dca_metadata") if isinstance(btc_metadata, Mapping) else None
        required_dca_metadata = {"regime", "actionable", "planned_investment_usd"}
        if not isinstance(dca_metadata, Mapping) or not required_dca_metadata.issubset(dca_metadata):
            raise ValueError("BTC DCA metadata missing; fallback signals cannot be replayed successfully")
        if not isinstance(dca_metadata["actionable"], bool):
            raise ValueError("BTC DCA actionable metadata must be boolean")
        try:
            planned_investment = float(dca_metadata["planned_investment_usd"])
        except (TypeError, ValueError) as exc:
            raise ValueError("BTC DCA planned investment metadata must be finite") from exc
        if not math.isfinite(planned_investment) or planned_investment < 0.0:
            raise ValueError("BTC DCA planned investment metadata must be finite and non-negative")

        target_series = pd.Series(0.0, index=symbols, dtype=float)
        for symbol, weight in target_values.items():
            target_series.loc[symbol] = weight

        execution_open = pd.Series(np.nan, index=symbols, dtype=float)
        for symbol in set(holdings[holdings > 0.0].index) | set(target_values):
            execution_open.loc[symbol] = _required_price(prices, symbol, "open", effective_date)
        pretrade_open_equity = cash + float(
            (holdings * execution_open.fillna(0.0)).sum()
        )
        previous_holdings = holdings.copy()
        holdings, cash, transaction_cost, sale_notional, purchase_notional = _rebalance_holdings(
            holdings,
            cash,
            execution_open,
            target_series,
            cost_rate=cost_rate,
        )
        fee = transaction_cost * (fee_rate / cost_rate) if cost_rate else 0.0
        slippage = transaction_cost * (slippage_rate / cost_rate) if cost_rate else 0.0
        execution_equity_after_cost = cash + float(
            (holdings * execution_open.fillna(0.0)).sum()
        )
        if not math.isclose(
            execution_equity_after_cost + transaction_cost,
            pretrade_open_equity,
            rel_tol=1e-9,
            abs_tol=1e-8,
        ):
            raise ValueError("execution cash, holdings, and costs do not reconcile")
        if cash < 0.0:
            raise ValueError("execution would overdraw cash")

        current_trades: list[dict[str, Any]] = []
        for symbol in symbols:
            quantity_delta = float(holdings.loc[symbol] - previous_holdings.loc[symbol])
            if abs(quantity_delta) <= 1e-12:
                continue
            open_price = float(execution_open.loc[symbol])
            notional = abs(quantity_delta) * open_price
            trade_fee = notional * fee_rate
            trade_slippage = notional * slippage_rate
            trade = {
                    "signal_date": signal_date,
                    "effective_date": effective_date,
                    "symbol": symbol,
                    "side": "buy" if quantity_delta > 0.0 else "sell",
                    "quantity": abs(quantity_delta),
                    "reference_open": open_price,
                    "notional": notional,
                    "target_weight": target_values.get(symbol, 0.0),
                    "fee": trade_fee,
                    "slippage": trade_slippage,
                    "cost": trade_fee + trade_slippage,
                }
            current_trades.append(trade)
            trade_records.append(trade)
        if not math.isclose(
            sum(row["cost"] for row in current_trades),
            transaction_cost,
            rel_tol=1e-8,
            abs_tol=1e-8,
        ):
            raise ValueError("trade cost records do not reconcile to the cash ledger")

        execution_close = pd.Series(np.nan, index=symbols, dtype=float)
        held_symbols = tuple(holdings[holdings > 0.0].index)
        for symbol in held_symbols:
            execution_close.loc[symbol] = _required_price(prices, symbol, "close", effective_date)
        ending_equity = cash + float((holdings * execution_close.fillna(0.0)).sum())
        if not math.isfinite(ending_equity) or ending_equity <= 0.0:
            raise ValueError("ending portfolio equity must remain finite and positive")

        daily_records.append(
            {
                "signal_date": signal_date,
                "effective_date": effective_date,
                "target_weights": dict(target_values),
                "actual_shares": {
                    symbol: float(holdings.loc[symbol]) for symbol in held_symbols
                },
                "cash": cash,
                "fee": fee,
                "slippage": slippage,
                "transaction_cost": transaction_cost,
                "external_flow": 0.0,
                "signal_total_equity": signal_equity,
                "pretrade_open_equity": pretrade_open_equity,
                "execution_equity_after_cost": execution_equity_after_cost,
                "execution_prices": {
                    symbol: float(execution_open.loc[symbol]) for symbol in held_symbols
                },
                "ending_equity": ending_equity,
                "cost_status": "explicit_synthetic_assumption",
                "simulation_model": "actual_strategy_replay",
                "evidence_kind": "synthetic",
            }
        )
        cash = float(cash)

    return ComboReplayResult(
        daily=pd.DataFrame(daily_records),
        trades=pd.DataFrame(trade_records),
        final_state=state,
    )

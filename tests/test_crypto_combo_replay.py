from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from crypto_strategies.backtest.combo_replay import (
    ComboSignalSnapshot,
    run_crypto_equity_combo_replay,
)


SIGNAL_DATES = pd.date_range("2024-01-01", periods=2, freq="D")
ALL_DATES = pd.date_range("2024-01-01", periods=3, freq="D")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _indicators(day: pd.Timestamp) -> dict[str, dict[str, float | bool]]:
    offsets = (day - SIGNAL_DATES[0]).days
    btc_close = (100.0, 120.0)[offsets]
    eth_close = (100.0, 270.0)[offsets]
    sol_close = (50.0, 58.0)[offsets]
    return {
        "BTCUSDT": {
            "close": btc_close,
            "sma20": 90.0,
            "sma60": 85.0,
            "sma200": 80.0,
            "roc20": 0.05,
            "roc60": 0.10,
            "roc120": 0.20,
            "vol20": 0.20,
            "avg_quote_vol_30": 1_000_000.0,
            "avg_quote_vol_90": 900_000.0,
            "avg_quote_vol_180": 800_000.0,
            "trend_persist_90": 0.8,
            "age_days": 2000.0,
            "regime_on": True,
        },
        "ETHUSDT": {
            "close": eth_close,
            "sma20": 90.0,
            "sma60": 85.0,
            "sma200": 80.0,
            "roc20": 0.60,
            "roc60": 0.80,
            "roc120": 1.00,
            "vol20": 0.25,
            "avg_quote_vol_30": 2_000_000.0,
            "avg_quote_vol_90": 1_800_000.0,
            "avg_quote_vol_180": 1_600_000.0,
            "trend_persist_90": 0.9,
            "age_days": 1800.0,
        },
        "SOLUSDT": {
            "close": sol_close,
            "sma20": 45.0,
            "sma60": 40.0,
            "sma200": 35.0,
            "roc20": 0.30,
            "roc60": 0.40,
            "roc120": 0.50,
            "vol20": 0.30,
            "avg_quote_vol_30": 1_500_000.0,
            "avg_quote_vol_90": 1_300_000.0,
            "avg_quote_vol_180": 1_100_000.0,
            "trend_persist_90": 0.7,
            "age_days": 1500.0,
        },
    }


def _snapshots() -> list[ComboSignalSnapshot]:
    return [
        ComboSignalSnapshot(
            signal_date=day,
            indicators_as_of=day,
            indicators_available_at=day,
            indicators=_indicators(day),
            universe_as_of=day,
            universe_available_at=day,
            universe_source_version="synthetic-universe-v1",
            universe=("ETHUSDT", "SOLUSDT"),
            benchmark_as_of=day,
            benchmark_available_at=day,
            benchmark={"regime_on": True, "ma200": 80.0, "ma200_slope": 0.01},
        )
        for day in SIGNAL_DATES
    ]


def _ohlc() -> pd.DataFrame:
    opens = {
        "BTCUSDT": [100.0, 110.0, 115.0],
        "ETHUSDT": [100.0, 250.0, 300.0],
        "SOLUSDT": [50.0, 55.0, 60.0],
    }
    closes = {
        "BTCUSDT": [100.0, 120.0, 118.0],
        "ETHUSDT": [100.0, 270.0, 310.0],
        "SOLUSDT": [50.0, 58.0, 62.0],
    }
    rows = [
        (day, symbol, opens[symbol][index], closes[symbol][index])
        for index, day in enumerate(ALL_DATES)
        for symbol in SYMBOLS
    ]
    return pd.DataFrame(
        [(open_price, close_price) for _, _, open_price, close_price in rows],
        index=pd.MultiIndex.from_tuples(
            [(day, symbol) for day, symbol, _, _ in rows], names=("date", "symbol")
        ),
        columns=("open", "close"),
    )


def _run(snapshots=None, ohlc=None, **kwargs):
    call_kwargs = {
        "initial_cash": 1_000.0,
        "fee_bps": 10.0,
        "slippage_bps": 20.0,
        "initial_state": {
            "rotation_pool_symbols": ["ETHUSDT", "SOLUSDT"],
            "trend_pool_version": "synthetic-universe-v1",
            "trend_pool_as_of_date": "2023-12-31",
        },
        "strategy_kwargs": {
            "btc_weight": 0.2,
            "trend_weight": 0.6,
            "dynamic_mode": False,
            "rotation_top_n": 1,
            "weight_mode": "equal",
            "vol_scaling_enabled": False,
            "circuit_breaker_enabled": False,
            "smart_multiplier_enabled": False,
        },
    }
    call_kwargs.update(kwargs)
    return run_crypto_equity_combo_replay(
        _snapshots() if snapshots is None else snapshots,
        _ohlc() if ohlc is None else ohlc,
        **call_kwargs,
    )


def test_replay_calls_real_combo_strategy_and_trades_next_open_without_lookahead() -> None:
    from crypto_strategies.strategies import crypto_equity_combo

    captured: list[tuple[dict, dict]] = []
    original = crypto_equity_combo.build_target_weights

    def capture(*args, **kwargs):
        targets, metadata = original(*args, **kwargs)
        captured.append((kwargs["prices"], kwargs["portfolio"]))
        return targets, metadata

    crypto_equity_combo.build_target_weights = capture
    try:
        result = _run()
    finally:
        crypto_equity_combo.build_target_weights = original

    first = result.daily.iloc[0]
    assert result.simulation_model == "actual_strategy_replay"
    assert result.evidence_kind == "synthetic"
    assert result.research_only is True
    assert first["signal_date"] == SIGNAL_DATES[0]
    assert first["effective_date"] == SIGNAL_DATES[1]
    assert first["target_weights"]["ETHUSDT"] == pytest.approx(0.6)
    assert first["target_weights"]["BTCUSDT"] > 0.0
    assert sum(first["target_weights"].values()) < 1.0
    assert first["actual_shares"]["BTCUSDT"] > 0.0
    assert first["actual_shares"]["ETHUSDT"] > 0.0
    assert captured[0][0]["ETHUSDT"] == 100.0
    assert captured[0][1]["total_equity"] == pytest.approx(1_000.0)
    assert first["actual_shares"]["ETHUSDT"] < 1_000.0 / 250.0
    assert first["cash"] > 0.0
    assert len(result.daily) == 2
    assert result.final_state["rotation_pool_source_as_of_date"] == "2024-01-02"
    assert result.daily.iloc[1]["signal_total_equity"] == pytest.approx(
        result.daily.iloc[0]["ending_equity"]
    )


def test_replay_costs_reconcile_to_cash_and_purchase_never_overdraws() -> None:
    result = _run()

    for _, row in result.daily.iterrows():
        execution_equity_after_cost = row["cash"] + sum(
            shares * row["execution_prices"][symbol]
            for symbol, shares in row["actual_shares"].items()
        )
        assert row["transaction_cost"] == pytest.approx(row["fee"] + row["slippage"])
        assert execution_equity_after_cost + row["transaction_cost"] == pytest.approx(
            row["pretrade_open_equity"]
        )
        assert row["cash"] >= 0.0
        assert row["external_flow"] == 0.0

    assert result.cost_status == "explicit_synthetic_assumption"


def test_future_feature_availability_is_rejected() -> None:
    snapshots = _snapshots()
    snapshots[0] = replace(
        snapshots[0], indicators_available_at=SIGNAL_DATES[0] + pd.Timedelta(days=1)
    )
    with pytest.raises(ValueError, match="available after signal date"):
        _run(snapshots=snapshots)

    snapshots[0] = replace(
        snapshots[0], indicators_as_of=SIGNAL_DATES[0],
        indicators_available_at=SIGNAL_DATES[0] - pd.Timedelta(days=1),
    )
    with pytest.raises(ValueError, match="availability precedes its as_of"):
        _run(snapshots=snapshots)

    snapshots = _snapshots()
    mismatched = dict(snapshots[0].indicators)
    mismatched["ETHUSDT"] = {**mismatched["ETHUSDT"], "close": 101.0}
    snapshots[0] = replace(snapshots[0], indicators=mismatched)
    with pytest.raises(ValueError, match="indicator close must match OHLC close"):
        _run(snapshots=snapshots)


def test_duplicate_ohlc_symbol_and_missing_held_quote_are_rejected() -> None:
    duplicate = pd.concat([_ohlc(), _ohlc().iloc[[0]]])
    with pytest.raises(ValueError, match="duplicate date/symbol"):
        _run(ohlc=duplicate)

    missing_close = _ohlc()
    missing_close.loc[(SIGNAL_DATES[1], "ETHUSDT"), "close"] = float("nan")
    with pytest.raises(ValueError, match="required close"):
        _run(ohlc=missing_close)


def test_missing_btc_signal_metadata_fallback_is_not_successful_replay() -> None:
    from crypto_strategies.strategies import crypto_btc_dca

    original = crypto_btc_dca.build_rebalance_plan

    def fail_plan(*args, **kwargs):
        raise ValueError("synthetic BTC plan failure")

    crypto_btc_dca.build_rebalance_plan = fail_plan
    try:
        with pytest.raises(ValueError, match="BTC DCA metadata"):
            _run()
    finally:
        crypto_btc_dca.build_rebalance_plan = original


def test_missing_btc_indicators_and_invalid_targets_are_rejected() -> None:
    snapshots = _snapshots()
    snapshots[0] = replace(
        snapshots[0], indicators={key: value for key, value in snapshots[0].indicators.items() if key != "BTCUSDT"}
    )
    with pytest.raises(ValueError, match="BTCUSDT indicators"):
        _run(snapshots=snapshots)

    from crypto_strategies.strategies import crypto_equity_combo

    original = crypto_equity_combo.build_target_weights
    crypto_equity_combo.build_target_weights = lambda **kwargs: (
        {"ETHUSDT": float("nan")},
        {
            "total_equity": kwargs["portfolio"]["total_equity"],
            "btc_leg": {
                "dca_metadata": {
                    "regime": "ordinary_dca",
                    "actionable": True,
                    "planned_investment_usd": 0.0,
                }
            },
            "trend_leg": {},
        },
    )
    try:
        with pytest.raises(ValueError, match="target weights"):
            _run()
        crypto_equity_combo.build_target_weights = lambda **kwargs: (
            {"ETHUSDT": 1.2},
            {
                "total_equity": kwargs["portfolio"]["total_equity"],
                "btc_leg": {
                    "dca_metadata": {
                        "regime": "ordinary_dca",
                        "actionable": True,
                        "planned_investment_usd": 0.0,
                    }
                },
                "trend_leg": {},
            },
        )
        with pytest.raises(ValueError, match="gross exposure must not exceed"):
            _run()
    finally:
        crypto_equity_combo.build_target_weights = original


def test_future_initial_rotation_state_and_unchecked_strategy_data_are_rejected() -> None:
    future_state = {
        "rotation_pool_symbols": ["ETHUSDT"],
        "trend_pool_version": "synthetic-universe-v1",
        "trend_pool_as_of_date": "2024-01-02",
    }
    with pytest.raises(ValueError, match="must predate the first signal date"):
        _run(initial_state=future_state)

    with pytest.raises(ValueError, match="cannot override point-in-time replay inputs"):
        _run(strategy_kwargs={"derived_indicators": {"BTCUSDT": {"roc20": 0.9}}})


def test_duplicate_signal_dates_and_missing_target_open_are_rejected() -> None:
    snapshots = _snapshots()
    snapshots[1] = replace(snapshots[1], signal_date=snapshots[0].signal_date)
    with pytest.raises(ValueError, match="signal dates must be unique"):
        _run(snapshots=snapshots)

    missing_open = _ohlc().drop(index=(SIGNAL_DATES[1], "ETHUSDT"))
    with pytest.raises(ValueError, match="missing required open"):
        _run(ohlc=missing_open)

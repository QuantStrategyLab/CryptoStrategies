from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import (
    BacktestOrchestrator,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

from crypto_strategies.backtest.live_pool_simulator import (
    run_live_pool_rotation_backtest,
)
from crypto_strategies.backtest.orchestrator_runner import (
    PROFILE_NAME,
    CryptoLivePoolBacktestRunner,
)


def _panel() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.date_range("2024-01-01", periods=5), ["A", "B"]], names=["date", "symbol"]
    )
    panel = pd.DataFrame({"open": 100.0, "in_universe": True}, index=index)
    panel["final_score"] = [1.0, 0.0] * 5
    return panel


@pytest.mark.parametrize("day_index", [1, 2, 3])
@pytest.mark.parametrize("cash_only", [False, True])
def test_internal_missing_date_is_not_compressed(day_index: int, cash_only: bool) -> None:
    panel = _panel()
    if cash_only:
        panel.loc[:, "in_universe"] = False
    day = panel.index.get_level_values("date").unique()[day_index]
    panel = panel.drop(index=day, level="date")

    with pytest.raises(ValueError, match="panel dates must be consecutive calendar days"):
        run_live_pool_rotation_backtest(panel, top_n=1)


@pytest.mark.parametrize("cash_only", [False, True])
def test_complete_daily_panel_keeps_existing_cash_and_invested_behavior(cash_only: bool) -> None:
    panel = _panel()
    if cash_only:
        panel.loc[:, "in_universe"] = False
        panel.loc[:, "open"] = np.nan

    result = run_live_pool_rotation_backtest(panel, top_n=1)

    assert result.returns.tolist() == ([] if cash_only else [0.0, 0.0, 0.0])


def test_late_listed_unselected_symbol_does_not_make_a_global_date_gap() -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel = panel.drop(index=[(day, "B") for day in dates[:2]])

    result = run_live_pool_rotation_backtest(panel, top_n=1)

    assert result.returns.index.tolist() == dates[1:-1].tolist()
    assert result.returns.tolist() == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("bad_open", [np.nan, np.inf, -np.inf, 0.0, -1.0])
@pytest.mark.parametrize("day_index", [1, 2, 4], ids=["entry", "held", "terminal"])
def test_required_open_must_be_finite_and_positive(bad_open: float, day_index: int) -> None:
    panel = _panel()
    day = panel.index.get_level_values("date").unique()[day_index]
    panel.loc[(day, "A"), "open"] = bad_open

    with pytest.raises(ValueError, match="required open prices must be finite and positive"):
        run_live_pool_rotation_backtest(panel, top_n=1, rebalance_every=7)


@pytest.mark.parametrize("day_index", [1, 2, 4])
def test_missing_required_symbol_row_is_not_a_zero_return(day_index: int) -> None:
    panel = _panel()
    day = panel.index.get_level_values("date").unique()[day_index]
    panel = panel.drop(index=(day, "A"))

    with pytest.raises(ValueError, match="required open prices must be finite and positive"):
        run_live_pool_rotation_backtest(panel, top_n=1, rebalance_every=7)


def test_missing_open_cannot_be_hidden_by_exiting_to_cash() -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel.loc[(dates[1:], slice(None)), "in_universe"] = False
    panel.loc[(dates[2], "A"), "open"] = np.nan

    with pytest.raises(ValueError, match="required open prices must be finite and positive"):
        run_live_pool_rotation_backtest(panel, top_n=1, rebalance_every=1)


@pytest.mark.parametrize("bad_open", [np.nan, np.inf, -np.inf, 0.0, -1.0])
def test_unselected_prices_do_not_affect_exposed_returns(bad_open: float) -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel.loc[(slice(None), "B"), "open"] = bad_open
    panel.loc[(dates[2:], "A"), "open"] = 101.0

    result = run_live_pool_rotation_backtest(panel, top_n=1, rebalance_every=7)

    assert result.returns.tolist() == pytest.approx([0.01, 0.0, 0.0])


def test_cash_after_exit_does_not_require_future_asset_prices() -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel.loc[(dates[1:], slice(None)), "in_universe"] = False
    panel.loc[(dates[3:], slice(None)), "open"] = np.nan

    result = run_live_pool_rotation_backtest(panel, top_n=1, rebalance_every=1, fee_bps=100)

    assert result.returns.tolist() == pytest.approx([-0.01, -0.01, 0.0])
    assert result.trade_log["turnover"].tolist() == pytest.approx([1.0, 1.0, 0.0])


def test_cash_before_late_selection_does_not_require_asset_prices() -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel.loc[(dates[:2], slice(None)), "in_universe"] = False
    panel.loc[(dates[:3], slice(None)), "open"] = np.nan

    result = run_live_pool_rotation_backtest(panel, top_n=1, rebalance_every=1)

    assert result.returns.tolist() == [0.0, 0.0, 0.0]
    assert result.trade_log["turnover"].tolist() == [0.0, 0.0, 1.0]


@pytest.mark.parametrize("signal_lag", [0, 1, 5])
def test_terminal_signal_does_not_require_an_execution_beyond_window(signal_lag: int) -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel.loc[:, "in_universe"] = False
    panel.loc[(dates[-1], "A"), "in_universe"] = True
    panel.loc[:, "open"] = np.nan

    result = run_live_pool_rotation_backtest(panel, top_n=1, signal_lag=signal_lag)

    assert result.returns.tolist() == [0.0] * max(len(dates) - signal_lag - 1, 0)


def test_orchestrator_propagates_invalid_input_without_persisting(tmp_path: Path) -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel.loc[(dates[-1], "A"), "open"] = np.nan
    runner = CryptoLivePoolBacktestRunner(panel=panel)
    orchestrator = BacktestOrchestrator(store=PerformanceStore(local_root=tmp_path))
    orchestrator.register_runner("crypto", runner)

    with pytest.raises(ValueError, match="required open prices must be finite and positive"):
        orchestrator.run(PROFILE_NAME, domain="crypto", params={"top_n": 1})

    assert runner.last_daily_returns.empty
    assert not runner.run_return_history
    assert not list(tmp_path.rglob("*.json"))


def test_runner_uses_only_prices_inside_requested_window() -> None:
    panel = _panel()
    dates = panel.index.get_level_values("date").unique()
    panel.loc[(dates[-1], slice(None)), "open"] = np.nan
    runner = CryptoLivePoolBacktestRunner(panel=panel)

    result = runner.run(
        PROFILE_NAME, {"top_n": 1}, start_date=dates[0].date(), end_date=dates[-2].date()
    )

    assert result.observation_count == 2
    assert runner.last_daily_returns.tolist() == [0.0, 0.0]

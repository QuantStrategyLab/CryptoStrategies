from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pandas as pd
import pytest

from crypto_strategies.backtest.live_pool_simulator import run_live_pool_rotation_backtest
from crypto_strategies.backtest.orchestrator_runner import (
    PROFILE_NAME,
    CryptoLivePoolBacktestRunner,
    _synthetic_panel,
)


def _panel(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": date,
                "symbol": symbol,
                "in_universe": True,
                "open": opening,
                "final_score": score,
            }
            for date, symbol, opening, score in rows
        ]
    ).set_index(["date", "symbol"])


def test_close_derived_score_is_applied_on_next_open_by_default() -> None:
    panel = _panel(
        [
            ("2024-01-01", "A", 100.0, 1.0),
            ("2024-01-01", "B", 100.0, 0.0),
            ("2024-01-02", "A", 200.0, 0.0),
            ("2024-01-02", "B", 100.0, 1.0),
            ("2024-01-03", "A", 200.0, 0.0),
            ("2024-01-03", "B", 100.0, 1.0),
        ]
    )

    result = run_live_pool_rotation_backtest(panel, top_n=1, rebalance_every=1)

    assert list(result.returns.index) == [pd.Timestamp("2024-01-02")]
    assert result.returns.iloc[0] == 0.0
    assert result.trade_log.loc[0, "signal_date"] == pd.Timestamp("2024-01-01")
    assert result.trade_log.loc[0, "effective_date"] == pd.Timestamp("2024-01-02")


def test_weights_drift_and_cost_aggregates_are_mathematically_exact() -> None:
    panel = _panel(
        [
            ("2024-01-01", "A", 100.0, 1.0),
            ("2024-01-01", "B", 100.0, 0.0),
            ("2024-01-02", "A", 100.0, 1.0),
            ("2024-01-02", "B", 100.0, 0.0),
            ("2024-01-03", "A", 200.0, 1.0),
            ("2024-01-03", "B", 100.0, 0.0),
            ("2024-01-04", "A", 300.0, 1.0),
            ("2024-01-04", "B", 100.0, 0.0),
            ("2024-01-05", "A", 300.0, 1.0),
            ("2024-01-05", "B", 200.0, 0.0),
        ]
    )

    result = run_live_pool_rotation_backtest(
        panel,
        top_n=2,
        rebalance_every=2,
        fee_bps=100,
        slippage_bps=100,
    )

    # Fee/slippage constrain entry notional (no self-financed full weight).
    cost_rate = 0.02
    entry_notional = 1.0 / (1.0 + cost_rate)
    ret0 = 1.5 / (1.0 + cost_rate) / 1.0 - 1.0  # 50/50 with A +100%
    ret1 = 1.0 / 3.0  # no trade; A +50% on drifted weights
    # Second rebalance from 75/25 back toward 50/50 under fee drag.
    equity_before_second = 2.0 / (1.0 + cost_rate)
    sale = 0.25 * equity_before_second
    purchase = sale * (1.0 - cost_rate) / (1.0 + cost_rate)
    second_cost = (sale + purchase) * cost_rate
    # After selling A back to 50%, keep prior B and add constrained buy; then B +100%.
    a_after = 0.5 * equity_before_second
    b_after = 0.25 * equity_before_second + purchase
    marked = a_after + 2.0 * b_after
    ret2 = marked / equity_before_second - 1.0

    assert result.returns.tolist() == pytest.approx([ret0, ret1, ret2])
    assert result.trade_log["turnover"].tolist() == pytest.approx([
        entry_notional / 2.0,
        (sale + purchase) / (2.0 * equity_before_second),
    ])
    assert result.trade_log["fee"].tolist() == pytest.approx([
        entry_notional * 0.01,
        second_cost * 0.5,
    ])
    assert result.trade_log["slippage"].tolist() == pytest.approx([
        entry_notional * 0.01,
        second_cost * 0.5,
    ])
    assert result.trade_log["cost"].tolist() == pytest.approx([
        entry_notional * cost_rate,
        second_cost,
    ])

    expected_total_return = (1.0 + ret0) * (1.0 + ret1) * (1.0 + ret2) - 1.0
    assert result.metrics["total_return"] == pytest.approx(expected_total_return)
    assert result.metrics["total_turnover"] == pytest.approx(result.trade_log["turnover"].sum())
    assert result.metrics["total_fees"] == pytest.approx(result.trade_log["fee"].sum())
    assert result.metrics["total_slippage"] == pytest.approx(result.trade_log["slippage"].sum())
    assert result.metrics["total_cost"] == pytest.approx(result.trade_log["cost"].sum())
    assert result.metrics["Turnover"] == pytest.approx(
        result.trade_log["turnover"].sum() / 3.0 * 365.25
    )


def test_full_cash_entry_and_exit_charge_full_turnover() -> None:
    panel = pd.DataFrame(
        [
            {"date": "2024-01-01", "symbol": "A", "in_universe": True, "open": 100.0, "final_score": 1.0},
            {"date": "2024-01-02", "symbol": "A", "in_universe": False, "open": 100.0, "final_score": 0.0},
            {"date": "2024-01-03", "symbol": "A", "in_universe": False, "open": 100.0, "final_score": 0.0},
        ]
    ).set_index(["date", "symbol"])

    result = run_live_pool_rotation_backtest(
        panel,
        top_n=1,
        rebalance_every=1,
        signal_lag_days=0,
        fee_bps=100,
    )

    entry = 1.0 / 1.01
    assert result.trade_log["turnover"].tolist() == pytest.approx([entry / 2.0, 0.5])
    assert result.trade_log["fee"].tolist() == pytest.approx([entry * 0.01, entry * 0.01])


def test_full_asset_replacement_has_unit_turnover() -> None:
    panel = _panel(
        [
            ("2024-01-01", "A", 100.0, 1.0),
            ("2024-01-01", "B", 100.0, 0.0),
            ("2024-01-02", "A", 100.0, 0.0),
            ("2024-01-02", "B", 100.0, 1.0),
            ("2024-01-03", "A", 100.0, 0.0),
            ("2024-01-03", "B", 100.0, 1.0),
        ]
    )

    result = run_live_pool_rotation_backtest(
        panel,
        top_n=1,
        rebalance_every=1,
        signal_lag_days=0,
    )

    assert result.trade_log["turnover"].tolist() == pytest.approx([0.5, 1.0])


@pytest.mark.parametrize(("fee_bps", "fee_rate"), [(10, 0.01), (0, 0.001)])
def test_runner_rejects_conflicting_fee_aliases(fee_bps: float, fee_rate: float) -> None:
    panel = _panel([
        ("2024-01-01", "A", 100.0, 1.0),
        ("2024-01-02", "A", 100.0, 1.0),
        ("2024-01-03", "A", 100.0, 1.0),
    ]).reset_index()
    panel["date"] = pd.to_datetime(panel["date"])
    runner = CryptoLivePoolBacktestRunner(panel=panel.set_index(["date", "symbol"]))

    with pytest.raises(ValueError, match="fee_rate and fee_bps disagree"):
        runner.run(PROFILE_NAME, {"fee_bps": fee_bps, "fee_rate": fee_rate})


def test_runner_accepts_consistent_fee_aliases() -> None:
    panel = _panel([
        ("2024-01-01", "A", 100.0, 1.0),
        ("2024-01-02", "A", 100.0, 1.0),
        ("2024-01-03", "A", 100.0, 1.0),
    ]).reset_index()
    panel["date"] = pd.to_datetime(panel["date"])
    indexed_panel = panel.set_index(["date", "symbol"])

    both_aliases = CryptoLivePoolBacktestRunner(panel=indexed_panel)
    fee_bps_only = CryptoLivePoolBacktestRunner(panel=indexed_panel)

    both_result = both_aliases.run(PROFILE_NAME, {"fee_bps": 10, "fee_rate": 0.001})
    bps_result = fee_bps_only.run(PROFILE_NAME, {"fee_bps": 10})

    assert both_result.total_return == pytest.approx(bps_result.total_return)
    pd.testing.assert_series_equal(both_aliases.last_daily_returns, fee_bps_only.last_daily_returns)


def test_runner_preserves_missing_fee_aliases() -> None:
    panel = _panel([
        ("2024-01-01", "A", 100.0, 1.0),
        ("2024-01-02", "A", 100.0, 1.0),
        ("2024-01-03", "A", 100.0, 1.0),
    ]).reset_index()
    panel["date"] = pd.to_datetime(panel["date"])
    indexed_panel = panel.set_index(["date", "symbol"])

    fee_rate_only = CryptoLivePoolBacktestRunner(panel=indexed_panel)
    fee_bps_only = CryptoLivePoolBacktestRunner(panel=indexed_panel)
    default_fee = CryptoLivePoolBacktestRunner(panel=indexed_panel)
    zero_fee = CryptoLivePoolBacktestRunner(panel=indexed_panel)

    rate_result = fee_rate_only.run(PROFILE_NAME, {"fee_rate": 0.001})
    bps_result = fee_bps_only.run(PROFILE_NAME, {"fee_bps": 10})
    default_result = default_fee.run(PROFILE_NAME, {})
    zero_result = zero_fee.run(PROFILE_NAME, {"fee_bps": 0})

    assert rate_result.total_return == pytest.approx(bps_result.total_return)
    assert default_result.total_return == pytest.approx(zero_result.total_return)
    pd.testing.assert_series_equal(fee_rate_only.last_daily_returns, fee_bps_only.last_daily_returns)
    pd.testing.assert_series_equal(default_fee.last_daily_returns, zero_fee.last_daily_returns)


@pytest.mark.parametrize("final_open", [100.0, 200.0])
def test_cost_that_wipes_out_portfolio_fails_closed(final_open: float) -> None:
    panel = _panel(
        [
            ("2024-01-01", "A", 100.0, 1.0),
            ("2024-01-02", "A", 100.0, 1.0),
            ("2024-01-03", "A", final_open, 1.0),
        ]
    )

    with pytest.raises(ValueError, match="transaction cost must be less than 1.0"):
        run_live_pool_rotation_backtest(panel, top_n=1, fee_bps=20_000)


def test_synthetic_panel_digest_is_stable_across_hash_seeds() -> None:
    code = (
        "import hashlib, sys; "
        "sys.path.insert(0, 'src'); "
        "from crypto_strategies.backtest.orchestrator_runner import _synthetic_panel; "
        "import pandas as pd; "
        "print(hashlib.sha256(pd.util.hash_pandas_object("
        "_synthetic_panel(days=8), index=True).values.tobytes()).hexdigest())"
    )

    def digest(seed: int) -> str:
        env = {**os.environ, "PYTHONHASHSEED": str(seed)}
        return subprocess.check_output([sys.executable, "-c", code], env=env, text=True).strip()

    assert digest(1) == digest(2)
    assert digest(1) == digest(1)


def test_research_dependencies_are_optional_only() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert {"numpy", "pandas"} <= {
        requirement.split(">", 1)[0].split("=", 1)[0].split("<", 1)[0]
        for requirement in project["optional-dependencies"]["research"]
    }
    assert not {"numpy", "pandas"} & {
        requirement.split(">", 1)[0].split("=", 1)[0].split("<", 1)[0]
        for requirement in project["dependencies"]
    }

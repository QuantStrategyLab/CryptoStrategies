from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pandas as pd
import pytest

from crypto_strategies.backtest.live_pool_simulator import run_live_pool_rotation_backtest
from crypto_strategies.backtest.orchestrator_runner import _synthetic_panel


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

    expected_returns = [0.49, 1.0 / 3.0, 0.495]
    assert result.returns.tolist() == pytest.approx(expected_returns)

    # After +100% then +50% in A, actual weights drift 1/2 -> 2/3 -> 3/4.
    # Rebalancing from (3/4, 1/4) to (1/2, 1/2) is half-L1 turnover 1/4.
    assert result.trade_log["turnover"].tolist() == pytest.approx([0.5, 0.25])
    assert result.trade_log["fee"].tolist() == pytest.approx([0.005, 0.0025])
    assert result.trade_log["slippage"].tolist() == pytest.approx([0.005, 0.0025])
    assert result.trade_log["cost"].tolist() == pytest.approx([0.01, 0.005])

    expected_total_return = (1.49 * (4.0 / 3.0) * 1.495) - 1.0
    assert result.metrics["total_return"] == pytest.approx(expected_total_return)
    assert result.metrics["total_turnover"] == pytest.approx(result.trade_log["turnover"].sum())
    assert result.metrics["total_fees"] == pytest.approx(result.trade_log["fee"].sum())
    assert result.metrics["total_slippage"] == pytest.approx(result.trade_log["slippage"].sum())
    assert result.metrics["total_cost"] == pytest.approx(result.trade_log["cost"].sum())
    assert result.metrics["Turnover"] == pytest.approx(0.75 / 3.0 * 365.25)


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

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import scripts.run_walk_forward_backtest as walk_forward
import crypto_strategies.backtest.orchestrator_runner as orchestrator_runner
from scripts.run_walk_forward_backtest import _baseline_from_return_tail, _baseline_param_set_id, run_walk_forward


def test_run_walk_forward_persists_lifecycle_baseline(tmp_path: Path) -> None:
    payload = run_walk_forward(
        profile="crypto_live_pool_rotation",
        synthetic_days=2200,
        store_root=tmp_path,
    )

    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "backtest" / "crypto" / "crypto_live_pool_rotation").glob("*.json")
    ]

    assert payload["baseline"]["sharpe_ratio"] is not None
    baseline_records = [record for record in records if "_baseline_" in record["param_set_id"]]
    assert baseline_records
    assert baseline_records[-1]["params"] == {"min_history_days": 120, "top_n": 2, "rebalance_every": 7}
    assert not any("_wf" in record["param_set_id"] for record in records)


def test_baseline_param_set_id_tracks_synthetic_days() -> None:
    first = _baseline_param_set_id(
        "crypto_live_pool_rotation",
        {"min_history_days": 120, "top_n": 2, "rebalance_every": 7},
        synthetic_days=2200,
    )
    second = _baseline_param_set_id(
        "crypto_live_pool_rotation",
        {"min_history_days": 120, "top_n": 2, "rebalance_every": 7},
        synthetic_days=2600,
    )

    assert first != second


def test_run_walk_forward_does_not_persist_partial_results_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(BacktestOrchestrator, "walk_forward", _raise)
    with pytest.raises(RuntimeError, match="boom"):
        run_walk_forward(
            profile="crypto_live_pool_rotation",
            synthetic_days=2200,
            store_root=tmp_path,
        )
    assert not list(tmp_path.rglob("*.json"))


def test_run_walk_forward_keeps_local_default_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(walk_forward, "DEFAULT_STORE_ROOT", tmp_path)

    run_walk_forward(profile="crypto_live_pool_rotation", synthetic_days=2200)

    assert list(tmp_path.rglob("*.json"))


def test_run_walk_forward_uses_real_panel_and_writes_return_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = pd.date_range("2022-01-01", "2024-12-31", freq="D")
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    rows = []
    market_rows = []
    for symbol_index, symbol in enumerate(symbols):
        for day_index, day in enumerate(dates):
            price = 100.0 + symbol_index * 10 + day_index * (0.1 + symbol_index / 100)
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "in_universe": True,
                    "open": price,
                    "final_score": float((day_index + symbol_index) % 10) / 10,
                }
            )
            if symbol in {"BTCUSDT", "ETHUSDT"}:
                market_rows.append({"date": day, "symbol": symbol, "close": price})
    panel = pd.DataFrame(rows)
    market_history = pd.DataFrame(market_rows)
    monkeypatch.setattr(
        orchestrator_runner,
        "_synthetic_panel",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("synthetic panel must not be used")),
    )
    returns_output = tmp_path / "returns" / "portfolio_and_tracker_returns.csv"

    payload = run_walk_forward(
        profile="crypto_live_pool_rotation",
        windows=(
            (pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-06-30").date()),
            (pd.Timestamp("2024-07-01").date(), pd.Timestamp("2024-12-31").date()),
        ),
        store_root=tmp_path / "store",
        panel=panel,
        market_history=market_history,
        returns_output=returns_output,
    )

    return_matrix = pd.read_csv(returns_output)
    assert payload["baseline"]["observation_count"] == 126
    assert {"as_of", "crypto_live_pool_rotation", "buy_hold_BTC"} <= set(return_matrix.columns)
    assert len(return_matrix) > payload["baseline"]["observation_count"]


def test_baseline_uses_exact_tail_of_full_return_stream() -> None:
    from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult

    index = pd.date_range("2024-01-01", periods=200, freq="D")
    returns = pd.Series(range(200), index=index, dtype=float) / 100000
    full_result = BacktestResult(
        strategy_profile="crypto_live_pool_rotation", domain="crypto", param_set_id="", params={}
    )

    baseline = _baseline_from_return_tail(full_result, returns)

    expected = returns.tail(126)
    assert baseline.start_date == expected.index.min().date()
    assert baseline.end_date == expected.index.max().date()
    assert baseline.observation_count == len(expected)

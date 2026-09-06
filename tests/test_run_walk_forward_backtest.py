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
from scripts.run_walk_forward_backtest import (
    _baseline_from_return_tail,
    _baseline_param_set_id,
    _normalize_market_history,
    _normalize_panel,
    run_walk_forward,
)


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
    dates = pd.date_range("2022-01-01", "2025-02-28", freq="D")
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
    assert pd.Timestamp(return_matrix["as_of"].max()) > pd.Timestamp("2024-12-31")


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


def test_external_inputs_reject_duplicate_keys() -> None:
    duplicate_panel = pd.DataFrame(
        [
            {"date": "2024-01-01", "symbol": "BTCUSDT", "in_universe": True, "open": 1, "final_score": 1},
            {"date": "2024-01-01", "symbol": "BTCUSDT", "in_universe": True, "open": 2, "final_score": 2},
        ]
    )
    duplicate_history = pd.DataFrame(
        [
            {"date": "2024-01-01", "symbol": "BTCUSDT", "close": 1},
            {"date": "2024-01-01", "symbol": "BTCUSDT", "close": 2},
        ]
    )

    with pytest.raises(ValueError, match="research panel contains duplicate"):
        _normalize_panel(duplicate_panel)
    with pytest.raises(ValueError, match="market history contains duplicate"):
        _normalize_market_history(duplicate_history)


def test_normalized_panel_preserves_unscored_open_rows() -> None:
    panel = pd.DataFrame(
        [
            {
                "date": "2024-01-01",
                "symbol": "BTCUSDT",
                "in_universe": False,
                "open": 100.0,
                "final_score": None,
            }
        ]
    )

    normalized = _normalize_panel(panel)

    assert len(normalized) == 1
    assert pd.isna(normalized.iloc[0]["final_score"])


@pytest.mark.parametrize("opening", [None, "unavailable"])
def test_normalized_panel_preserves_missing_prices_but_still_drops_invalid_dates(opening) -> None:
    panel = pd.DataFrame([
        {"date": "2024-01-01", "symbol": " a ", "in_universe": True, "open": opening, "final_score": 1},
        {"date": "invalid", "symbol": "B", "in_universe": True, "open": opening, "final_score": 1},
    ])

    normalized = _normalize_panel(panel)

    assert normalized.index.tolist() == [(pd.Timestamp("2024-01-01"), "A")]
    assert pd.isna(normalized.iloc[0]["open"])


@pytest.mark.parametrize("opening", [None, "unavailable"])
def test_cli_rejects_whole_missing_price_day_without_success_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, opening,
) -> None:
    dates = pd.date_range(walk_forward.DEFAULT_WINDOWS[0][0], walk_forward.DEFAULT_WINDOWS[-1][1])
    panel = pd.DataFrame([
        {"date": day, "symbol": symbol, "in_universe": True, "open": 100.0, "final_score": score}
        for day in dates
        for symbol, score in [("BTCUSDT", 3), ("ETHUSDT", 2), ("SOLUSDT", 1)]
    ])
    history = panel.loc[panel["symbol"].isin(["BTCUSDT", "ETHUSDT"]), ["date", "symbol", "open"]]
    history = history.rename(columns={"open": "close"})
    panel["open"] = panel["open"].astype(object)
    panel.loc[panel["date"] == dates[40], "open"] = opening
    panel_path = tmp_path / "panel.csv"
    history_path = tmp_path / "history.csv"
    output_path = tmp_path / "result.json"
    returns_path = tmp_path / "returns.csv"
    store = tmp_path / "store"
    panel.to_csv(panel_path, index=False)
    history.to_csv(history_path, index=False)
    monkeypatch.setattr(sys, "argv", [
        "run_walk_forward_backtest.py", "--panel", str(panel_path),
        "--market-history", str(history_path), "--store-root", str(store),
        "--json-output", str(output_path), "--returns-output", str(returns_path),
    ])

    with pytest.raises(ValueError, match="required open prices must be finite and positive"):
        walk_forward.main()

    assert not output_path.exists()
    assert not returns_path.exists()
    assert not list(store.rglob("*.json"))
    assert not capsys.readouterr().out


@pytest.mark.parametrize("cash_first", [False, True])
def test_normalized_missing_unexposed_prices_preserve_runner_periods(cash_first: bool) -> None:
    dates = pd.date_range("2024-01-01", periods=5)
    panel = pd.DataFrame([
        {"date": day, "symbol": symbol, "in_universe": True, "open": 100.0, "final_score": score}
        for day in dates for symbol, score in [("A", 1), ("B", 0)]
    ])
    panel.loc[panel["symbol"] == "B", "open"] = float("nan")
    if cash_first:
        panel.loc[panel["date"] < dates[2], "in_universe"] = False
        panel.loc[panel["date"] < dates[3], "open"] = float("nan")
    runner = orchestrator_runner.CryptoLivePoolBacktestRunner(panel=_normalize_panel(panel))

    result = runner.run("crypto_live_pool_rotation", {"top_n": 1, "rebalance_every": 1})

    assert result.observation_count == 3
    assert runner.last_daily_returns.index.tolist() == dates[1:-1].tolist()
    assert runner.last_daily_returns.tolist() == [0.0, 0.0, 0.0]

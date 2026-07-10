from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.run_walk_forward_backtest import _baseline_param_set_id, run_walk_forward


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
    assert any("_wf" in record["param_set_id"] for record in records)


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

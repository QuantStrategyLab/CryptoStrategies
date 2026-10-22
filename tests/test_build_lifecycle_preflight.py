from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

from scripts import build_lifecycle_preflight as lifecycle


@pytest.mark.parametrize("layout", ["store", "flat"])
def test_exports_single_flat_baseline_without_changing_payload(tmp_path, monkeypatch, layout):
    original_bytes = {}
    original_matrix_bytes = {}
    panel = pd.DataFrame({"value": [1.5]})
    market_history = pd.DataFrame({"close": [100.5]})

    def persist_baseline(*, profile, store_root, returns_output, panel, market_history):
        assert panel.iloc[0, 0] == 1.5
        assert market_history.iloc[0, 0] == 100.5
        panel.iloc[0, 0] = 9.5
        market_history.iloc[0, 0] = 200.5
        baseline = BacktestResult(
            strategy_profile=profile,
            domain="crypto",
            run_id=f"{profile}-baseline",
            param_set_id=f"{profile}_baseline",
            params={"top_n": 2},
            computed_at="2026-10-22T12:00:00+00:00",
        )
        PerformanceStore(local_root=store_root).save_backtest_result(baseline)
        directory = store_root / "backtest" / "crypto" / profile
        path = next(directory.rglob("backtest_*.json"))
        if layout == "flat":
            path = path.replace(directory / "backtest_v1.json")
        original_bytes[profile] = path.read_bytes()
        returns_output.parent.mkdir(parents=True)
        matrix_bytes = f"as_of,{profile},buy_hold_BTC\n2026-10-20,0.01,0.02\n2026-10-21,-0.02,-0.01\n".encode()
        returns_output.write_bytes(matrix_bytes)
        original_matrix_bytes[profile] = matrix_bytes
        return {"baseline": {"run_id": baseline.run_id}}

    monkeypatch.setattr(lifecycle, "run_walk_forward", persist_baseline)
    result = lifecycle.build_lifecycle_preflight_bundle(
        panel=panel, market_history=market_history, bundle_root=tmp_path
    )

    assert result["profiles"] == sorted(lifecycle.SUPPORTED_PROFILES)
    assert panel.iloc[0, 0] == 1.5
    assert market_history.iloc[0, 0] == 100.5
    for profile in result["profiles"]:
        directory = tmp_path / "data" / "lifecycle_store" / "backtest" / "crypto" / profile
        paths = list(directory.rglob("backtest_*.json"))
        assert len(paths) == 1
        assert paths[0].parent == directory
        assert paths[0].read_bytes() == original_bytes[profile]
        assert json.loads(paths[0].read_text())["run_id"] == f"{profile}-baseline"
        matrix = tmp_path / "external" / "CryptoLivePoolPipelines" / "data" / "output" / profile / "portfolio_and_tracker_returns.csv"
        assert matrix.read_bytes() == original_matrix_bytes[profile]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "run_id", "profile", "domain", "empty_run"])
def test_rejects_missing_duplicate_or_mismatched_baseline(tmp_path, monkeypatch, fault):
    def persist_baseline(*, profile, store_root, **_kwargs):
        run_id = f"{profile}-baseline"
        if fault != "missing":
            baseline = BacktestResult(
                strategy_profile=profile,
                domain="crypto",
                run_id=run_id,
                param_set_id=f"{profile}_baseline",
                params={},
                computed_at="2026-10-22T12:00:00+00:00",
            )
            PerformanceStore(local_root=store_root).save_backtest_result(baseline)
            directory = store_root / "backtest" / "crypto" / profile
            path = next(directory.rglob("backtest_*.json"))
            payload = json.loads(path.read_text())
            if fault in {"run_id", "profile", "domain"}:
                key = "strategy_profile" if fault == "profile" else fault
                payload[key] = "another-value"
                path.write_text(json.dumps(payload))
            if fault == "duplicate":
                (directory / "backtest_duplicate.json").write_bytes(path.read_bytes())
        return {"baseline": {"run_id": "" if fault == "empty_run" else run_id}}

    monkeypatch.setattr(lifecycle, "run_walk_forward", persist_baseline)
    with pytest.raises(RuntimeError, match="one persisted lifecycle baseline"):
        lifecycle.build_lifecycle_preflight_bundle(
            panel=pd.DataFrame(), market_history=pd.DataFrame(), bundle_root=tmp_path
        )


def test_cli_requires_real_input_paths():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "build_lifecycle_preflight.py")],
        cwd=root,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "src"), os.environ.get("PYTHONPATH", "")])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--panel" in result.stderr
    assert "--market-history" in result.stderr
    assert "--bundle-root" in result.stderr

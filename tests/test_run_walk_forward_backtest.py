from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.run_walk_forward_backtest import run_walk_forward


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
    assert baseline_records[-1]["params"]["_qpk_pin"]
    assert baseline_records[-1]["params"]["_baseline_end_date"]
    assert any("_wf" in record["param_set_id"] for record in records)

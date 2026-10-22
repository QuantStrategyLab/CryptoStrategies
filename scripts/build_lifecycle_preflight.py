#!/usr/bin/env python3
"""Build the monitor's flat lifecycle bundle from real crypto input files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

try:
    from .run_walk_forward_backtest import SUPPORTED_PROFILES, run_walk_forward
except ImportError:
    from run_walk_forward_backtest import SUPPORTED_PROFILES, run_walk_forward


def build_lifecycle_preflight_bundle(
    *, panel: pd.DataFrame, market_history: pd.DataFrame, bundle_root: Path
) -> dict[str, Any]:
    store_root = bundle_root / "data" / "lifecycle_store"
    profiles = sorted(SUPPORTED_PROFILES)
    for profile in profiles:
        returns_output = (
            bundle_root / "external" / "CryptoLivePoolPipelines" / "data" / "output"
            / profile / "portfolio_and_tracker_returns.csv"
        )
        result = run_walk_forward(
            profile=profile,
            store_root=store_root,
            panel=panel.copy(deep=True),
            market_history=market_history.copy(deep=True),
            returns_output=returns_output,
        )
        directory = store_root / "backtest" / "crypto" / profile
        baseline_paths = list(directory.rglob("backtest_*.json"))
        expected_run_id = result["baseline"]["run_id"]
        baseline = PerformanceStore(local_root=store_root).load_latest_backtest("crypto", profile)
        if (
            len(baseline_paths) != 1
            or not isinstance(expected_run_id, str)
            or not expected_run_id.strip()
            or baseline is None
            or baseline.run_id != expected_run_id
            or baseline.domain != "crypto"
            or baseline.strategy_profile != profile
        ):
            raise RuntimeError(f"expected one persisted lifecycle baseline for profile={profile!r}")
        export_path = directory / f"backtest_v{baseline.param_version}.json"
        if baseline_paths[0] != export_path:
            baseline_paths[0].replace(export_path)
    return {"profiles": profiles}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--market-history", required=True, type=Path)
    parser.add_argument("--bundle-root", required=True, type=Path)
    args = parser.parse_args()
    result = build_lifecycle_preflight_bundle(
        panel=pd.read_csv(args.panel, compression="infer"),
        market_history=pd.read_csv(args.market_history, compression="infer"),
        bundle_root=args.bundle_root,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

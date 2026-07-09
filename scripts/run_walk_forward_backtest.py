#!/usr/bin/env python3
"""Run walk-forward backtests via QuantPlatformKit BacktestOrchestrator."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from crypto_strategies.backtest.orchestrator_runner import (
    DEFAULT_MIN_HISTORY_DAYS,
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    CryptoLivePoolBacktestRunner,
)

DEFAULT_WINDOWS: tuple[tuple[date, date], ...] = (
    (date(2023, 6, 1), date(2024, 5, 31)),
    (date(2024, 6, 1), date(2025, 5, 31)),
)

PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    PROFILE_NAME: {"min_history_days": DEFAULT_MIN_HISTORY_DAYS, "top_n": 2, "rebalance_every": 7},
}


def _result_payload(item: Any) -> dict[str, Any]:
    return {
        "start_date": item.start_date.isoformat() if item.start_date else None,
        "end_date": item.end_date.isoformat() if item.end_date else None,
        "sharpe_ratio": item.sharpe_ratio,
        "max_drawdown": item.max_drawdown,
        "cagr": item.cagr,
        "total_return": item.total_return,
        "observation_count": item.observation_count,
        "run_id": getattr(item, "run_id", None),
    }


def run_walk_forward(
    *,
    profile: str,
    windows: tuple[tuple[date, date], ...] = DEFAULT_WINDOWS,
    synthetic_days: int = 1600,
    store_root: Path | None = None,
) -> dict[str, Any]:
    from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported profile={profile!r}; supported={sorted(SUPPORTED_PROFILES)}")

    params = dict(PROFILE_DEFAULTS.get(profile, {"min_history_days": DEFAULT_MIN_HISTORY_DAYS}))
    runner = CryptoLivePoolBacktestRunner(synthetic_days=synthetic_days)
    store = PerformanceStore(local_root=store_root or Path("/tmp/crypto_wf_store"))
    orchestrator = BacktestOrchestrator(store=store)
    orchestrator.register_runner("crypto", runner)

    baseline = runner.run(profile, params)
    wf_results = orchestrator.walk_forward(
        profile,
        domain="crypto",
        params=params,
        windows=windows,
        param_set_id=f"{profile}_wf",
    )
    return {
        "strategy_profile": profile,
        "domain": "crypto",
        "baseline": _result_payload(baseline),
        "walk_forward_folds": [_result_payload(item) for item in wf_results],
        "source": "BacktestOrchestrator.walk_forward",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Crypto walk-forward backtest via BacktestOrchestrator.")
    parser.add_argument("--profile", default=PROFILE_NAME)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--synthetic-days", type=int, default=1600)
    parser.add_argument("--store-root", type=Path)
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({"profiles": sorted(SUPPORTED_PROFILES)}, indent=2))
        return 0

    payload = run_walk_forward(
        profile=args.profile,
        synthetic_days=args.synthetic_days,
        store_root=args.store_root,
    )
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

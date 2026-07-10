#!/usr/bin/env python3
"""Run walk-forward backtests via QuantPlatformKit BacktestOrchestrator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from crypto_strategies.backtest.orchestrator_runner import (
    COMBO_DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_MIN_HISTORY_DAYS,
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    build_backtest_runner,
)
from crypto_strategies.strategies.crypto_equity_combo import PROFILE_NAME as CRYPTO_EQUITY_COMBO_PROFILE

DEFAULT_WINDOWS: tuple[tuple[date, date], ...] = (
    (date(2023, 6, 1), date(2024, 5, 31)),
    (date(2024, 6, 1), date(2025, 5, 31)),
)

PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    PROFILE_NAME: {"min_history_days": DEFAULT_MIN_HISTORY_DAYS, "top_n": 2, "rebalance_every": 7},
    CRYPTO_EQUITY_COMBO_PROFILE: {
        "min_history_days": COMBO_DEFAULT_MIN_HISTORY_DAYS,
        "combo_mode": "dynamic",
    },
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


def _baseline_param_set_id(profile: str, params: dict[str, Any]) -> str:
    fingerprint = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    return f"{profile}_baseline_{fingerprint}"


def _current_qpk_pin() -> str:
    text = (Path(__file__).resolve().parents[1] / "qsl.toml").read_text(encoding="utf-8")
    match = re.search(r"QuantPlatformKit\.git@([0-9a-f]{40})", text)
    return match.group(1) if match else "unknown"


def _baseline_identity_params(
    params: dict[str, Any],
    *,
    synthetic_days: int,
    baseline_result: Any,
) -> dict[str, Any]:
    identity = copy.deepcopy(params)
    identity["_baseline_start_date"] = baseline_result.start_date.isoformat() if baseline_result.start_date else None
    identity["_baseline_end_date"] = baseline_result.end_date.isoformat() if baseline_result.end_date else None
    identity["_qpk_pin"] = _current_qpk_pin()
    identity["_synthetic_days"] = synthetic_days
    return identity


def _build_runner(*, profile: str, synthetic_days: int, panel: Any = None, market_history: Any = None):
    return build_backtest_runner(
        profile,
        panel=panel,
        market_history=market_history,
        synthetic_days=synthetic_days,
    )


def run_walk_forward(
    *,
    profile: str,
    windows: tuple[tuple[date, date], ...] = DEFAULT_WINDOWS,
    synthetic_days: int = 1600,
    store_root: Path | None = None,
    panel: Any = None,
    market_history: Any = None,
) -> dict[str, Any]:
    from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported profile={profile!r}; supported={sorted(SUPPORTED_PROFILES)}")

    params = dict(PROFILE_DEFAULTS.get(profile, {"min_history_days": DEFAULT_MIN_HISTORY_DAYS}))
    store = PerformanceStore(local_root=store_root) if store_root is not None else PerformanceStore.from_env()
    orchestrator = BacktestOrchestrator(store=store)

    baseline_params = copy.deepcopy(params)
    runner = _build_runner(
        profile=profile,
        panel=panel,
        market_history=market_history,
        synthetic_days=synthetic_days,
    )
    orchestrator.register_runner("crypto", runner)
    baseline_probe = orchestrator.run(
        profile,
        domain="crypto",
        params=copy.deepcopy(baseline_params),
        param_set_id="__discarded__",
        start_date=None,
        end_date=None,
    )
    baseline_store_params = _baseline_identity_params(
        baseline_params,
        synthetic_days=synthetic_days,
        baseline_result=baseline_probe,
    )
    baseline = orchestrator.run(
        profile,
        domain="crypto",
        params=baseline_store_params,
        param_set_id=_baseline_param_set_id(profile, baseline_store_params),
        start_date=None,
        end_date=None,
    )
    wf_params = copy.deepcopy(params)
    wf_results = orchestrator.walk_forward(
        profile,
        domain="crypto",
        params=wf_params,
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

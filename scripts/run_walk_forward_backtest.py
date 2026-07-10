#!/usr/bin/env python3
"""Run walk-forward backtests via QuantPlatformKit BacktestOrchestrator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
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
DEFAULT_STORE_ROOT = Path("/tmp/crypto_wf_store")

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


def _baseline_param_set_id(profile: str, params: dict[str, Any], *, synthetic_days: int) -> str:
    identity = {
        "params": params,
        "synthetic_days": synthetic_days,
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    return f"{profile}_baseline_{fingerprint}"


def _build_runner(*, profile: str, synthetic_days: int, panel: Any = None, market_history: Any = None):
    return build_backtest_runner(
        profile,
        panel=panel,
        market_history=market_history,
        synthetic_days=synthetic_days,
    )


def _run_baseline(runner: Any, profile: str, params: dict[str, Any]) -> Any:
    try:
        return runner.run(
            profile,
            params,
            start_date=None,
            end_date=None,
        )
    except TypeError as exc:
        message = str(exc)
        if "start_date" not in message and "end_date" not in message:
            raise
        return runner.run(profile, params)


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
    store = PerformanceStore(local_root=store_root or DEFAULT_STORE_ROOT)
    orchestrator = BacktestOrchestrator(store=store)

    baseline_params = copy.deepcopy(params)
    runner = _build_runner(
        profile=profile,
        panel=panel,
        market_history=market_history,
        synthetic_days=synthetic_days,
    )
    orchestrator.register_runner("crypto", runner)
    baseline_raw = _run_baseline(runner, profile, copy.deepcopy(baseline_params))
    wf_params = copy.deepcopy(params)
    wf_results = orchestrator.walk_forward(
        profile,
        domain="crypto",
        params=wf_params,
        windows=windows,
        param_set_id=f"{profile}_wf",
    )
    baseline = orchestrator.persist_result(
        baseline_raw,
        strategy_profile=profile,
        domain="crypto",
        params=baseline_params,
        param_set_id=_baseline_param_set_id(
            profile,
            baseline_params,
            synthetic_days=synthetic_days,
        ),
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

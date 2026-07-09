#!/usr/bin/env python3
"""Generic crypto orchestrator research entrypoint (task 3c)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_strategies.backtest.orchestrator_runner import (  # noqa: E402
    DEFAULT_MIN_HISTORY_DAYS,
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    CryptoLivePoolBacktestRunner,
)
from scripts.run_walk_forward_backtest import run_walk_forward  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Crypto orchestrator research backtest.")
    parser.add_argument("--profile", default=PROFILE_NAME)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--mode", choices=("single", "walk_forward"), default="walk_forward")
    parser.add_argument("--synthetic-days", type=int, default=1600)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({"profiles": sorted(SUPPORTED_PROFILES)}, indent=2))
        return 0

    if args.mode == "walk_forward":
        payload = run_walk_forward(profile=args.profile, synthetic_days=args.synthetic_days)
    else:
        runner = CryptoLivePoolBacktestRunner(synthetic_days=args.synthetic_days)
        params = {"min_history_days": DEFAULT_MIN_HISTORY_DAYS, "top_n": 2, "rebalance_every": 7}
        result = runner.run(args.profile, params)
        payload = {
            "profile": args.profile,
            "metrics": {
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown,
                "cagr": result.cagr,
            },
            "source": "CryptoLivePoolBacktestRunner",
        }

    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.json_output:
        args.json_output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

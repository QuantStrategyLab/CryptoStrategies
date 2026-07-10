#!/usr/bin/env python3
"""Run walk-forward backtests via QuantPlatformKit BacktestOrchestrator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_strategies.backtest.orchestrator_runner import (
    COMBO_DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_MIN_HISTORY_DAYS,
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    build_backtest_runner,
)
from crypto_strategies.backtest.live_pool_simulator import _performance_metrics
from crypto_strategies.strategies.crypto_equity_combo import PROFILE_NAME as CRYPTO_EQUITY_COMBO_PROFILE

DEFAULT_WINDOWS: tuple[tuple[date, date], ...] = (
    (date(2023, 6, 1), date(2024, 5, 31)),
    (date(2024, 6, 1), date(2025, 5, 31)),
)
DEFAULT_STORE_ROOT = Path("/tmp/crypto_wf_store")
DRIFT_BASELINE_HORIZON_DAYS = 126

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


def _baseline_param_set_id(
    profile: str,
    params: dict[str, Any],
    *,
    synthetic_days: int,
    windows: tuple[tuple[date, date], ...] = DEFAULT_WINDOWS,
    data_fingerprint: str = "",
) -> str:
    identity = {
        "params": params,
        "data_fingerprint": data_fingerprint or f"synthetic:{synthetic_days}",
        "windows": [(start.isoformat(), end.isoformat()) for start, end in windows],
        "drift_baseline_horizon_days": DRIFT_BASELINE_HORIZON_DAYS,
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


def _normalize_panel(panel: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(panel).copy()
    if isinstance(panel.index, pd.MultiIndex) and list(panel.index.names) == ["date", "symbol"]:
        frame = panel.reset_index()
    required = {"date", "symbol", "in_universe", "open", "final_score"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"research panel is missing columns: {', '.join(missing)}")
    frame = frame[["date", "symbol", "in_universe", "open", "final_score"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["final_score"] = pd.to_numeric(frame["final_score"], errors="coerce")
    frame["in_universe"] = frame["in_universe"].astype(str).str.lower().isin({"true", "1"})
    frame = frame.dropna(subset=["date", "symbol", "open", "final_score"])
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("research panel contains duplicate date/symbol rows")
    return frame.set_index(["date", "symbol"]).sort_index()


def _normalize_market_history(market_history: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(market_history).copy()
    if "date" not in frame.columns and "as_of" in frame.columns:
        frame = frame.rename(columns={"as_of": "date"})
    required = {"date", "symbol", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"market history is missing columns: {', '.join(missing)}")
    frame = frame[["date", "symbol", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna()
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("market history contains duplicate date/symbol rows")
    return frame.sort_values(["date", "symbol"])


def _fingerprint(*frames: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    for frame in frames:
        digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()[:16]


def _shared_inputs(
    *,
    windows: tuple[tuple[date, date], ...],
    panel: pd.DataFrame,
    market_history: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    full_start = min(start for start, _ in windows)
    full_end = max(end for _, end in windows)
    normalized_panel = _normalize_panel(panel)
    panel_dates = normalized_panel.index.get_level_values("date")
    normalized_panel = normalized_panel.loc[
        (panel_dates >= pd.Timestamp(full_start)) & (panel_dates <= pd.Timestamp(full_end))
    ]
    if normalized_panel.empty or normalized_panel.index.get_level_values("date").max() < pd.Timestamp(full_end) - pd.Timedelta(days=2):
        raise ValueError("research panel does not cover the latest walk-forward window")
    if normalized_panel.groupby(level="date")["in_universe"].sum().min() < 2:
        raise ValueError("research panel requires at least two in-universe symbols")

    normalized_history = _normalize_market_history(market_history)
    lookback_start = pd.Timestamp(full_start) - pd.Timedelta(days=COMBO_DEFAULT_MIN_HISTORY_DAYS + 5)
    normalized_history = normalized_history.loc[
        (normalized_history["date"] >= lookback_start)
        & (normalized_history["date"] <= pd.Timestamp(full_end))
    ].copy()
    required_symbols = {"BTCUSDT", "ETHUSDT"}
    missing_symbols = sorted(required_symbols - set(normalized_history["symbol"]))
    if missing_symbols:
        raise ValueError(f"market history is missing required symbols: {', '.join(missing_symbols)}")
    reference_dates = set(normalized_history.loc[normalized_history["symbol"] == "BTCUSDT", "date"])
    for symbol in sorted(required_symbols):
        symbol_dates = set(normalized_history.loc[normalized_history["symbol"] == symbol, "date"])
        if (
            len(symbol_dates & reference_dates) / len(reference_dates) < 0.99
            or min(symbol_dates) > min(reference_dates)
            or max(symbol_dates) < max(reference_dates)
        ):
            raise ValueError(f"market history has incomplete symbol coverage: {symbol}")
    if max(reference_dates) < pd.Timestamp(full_end) - pd.Timedelta(days=2):
        raise ValueError("market history does not cover the latest walk-forward window")
    return normalized_panel, normalized_history, _fingerprint(normalized_panel, normalized_history)


def _write_return_matrix(
    output_path: Path,
    *,
    profile: str,
    returns: pd.Series,
    market_history: pd.DataFrame,
) -> None:
    frame = returns.rename(profile).to_frame()
    benchmark = _normalize_market_history(market_history)
    benchmark = benchmark.loc[benchmark["symbol"] == "BTCUSDT"].set_index("date")["close"].pct_change()
    frame["buy_hold_BTC"] = benchmark.reindex(frame.index)
    frame.index.name = "as_of"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.reset_index().to_csv(output_path, index=False)


def _baseline_from_return_tail(full_result: Any, returns: pd.Series) -> Any:
    tail = returns.tail(DRIFT_BASELINE_HORIZON_DAYS)
    metrics = _performance_metrics(tail)
    max_drawdown = float(metrics["Max Drawdown"])
    cagr = float(metrics["CAGR"])
    return replace(
        full_result,
        sharpe_ratio=float(metrics["Sharpe"]),
        calmar_ratio=abs(cagr / max_drawdown) if max_drawdown else None,
        max_drawdown=max_drawdown,
        cagr=cagr,
        volatility=float(metrics["Annualized Volatility"]),
        win_rate=float(metrics["Win Rate"]),
        total_return=float(metrics["total_return"]),
        start_date=tail.index.min().date(),
        end_date=tail.index.max().date(),
        observation_count=int(metrics["Trading Days"]),
    )


def run_walk_forward(
    *,
    profile: str,
    windows: tuple[tuple[date, date], ...] = DEFAULT_WINDOWS,
    synthetic_days: int = 1600,
    store_root: Path | None = None,
    panel: Any = None,
    market_history: Any = None,
    returns_output: Path | None = None,
) -> dict[str, Any]:
    from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported profile={profile!r}; supported={sorted(SUPPORTED_PROFILES)}")

    params = dict(PROFILE_DEFAULTS.get(profile, {"min_history_days": DEFAULT_MIN_HISTORY_DAYS}))
    target_root = store_root or DEFAULT_STORE_ROOT
    target_root.mkdir(parents=True, exist_ok=True)
    baseline_params = copy.deepcopy(params)
    data_fingerprint = f"synthetic:{synthetic_days}"
    shared_panel = panel
    shared_market_history = market_history
    if panel is not None and market_history is not None:
        shared_panel, shared_market_history, data_fingerprint = _shared_inputs(
            windows=windows,
            panel=panel,
            market_history=market_history,
        )
    return_matrix_runner = _build_runner(
        profile=profile,
        panel=shared_panel,
        market_history=shared_market_history,
        synthetic_days=synthetic_days,
    )
    full_start = min(start for start, _ in windows)
    baseline_end = max(end for _, end in windows)
    full_window_raw = return_matrix_runner.run(
        profile,
        copy.deepcopy(baseline_params),
        start_date=full_start,
        end_date=baseline_end,
    )
    full_window_returns = return_matrix_runner.last_daily_returns
    if len(full_window_returns) < DRIFT_BASELINE_HORIZON_DAYS:
        raise ValueError("full-window returns do not cover the 126-day drift baseline")
    baseline_raw = _baseline_from_return_tail(full_window_raw, full_window_returns)
    with tempfile.TemporaryDirectory(prefix=f"{profile}_wf_", dir=target_root) as scratch_dir:
        scratch_orchestrator = BacktestOrchestrator(store=PerformanceStore(local_root=Path(scratch_dir)))
        scratch_orchestrator.register_runner(
            "crypto",
            _build_runner(
                profile=profile,
                panel=shared_panel,
                market_history=shared_market_history,
                synthetic_days=synthetic_days,
            ),
        )
        wf_results = scratch_orchestrator.walk_forward(
            profile,
            domain="crypto",
            params=copy.deepcopy(params),
            windows=windows,
            param_set_id=f"{profile}_wf",
        )
    orchestrator = BacktestOrchestrator(store=PerformanceStore(local_root=target_root))
    baseline = orchestrator.persist_result(
        baseline_raw,
        strategy_profile=profile,
        domain="crypto",
        params=baseline_params,
        param_set_id=_baseline_param_set_id(
            profile,
            baseline_params,
            synthetic_days=synthetic_days,
            windows=windows,
            data_fingerprint=data_fingerprint,
        ),
    )
    if returns_output is not None:
        if shared_market_history is None:
            raise ValueError("returns_output requires market_history")
        _write_return_matrix(
            returns_output,
            profile=profile,
            returns=full_window_returns,
            market_history=shared_market_history,
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
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--market-history", type=Path)
    parser.add_argument("--returns-output", type=Path)
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({"profiles": sorted(SUPPORTED_PROFILES)}, indent=2))
        return 0

    panel = pd.read_csv(args.panel, compression="infer") if args.panel else None
    market_history = pd.read_csv(args.market_history, compression="infer") if args.market_history else None
    payload = run_walk_forward(
        profile=args.profile,
        synthetic_days=args.synthetic_days,
        store_root=args.store_root,
        panel=panel,
        market_history=market_history,
        returns_output=args.returns_output,
    )
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

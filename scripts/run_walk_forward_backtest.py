#!/usr/bin/env python3
"""Run walk-forward backtests via QuantPlatformKit BacktestOrchestrator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from quant_platform_kit.strategy_lifecycle.performance_metrics import compute_window_metrics

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
DRIFT_BASELINE_HORIZON_DAYS = 126
CRYPTO_PERIODS_PER_YEAR = 365.25
CRYPTO_CALENDAR_ID = "CRYPTO_NATURAL_DAY"

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
    frame = frame.dropna(subset=["date", "symbol"])
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
        panel_dates >= pd.Timestamp(full_start)
    ]
    if normalized_panel.empty or normalized_panel.index.get_level_values("date").max() < pd.Timestamp(full_end) - pd.Timedelta(days=2):
        raise ValueError("research panel does not cover the latest walk-forward window")
    scored_panel = normalized_panel.dropna(subset=["final_score"])
    if scored_panel.groupby(level="date")["in_universe"].sum().min() < 2:
        raise ValueError("research panel requires at least two in-universe symbols")

    normalized_history = _normalize_market_history(market_history)
    lookback_start = pd.Timestamp(full_start) - pd.Timedelta(days=COMBO_DEFAULT_MIN_HISTORY_DAYS + 5)
    current_end = min(
        normalized_panel.index.get_level_values("date").max(),
        normalized_history["date"].max(),
    )
    normalized_history = normalized_history.loc[
        (normalized_history["date"] >= lookback_start)
        & (normalized_history["date"] <= current_end)
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
    metrics = compute_window_metrics(
        tail,
        window_days=DRIFT_BASELINE_HORIZON_DAYS,
        periods_per_year=CRYPTO_PERIODS_PER_YEAR,
        calendar_id=CRYPTO_CALENDAR_ID,
    )
    max_drawdown = float(metrics.max_drawdown)
    cagr = float(metrics.cagr)
    return replace(
        full_result,
        sharpe_ratio=float(metrics.sharpe_ratio),
        calmar_ratio=float(metrics.calmar_ratio),
        max_drawdown=max_drawdown,
        cagr=cagr,
        volatility=float(metrics.volatility),
        win_rate=float(metrics.win_rate),
        total_return=float(metrics.total_return),
        start_date=metrics.start_date,
        end_date=metrics.end_date,
        observation_count=metrics.observation_count,
        periods_per_year=metrics.periods_per_year,
        calendar_id=metrics.calendar_id,
    )



def _content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_sha256(frame: pd.DataFrame) -> str:
    # Includes columns, dtypes, index and data; this is not source authentication.
    digest = hashlib.sha256()
    digest.update(json.dumps({"columns": list(map(str, frame.columns)),
                              "dtypes": list(map(str, frame.dtypes)),
                              "index_names": list(map(str, frame.index.names))},
                             sort_keys=True).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


class _Attempt:
    def __init__(self, directory: Path, *, profile: str, windows, synthetic_days: int):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (uuid.uuid4().hex + ".json")
        self.data = {
            "schema_version": "crypto.walk_forward_attempt.v1",
            "attempt_id": self.path.stem, "status": "started",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "profile": profile if profile in SUPPORTED_PROFILES else "unsupported",
            "params": dict(PROFILE_DEFAULTS.get(profile, {"min_history_days": DEFAULT_MIN_HISTORY_DAYS})),
            "windows": [[start.isoformat(), end.isoformat()] for start, end in windows],
            "synthetic_days": synthetic_days, "family_complete": False,
            "cost_status": "unknown", "data": {"status": "unknown"},
            "source": {"entrypoint_sha256": _content_sha256(Path(__file__)),
                       "dependencies": "unknown", "authenticity": "unverified"},
            "baseline_status": "not_attempted", "returns_status": "not_requested",
            "stage": "input",
            "json_output_status": "not_requested",
        }
        # A same-directory hard link publishes the fully written record without
        # replacing an existing ID. A failed publication never starts the run.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".attempt-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def checkpoint(self) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".attempt-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def finish(self, *, failed_stage: str | None = None) -> None:
        self.data["status"] = "failed" if failed_stage else "completed"
        self.data["finished_at"] = datetime.now(timezone.utc).isoformat()
        if failed_stage:
            self.data["failure_stage"] = failed_stage
        self.checkpoint()



def _run_walk_forward(
    *,
    profile: str,
    windows: tuple[tuple[date, date], ...] = DEFAULT_WINDOWS,
    synthetic_days: int = 1600,
    store_root: Path | None = None,
    panel: Any = None,
    market_history: Any = None,
    returns_output: Path | None = None,
    attempt: _Attempt | None = None,
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
    if attempt is not None:
        attempt.data["stage"] = "folds"
        if shared_panel is not None and shared_market_history is not None:
            attempt.data["data"] = {"status": "normalized_frames",
                "panel_sha256": _frame_sha256(shared_panel),
                "market_history_sha256": _frame_sha256(shared_market_history),
                "hash_method": "pandas_hash_pandas_object_plus_schema_v1",
                "pandas_version": pd.__version__, "authenticity": "unverified"}
        else:
            attempt.data["data"] = {"status": "unknown", "reason": "runner_generated_or_partial_inputs"}
        attempt.checkpoint()
    with tempfile.TemporaryDirectory(prefix=f"{profile}_wf_", dir=target_root) as scratch_dir:
        scratch_orchestrator = BacktestOrchestrator(store=PerformanceStore(local_root=Path(scratch_dir)))
        walk_forward_runner = _build_runner(
            profile=profile,
            panel=shared_panel,
            market_history=shared_market_history,
            synthetic_days=synthetic_days,
        )
        scratch_orchestrator.register_runner(
            "crypto",
            walk_forward_runner,
        )
        wf_results = scratch_orchestrator.walk_forward(
            profile,
            domain="crypto",
            params=copy.deepcopy(params),
            windows=windows,
            param_set_id=f"{profile}_wf",
        )
        full_window_returns = pd.concat(walk_forward_runner.run_return_history).sort_index()
    if len(full_window_returns) < DRIFT_BASELINE_HORIZON_DAYS:
        raise ValueError("walk-forward returns do not cover the 126-day drift baseline")
    baseline_raw = _baseline_from_return_tail(wf_results[-1], full_window_returns)
    orchestrator = BacktestOrchestrator(store=PerformanceStore(local_root=target_root))
    if attempt is not None:
        attempt.data["stage"] = "baseline"
        attempt.data["baseline_status"] = "unknown"
        attempt.checkpoint()
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
    if attempt is not None:
        attempt.data["baseline_status"] = "committed"
        attempt.data["baseline_run_id"] = getattr(baseline, "run_id", None)
        attempt.checkpoint()
    if returns_output is not None:
        if attempt is not None:
            attempt.data["stage"] = "returns_output"
            attempt.data["returns_status"] = "unknown"
            attempt.checkpoint()
        if shared_market_history is None:
            raise ValueError("returns_output requires market_history")
        current_end = min(
            shared_panel.index.get_level_values("date").max(),
            shared_market_history["date"].max(),
        ).date()
        current_runner = _build_runner(
            profile=profile,
            panel=shared_panel,
            market_history=shared_market_history,
            synthetic_days=synthetic_days,
        )
        current_runner.run(
            profile,
            copy.deepcopy(params),
            start_date=min(start for start, _ in windows),
            end_date=current_end,
        )
        _write_return_matrix(
            returns_output,
            profile=profile,
            returns=current_runner.last_daily_returns,
            market_history=shared_market_history,
        )
        if attempt is not None:
            attempt.data["returns_status"] = "written"
            attempt.data["returns_sha256"] = _content_sha256(returns_output)
            attempt.checkpoint()
    return {
        "strategy_profile": profile,
        "domain": "crypto",
        "baseline": _result_payload(baseline),
        "walk_forward_folds": [_result_payload(item) for item in wf_results],
        "source": "BacktestOrchestrator.walk_forward",
    }



def run_walk_forward(*, profile: str, windows=DEFAULT_WINDOWS, synthetic_days: int = 1600,
                     store_root: Path | None = None, panel: Any = None, market_history: Any = None,
                     returns_output: Path | None = None, attempt_record_dir: Path | None = None,
                     _attempt: _Attempt | None = None) -> dict[str, Any]:
    owned = _attempt is None and attempt_record_dir is not None
    attempt = _attempt or (_Attempt(attempt_record_dir, profile=profile, windows=windows,
                                    synthetic_days=synthetic_days) if owned else None)
    try:
        result = _run_walk_forward(profile=profile, windows=windows, synthetic_days=synthetic_days,
            store_root=store_root, panel=panel, market_history=market_history,
            returns_output=returns_output, attempt=attempt)
        if attempt is not None:
            encoded = json.dumps(result, sort_keys=True, separators=(",", ":"),
                                 default=str).encode("utf-8")
            attempt.data["result_sha256"] = hashlib.sha256(encoded).hexdigest()
            attempt.data["result_digest_scope"] = "returned_summary_json_v1"
            attempt.checkpoint()
    except Exception:
        if owned and attempt is not None:
            attempt.finish(failed_stage=attempt.data["stage"])
        raise
    if owned and attempt is not None:
        attempt.finish()
    return result


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
    parser.add_argument("--attempt-record-dir", type=Path)
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({"profiles": sorted(SUPPORTED_PROFILES)}, indent=2))
        return 0

    attempt = (_Attempt(args.attempt_record_dir, profile=args.profile, windows=DEFAULT_WINDOWS,
                        synthetic_days=args.synthetic_days) if args.attempt_record_dir else None)
    stage = "input"
    try:
        panel = pd.read_csv(args.panel, compression="infer") if args.panel else None
        market_history = pd.read_csv(args.market_history, compression="infer") if args.market_history else None
        stage = "run"
        payload = run_walk_forward(profile=args.profile, synthetic_days=args.synthetic_days,
            store_root=args.store_root, panel=panel, market_history=market_history,
            returns_output=args.returns_output, _attempt=attempt)
        stage = "json_output"
        text = json.dumps(payload, indent=2, sort_keys=True, default=str)
        if args.json_output:
            if attempt is not None:
                attempt.data["json_output_status"] = "unknown"
                attempt.checkpoint()
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(text + "\n")
            if attempt is not None:
                attempt.data["json_output_status"] = "written"
                attempt.data["json_output_sha256"] = _content_sha256(args.json_output)
        print(text)
    except Exception:
        if attempt is not None:
            attempt.finish(failed_stage=(attempt.data["stage"] if stage == "run" else stage))
        raise
    if attempt is not None:
        attempt.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

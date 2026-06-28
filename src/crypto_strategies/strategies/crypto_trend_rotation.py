"""Trend rotation standalone strategy (stripped of BTC DCA).

Profile: crypto_trend_rotation
Domain: crypto
Source: feature_snapshot

This strategy reuses the core rank/weight/budget/sell logic from
crypto_live_pool_rotation but does NOT allocate any budget to BTC.
It is a pure altcoin trend-rotation signal.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from crypto_strategies.strategies.crypto_live_pool_rotation.core import (
    select_rotation_weights,
)
from crypto_strategies.strategies.crypto_live_pool_rotation.rotation import (
    resolve_authoritative_rotation_pool,
)

PROFILE_NAME = "crypto_trend_rotation"
SIGNAL_SOURCE = "feature_snapshot"

REQUIRED_FEATURE_COLUMNS = frozenset(
    {
        "symbol",
        "close",
        "sma20",
        "sma60",
        "sma200",
        "roc20",
        "roc60",
        "roc120",
        "vol20",
        "avg_quote_vol_30",
        "avg_quote_vol_90",
        "avg_quote_vol_180",
        "trend_persist_90",
        "age_days",
    }
)


def _to_indicator_frame(feature_snapshot) -> pd.DataFrame:
    """Normalise a feature snapshot into a DataFrame indexed by symbol."""
    frame = (
        feature_snapshot.copy()
        if isinstance(feature_snapshot, pd.DataFrame)
        else pd.DataFrame(list(feature_snapshot))
    )
    if frame.empty:
        raise ValueError("feature_snapshot must contain at least one row")
    missing = REQUIRED_FEATURE_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(
            f"feature_snapshot missing required columns: {', '.join(sorted(missing))}"
        )
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    for col in REQUIRED_FEATURE_COLUMNS - {"symbol"}:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["close", "sma20", "vol20"])
    return frame.set_index("symbol")


def build_target_weights(
    indicators_map: dict[str, dict[str, Any]],
    universe_snapshot: list[str],
    prices: dict[str, float],
    state: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build altcoin-only rotation weights for the trend pool.

    Steps
    -----
    1. Resolve the authoritative rotation pool from universe snapshot.
    2. Select rotation weights (top N by relative strength) from that pool.
    3. Return the altcoin weights dict -- no BTC allocation.

    Parameters
    ----------
    indicators_map : dict[str, dict]
        Symbol -> indicator-dict (close, sma*, roc*, vol*, etc.).
    universe_snapshot : list[str]
        Full candidate universe provided by the upstream platform.
    prices : dict[str, float]
        Current price per symbol.
    state : dict
        Mutable strategy state (used for pool caching and trade tracking).
    config : dict
        Runtime configuration keys:
            - trend_pool_size (int, default 5)
            - rotation_top_n (int, default 2)
            - weight_mode (str, default ``"inverse_vol"``)
            - allow_rotation_refresh (bool, default True)

    Returns
    -------
    dict[str, dict]
        Selected candidates keyed by symbol, each holding ``weight``,
        ``relative_score``, and ``abs_momentum``.  Empty dict when no
        candidates pass filters.
    """
    trend_pool_size = int(config.get("trend_pool_size", 5))
    rotation_top_n = int(config.get("rotation_top_n", 2))
    weight_mode = str(config.get("weight_mode", "inverse_vol"))
    allow_refresh = bool(config.get("allow_rotation_refresh", True))

    trend_pool = resolve_authoritative_rotation_pool(
        state,
        trend_universe_symbols=list(universe_snapshot),
        trend_pool_size=trend_pool_size,
        allow_refresh=allow_refresh,
    )

    selected_candidates = select_rotation_weights(
        indicators_map,
        prices,
        {},
        trend_pool,
        rotation_top_n,
        weight_mode=weight_mode,
    )
    return selected_candidates


def compute_signals(
    feature_snapshot,
    current_holdings,
    *,
    translator: Any | None = None,
    **kwargs: Any,
) -> tuple[dict[str, float] | None, str, bool, str, dict[str, Any]]:
    """Compute trend-rotation signals from a feature snapshot.

    Returns a 5-tuple matching the canonical feature-snapshot signal
    contract: ``(weights, signal_desc, is_emergency, debug_str, metadata)``.

    Parameters
    ----------
    feature_snapshot : pd.DataFrame or list[dict]
        Rows containing the required feature columns.
    current_holdings : list-like
        Symbols currently held (used for sell-reason checks).
    translator : callable or None
        Optional translation helper for sell-reason text.
    **kwargs
        Forwarded to the internal rotation helpers.  Supported keys:

        - trend_pool_size (int)
        - rotation_top_n (int)
        - weight_mode (str)
        - allow_rotation_refresh (bool)
        - atr_multiplier (float)

    Returns
    -------
    tuple
        ``(weights, signal_desc, is_emergency, debug_str, metadata)``
    """
    _ = translator
    del translator

    config = {
        "trend_pool_size": kwargs.get("trend_pool_size", 5),
        "rotation_top_n": kwargs.get("rotation_top_n", 2),
        "weight_mode": kwargs.get("weight_mode", "inverse_vol"),
        "allow_rotation_refresh": kwargs.get("allow_rotation_refresh", True),
    }

    frame = _to_indicator_frame(feature_snapshot)
    universe_symbols = list(frame.index)
    prices = frame["close"].to_dict()

    indicators_map: dict[str, dict[str, Any]] = {}
    for symbol in universe_symbols:
        row = frame.loc[symbol]
        indicators_map[symbol] = {col: row[col] for col in row.index}

    state: dict[str, Any] = {}
    selected = build_target_weights(
        indicators_map,
        universe_symbols,
        prices,
        state,
        config,
    )

    if not selected:
        return (
            None,
            "crypto_trend_rotation: no candidates passed filters",
            False,
            "no_candidates",
            {"managed_symbols": tuple(universe_symbols), "profile": PROFILE_NAME},
        )

    weights = {sym: float(payload["weight"]) for sym, payload in selected.items()}
    selected_symbols = ", ".join(
        f"{sym}({payload['relative_score']:.3f})"
        for sym, payload in selected.items()
    )
    signal_desc = f"crypto_trend_rotation selected: {selected_symbols}"

    metadata: dict[str, Any] = {
        "managed_symbols": tuple(universe_symbols),
        "profile": PROFILE_NAME,
        "selected_candidates": {
            sym: {
                "weight": float(payload["weight"]),
                "relative_score": float(payload["relative_score"]),
                "abs_momentum": float(payload["abs_momentum"]),
            }
            for sym, payload in selected.items()
        },
    }

    return weights, signal_desc, False, "ok", metadata


def extract_managed_symbols(
    feature_snapshot,
    *,
    benchmark_symbol: str | None = None,
    **kwargs: Any,
) -> tuple[str, ...]:
    """Extract the set of symbols managed by this strategy.

    Parameters
    ----------
    feature_snapshot : pd.DataFrame or list[dict]
        Feature rows with at least a ``symbol`` column.
    benchmark_symbol : str or None
        Ignored in the altcoin-only context (kept for interface
        compatibility).

    Returns
    -------
    tuple[str, ...]
        Sorted, deduplicated symbol list.
    """
    _ = benchmark_symbol, kwargs
    del benchmark_symbol

    frame = (
        feature_snapshot.copy()
        if isinstance(feature_snapshot, pd.DataFrame)
        else pd.DataFrame(list(feature_snapshot))
    )
    if frame.empty:
        return ()

    symbols = sorted(
        set(
            str(s).strip().upper()
            for s in frame["symbol"].dropna().unique()
            if s
        )
    )
    return tuple(symbols)

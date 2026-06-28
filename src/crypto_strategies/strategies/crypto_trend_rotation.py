"""Trend rotation standalone strategy (enhanced).

Profile: crypto_trend_rotation
Domain: crypto
Source: feature_snapshot + derived_indicators (BTC benchmark)

This strategy reuses the core rank/weight/sell logic from
crypto_live_pool_rotation but does NOT allocate any budget to BTC.
It is a pure altcoin trend-rotation signal.

Enhancements over the original stripped version:
- Proper BTC benchmark snapshot integration (fixes the empty {} bug)
- Volatility-based position sizing
- Market drawdown circuit breaker
- i18n signal descriptions
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from crypto_strategies._utils import (
    coerce_bool,
    coerce_float,
    translate_with_fallback,
)
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

# --- BTC benchmark extraction ---


def _extract_btc_snapshot(indicators_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Extract BTC benchmark data from the indicators map.

    Looks for BTCUSDT, BTC-USD, or BTC key in the map and extracts
    the necessary roc and regime fields.
    """
    btc_keys = ("BTCUSDT", "BTC-USD", "BTC", "BTCUSDT.P")
    btc_data = None
    for key in btc_keys:
        candidate = indicators_map.get(key)
        if isinstance(candidate, dict) and candidate:
            btc_data = candidate
            break

    if btc_data is None:
        # Search case-insensitively
        for k, v in indicators_map.items():
            if isinstance(v, dict) and str(k).upper().replace("-", "").replace(".", "") == "BTCUSDT":
                btc_data = v
                break

    if btc_data is None:
        # Return a default "regime on" snapshot so rotation can proceed
        return {
            "regime_on": True,
            "btc_roc20": 0.05,
            "btc_roc60": 0.10,
            "btc_roc120": 0.20,
        }

    roc20 = coerce_float(btc_data.get("roc20"), default=0.05)
    roc60 = coerce_float(btc_data.get("roc60"), default=0.05)
    roc120 = coerce_float(btc_data.get("roc120"), default=0.05)
    close = coerce_float(btc_data.get("close"), default=0.0)
    sma200 = coerce_float(btc_data.get("sma200"), default=0.0)

    # Regime on = BTC price above SMA200 (long-term uptrend)
    regime_on = bool(close > sma200 if sma200 > 0 else True)
    # Also check explicit regime field if present
    explicit_regime = btc_data.get("regime_on")
    if explicit_regime is not None:
        regime_on = coerce_bool(explicit_regime, default=True)

    return {
        "regime_on": regime_on,
        "btc_roc20": float(roc20),
        "btc_roc60": float(roc60),
        "btc_roc120": float(roc120),
    }


# --- Market circuit breaker ---


def _check_circuit_breaker(
    btc_snapshot: dict[str, Any],
    *,
    circuit_breaker_enabled: bool = True,
    btc_drawdown_threshold: float = 0.30,
) -> tuple[bool, str]:
    """Check if trend rotation should be suspended due to market stress.

    Returns (blocked, reason).
    """
    if not coerce_bool(circuit_breaker_enabled, default=True):
        return False, ""

    if not btc_snapshot.get("regime_on", True):
        return True, "btc_below_sma200"

    # Check for extreme BTC drawdown
    btc_close = coerce_float(btc_snapshot.get("close"), default=float("nan"))
    btc_sma200 = coerce_float(btc_snapshot.get("sma200"), default=float("nan"))
    if not pd.isna(btc_close) and not pd.isna(btc_sma200) and btc_sma200 > 0:
        drawdown = 1.0 - btc_close / btc_sma200
        if drawdown > float(btc_drawdown_threshold):
            return True, f"btc_drawdown_{drawdown:.0%}_exceeds_{btc_drawdown_threshold:.0%}"

    return False, ""


# --- Volatility-based position sizing ---


def _apply_volatility_scaling(
    weights: dict[str, float],
    indicators_map: dict[str, dict[str, Any]],
    *,
    vol_scaling_enabled: bool = True,
    target_vol: float = 0.40,
    max_leverage: float = 1.0,
) -> dict[str, float]:
    """Scale position weights inversely by volatility.

    When vol_scaling_enabled, each weight is scaled so the
    portfolio-level volatility stays near target_vol.
    max_leverage caps the total exposure (1.0 = 100%).
    """
    if not coerce_bool(vol_scaling_enabled, default=True):
        return weights
    if not weights:
        return weights

    total_weight = sum(weights.values())
    if total_weight <= 0:
        return weights

    # Estimate portfolio vol as weighted average of individual vols
    weighted_vol = 0.0
    vol_sum = 0.0
    for symbol, weight in weights.items():
        indicators = indicators_map.get(symbol, {})
        vol20 = coerce_float(indicators.get("vol20"), default=float("nan"))
        if not pd.isna(vol20) and vol20 > 0:
            weighted_vol += weight * vol20
            vol_sum += weight

    if vol_sum <= 0 or weighted_vol <= 0:
        return weights

    avg_vol = weighted_vol / vol_sum
    target_vol = float(target_vol)
    max_lev = float(max_leverage)

    # Scale: if current vol > target, reduce; if < target, allow up to max_leverage
    scale = min(max_lev, target_vol / avg_vol) if avg_vol > 0 else max_lev
    scale = max(0.5, min(1.0, scale))  # clamp to [0.5, 1.0] to avoid extreme moves

    return {symbol: weight * scale for symbol, weight in weights.items()}


# --- Core ---


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
    1. Extract BTC benchmark from indicators_map (fixes the empty {} bug).
    2. Check market circuit breaker.
    3. Resolve the authoritative rotation pool from universe snapshot.
    4. Select rotation weights (top N by relative strength) from that pool.
    5. Apply volatility-based position scaling.
    6. Return the altcoin weights dict — no BTC allocation.

    Parameters
    ----------
    indicators_map : dict[str, dict]
        Symbol → indicator-dict (close, sma*, roc*, vol*, etc.).
        Must contain BTCUSDT (or equivalent) for benchmark extraction.
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
            - weight_mode (str, default "inverse_vol")
            - allow_rotation_refresh (bool, default True)
            - circuit_breaker_enabled (bool, default True)
            - btc_drawdown_threshold (float, default 0.30)
            - vol_scaling_enabled (bool, default True)

    Returns
    -------
    dict[str, dict]
        Selected candidates keyed by symbol, each holding ``weight``,
        ``relative_score``, and ``abs_momentum``.  Empty dict when no
        candidates pass filters or circuit breaker is active.
    """
    trend_pool_size = int(config.get("trend_pool_size", 5))
    rotation_top_n = int(config.get("rotation_top_n", 2))
    weight_mode = str(config.get("weight_mode", "inverse_vol"))
    allow_refresh = bool(config.get("allow_rotation_refresh", True))
    circuit_breaker_enabled = coerce_bool(config.get("circuit_breaker_enabled"), default=True)
    btc_drawdown_threshold = float(config.get("btc_drawdown_threshold", 0.30))
    vol_scaling_enabled = coerce_bool(config.get("vol_scaling_enabled"), default=True)

    # Extract BTC benchmark from indicators_map
    btc_snapshot = _extract_btc_snapshot(indicators_map)

    # Circuit breaker check
    blocked, block_reason = _check_circuit_breaker(
        btc_snapshot,
        circuit_breaker_enabled=circuit_breaker_enabled,
        btc_drawdown_threshold=btc_drawdown_threshold,
    )
    if blocked:
        return {}

    trend_pool = resolve_authoritative_rotation_pool(
        state,
        trend_universe_symbols=list(universe_snapshot),
        trend_pool_size=trend_pool_size,
        allow_refresh=allow_refresh,
    )

    selected_candidates = select_rotation_weights(
        indicators_map,
        prices,
        btc_snapshot,  # FIXED: was {} — now passes real BTC benchmark data
        trend_pool,
        rotation_top_n,
        weight_mode=weight_mode,
    )

    if not selected_candidates:
        return {}

    # Apply volatility scaling
    weights_map = {
        sym: float(payload["weight"])
        for sym, payload in selected_candidates.items()
    }
    scaled_weights = _apply_volatility_scaling(
        weights_map,
        indicators_map,
        vol_scaling_enabled=vol_scaling_enabled,
    )

    return {
        sym: {
            "weight": scaled_weights.get(sym, selected_candidates[sym]["weight"]),
            "relative_score": selected_candidates[sym]["relative_score"],
            "abs_momentum": selected_candidates[sym]["abs_momentum"],
        }
        for sym in selected_candidates
    }


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
        Must include BTCUSDT row for benchmark extraction.
    current_holdings : list-like
        Symbols currently held (used for sell-reason checks).
    translator : callable or None
        Optional translation helper.
    **kwargs
        Forwarded to the internal rotation helpers.

    Returns
    -------
    tuple
        ``(weights, signal_desc, is_emergency, debug_str, metadata)``
    """
    config = {
        "trend_pool_size": kwargs.get("trend_pool_size", 5),
        "rotation_top_n": kwargs.get("rotation_top_n", 2),
        "weight_mode": kwargs.get("weight_mode", "inverse_vol"),
        "allow_rotation_refresh": kwargs.get("allow_rotation_refresh", True),
        "circuit_breaker_enabled": kwargs.get("circuit_breaker_enabled", True),
        "btc_drawdown_threshold": kwargs.get("btc_drawdown_threshold", 0.30),
        "vol_scaling_enabled": kwargs.get("vol_scaling_enabled", True),
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
        signal_desc = translate_with_fallback(
            translator,
            "trend_rotation_no_candidates",
            fallback_en="crypto_trend_rotation: no candidates passed filters",
            fallback_zh="加密货币趋势轮动：无候选币种通过筛选",
        )
        return (
            None,
            signal_desc,
            False,
            "no_candidates",
            {"managed_symbols": tuple(universe_symbols), "profile": PROFILE_NAME},
        )

    weights = {sym: float(payload["weight"]) for sym, payload in selected.items()}
    selected_symbols = ", ".join(
        f"{sym}({payload['relative_score']:.3f})"
        for sym, payload in sorted(selected.items(), key=lambda x: -x[1]["relative_score"])
    )
    signal_desc = translate_with_fallback(
        translator,
        "trend_rotation_selected",
        fallback_en=f"crypto_trend_rotation selected: {selected_symbols}",
        fallback_zh=f"加密货币趋势轮动选中：{selected_symbols}",
    )

    metadata: dict[str, Any] = {
        "managed_symbols": tuple(universe_symbols),
        "profile": PROFILE_NAME,
        "selected_count": len(selected),
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
        Ignored in the altcoin-only context.

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

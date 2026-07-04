"""Crypto equity combo strategy — BTC DCA + Trend Rotation.

Combines two enhanced independent strategies into a single weight-allocated
portfolio. BTC leg delegates to ``crypto_btc_dca.compute_signals`` so that
smart sizing (AHR999, drawdown multipliers, Z-score exit) is active when
configured.

Static mode: fixed weights per leg (default: 30/70 BTC/trend).
Dynamic mode: regime-based adjustment — when BTC is below SMA200, reduce
trend leg by 50 % and re-allocate to BTC.

Usage
-----
from crypto_strategies.strategies.crypto_equity_combo import compute_signals
"""

from __future__ import annotations

import logging
from typing import Any

from crypto_strategies._utils import coerce_float, translate_with_fallback
from crypto_strategies.strategies.crypto_trend_rotation import (
    _apply_volatility_scaling,
    _check_circuit_breaker,
    _extract_btc_snapshot,
)

logger = logging.getLogger(__name__)

CRYPTO_EQUITY_DOMAIN = "crypto_equity"
SIGNAL_SOURCE = "combo"
STATUS_ICON = "\U0001f500"
PROFILE_NAME = "crypto_equity_combo"

DEFAULT_BTC_WEIGHT = 0.30
DEFAULT_TREND_WEIGHT = 0.70
DYNAMIC_REGIME_OFF_CUT = 0.50

TREND_ONLY_KWARGS = frozenset({
    "trend_pool_size",
    "rotation_top_n",
    "weight_mode",
    "allow_rotation_refresh",
    "circuit_breaker_enabled",
    "btc_drawdown_threshold",
    "vol_scaling_enabled",
    "target_vol",
    "max_leverage",
})


def _clamp_ratio(value: float, *, default: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    return min(1.0, max(0.0, numeric))


def _compute_btc_leg(
    total_equity: float,
    btc_weight: float,
    *,
    prices: dict[str, float] | None = None,
    portfolio: Any = None,
    derived_indicators: dict[str, Any] | None = None,
    translator=None,
    **kwargs: Any,
) -> tuple[dict[str, float], dict[str, object]]:
    """Compute BTC leg target using the enhanced smart DCA strategy.

    Delegates to ``crypto_btc_dca.compute_signals`` to incorporate
    AHR999 cycle multiplier, drawdown sizing, and Z-score exit signals.
    Falls back to equity-scaled ratio on error.
    """
    from crypto_strategies.strategies.crypto_btc_dca import (
        compute_signals,
        get_dynamic_btc_target_ratio,
    )

    base_ratio = get_dynamic_btc_target_ratio(total_equity)
    smart_ratio = base_ratio
    dca_metadata: dict[str, Any] = {}
    zscore_target_exposure = 1.0
    try:
        btc_kwargs = {k: v for k, v in kwargs.items() if k not in TREND_ONLY_KWARGS}
        result = compute_signals(
            prices=prices or {},
            portfolio=portfolio,
            total_equity=total_equity,
            derived_indicators=derived_indicators,
            translator=translator,
            **btc_kwargs,
        )
        dca_metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
        regime = str(dca_metadata.get("regime", ""))
        # Use accumulation multipliers to scale the DCA leg, but do not let
        # valuation-skip regimes force a full target-weight sell in combo mode.
        if regime and regime not in ("ordinary_dca", "ahr999_expensive"):
            multiplier = float(dca_metadata.get("multiplier", 1.0))
            smart_ratio = min(base_ratio * max(1.0, multiplier), base_ratio * 3.0)
            smart_ratio = min(0.65, max(0.0, smart_ratio))

        zscore_exit = dca_metadata.get("zscore_exit")
        if isinstance(zscore_exit, dict) and zscore_exit.get("applied"):
            zscore_target_exposure = _clamp_ratio(
                zscore_exit.get("target_btc_exposure"),
                default=1.0,
            )
            smart_ratio *= zscore_target_exposure
    except (ValueError, TypeError) as exc:
        logger.debug("btc_dca smart signals unavailable (non-critical): %s", exc)

    target_weight = smart_ratio * float(btc_weight)
    return {"BTCUSDT": target_weight}, {
        "base_ratio": base_ratio,
        "smart_ratio": smart_ratio,
        "zscore_target_exposure": zscore_target_exposure,
        "dca_metadata": dca_metadata,
    }


def _compute_trend_leg(
    indicators_map: dict[str, dict[str, Any]],
    prices: dict[str, float],
    universe_snapshot: list[str],
    state: dict[str, Any],
    trend_weight: float,
    trend_pool_size: int = 5,
    rotation_top_n: int = 2,
    weight_mode: str = "inverse_vol",
    vol_scaling_enabled: bool = True,
    allow_rotation_refresh: bool = True,
    circuit_breaker_enabled: bool = True,
    btc_drawdown_threshold: float = 0.30,
    target_vol: float = 0.40,
    max_leverage: float = 1.0,
) -> tuple[dict[str, float], dict[str, object]]:
    """Compute trend leg targets using rotation logic."""
    from crypto_strategies.strategies.crypto_live_pool_rotation.core import (
        select_rotation_weights,
    )
    from crypto_strategies.strategies.crypto_live_pool_rotation.rotation import (
        resolve_authoritative_rotation_pool,
    )

    btc_snapshot = _extract_btc_snapshot(indicators_map)
    blocked, _ = _check_circuit_breaker(
        btc_snapshot,
        circuit_breaker_enabled=circuit_breaker_enabled,
        btc_drawdown_threshold=btc_drawdown_threshold,
    )
    if blocked:
        return {}, {"trend_pool": (), "rotation_candidates": {}, "circuit_blocked": True}

    trend_pool = resolve_authoritative_rotation_pool(
        state,
        trend_universe_symbols=list(universe_snapshot),
        trend_pool_size=trend_pool_size,
        allow_refresh=allow_rotation_refresh,
    )

    candidates = select_rotation_weights(
        indicators_map, prices, btc_snapshot, trend_pool,
        rotation_top_n, weight_mode=weight_mode,
    )
    trend_metadata: dict[str, object] = {
        "trend_pool": tuple(trend_pool),
        "rotation_candidates": {
            symbol: {
                "weight": float(payload.get("weight", 0.0)),
                "relative_score": float(payload.get("relative_score", 0.0)),
                "abs_momentum": float(payload.get("abs_momentum", 0.0)),
            }
            for symbol, payload in candidates.items()
        },
        "ranking_preview": tuple(trend_pool[: int(trend_pool_size)]),
        "rotation_pool_source_version": state.get("rotation_pool_source_version"),
        "rotation_pool_source_as_of_date": state.get("rotation_pool_source_as_of_date"),
        "rotation_pool_last_month": state.get("rotation_pool_last_month"),
        "circuit_blocked": False,
    }
    if not candidates:
        return {}, trend_metadata

    raw_weights = {
        sym: float(payload["weight"]) * float(trend_weight)
        for sym, payload in candidates.items()
    }
    weights = _apply_volatility_scaling(
        raw_weights, indicators_map,
        vol_scaling_enabled=vol_scaling_enabled,
        target_vol=target_vol,
        max_leverage=max_leverage,
    )
    return weights, trend_metadata


def build_target_weights(
    prices: dict[str, float] | None = None,
    indicators_map: dict[str, Any] | None = None,
    universe_snapshot: dict[str, Any] | None = None,
    benchmark_snapshot: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    *,
    btc_weight: float = DEFAULT_BTC_WEIGHT,
    trend_weight: float = DEFAULT_TREND_WEIGHT,
    dynamic_mode: bool = True,
    translator=None,
    **kwargs: Any,
) -> tuple[dict[str, float], dict[str, object]]:
    """Compute combined target weights from both sub-strategies."""
    if prices is None or indicators_map is None:
        return {}, {"error": "missing required inputs"}

    prices = {str(k).strip().upper(): float(v or 0) for k, v in prices.items() if v is not None}

    if isinstance(universe_snapshot, dict):
        universe_symbols = list(universe_snapshot.keys())
    elif isinstance(universe_snapshot, (list, tuple)):
        universe_symbols = list(universe_snapshot)
    else:
        universe_symbols = list(prices)

    total_equity = 100000.0
    if isinstance(portfolio, dict):
        total_equity = coerce_float(portfolio.get("total_equity"), default=100000.0)
    elif portfolio is not None:
        total_equity = coerce_float(getattr(portfolio, "total_equity", None), default=100000.0)

    state = state or {}

    # Dynamic regime adjustment
    regime_off = False
    if benchmark_snapshot:
        regime_on = benchmark_snapshot.get("regime_on")
        if regime_on is not None:
            regime_off = not bool(regime_on)
    else:
        btc_snapshot = _extract_btc_snapshot(indicators_map or {})
        regime_off = not btc_snapshot.get("regime_on", True)

    if dynamic_mode and regime_off:
        effective_btc = btc_weight + trend_weight * DYNAMIC_REGIME_OFF_CUT
        effective_trend = trend_weight * (1.0 - DYNAMIC_REGIME_OFF_CUT)
    else:
        effective_btc = btc_weight
        effective_trend = trend_weight

    # Compute legs
    btc_weights, btc_leg_metadata = _compute_btc_leg(
        total_equity, effective_btc,
        prices=prices, portfolio=portfolio,
        derived_indicators=indicators_map,
        translator=translator,
        **kwargs,
    )

    trend_weights: dict[str, float] = {}
    try:
        trend_weights, trend_metadata = _compute_trend_leg(
            indicators_map, prices, universe_symbols, state, effective_trend,
            trend_pool_size=int(kwargs.get("trend_pool_size", 5)),
            rotation_top_n=int(kwargs.get("rotation_top_n", 2)),
            weight_mode=str(kwargs.get("weight_mode", "inverse_vol")),
            vol_scaling_enabled=bool(kwargs.get("vol_scaling_enabled", True)),
            allow_rotation_refresh=bool(kwargs.get("allow_rotation_refresh", True)),
            circuit_breaker_enabled=bool(kwargs.get("circuit_breaker_enabled", True)),
            btc_drawdown_threshold=float(kwargs.get("btc_drawdown_threshold", 0.30)),
            target_vol=float(kwargs.get("target_vol", 0.40)),
            max_leverage=float(kwargs.get("max_leverage", 1.0)),
        )
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("trend_leg failed, using empty weights: %s", exc)
        trend_metadata = {"trend_pool": (), "rotation_candidates": {}, "error": str(exc)}

    # Combine
    all_symbols = set(btc_weights) | set(trend_weights)
    combined: dict[str, float] = {}
    for symbol in all_symbols:
        combined[symbol] = btc_weights.get(symbol, 0.0) + trend_weights.get(symbol, 0.0)

    total = sum(combined.values())
    if total > 1.0:
        combined = {s: w / total for s, w in combined.items()}

    metadata: dict[str, object] = {
        "combo": {
            "btc_weight": effective_btc,
            "trend_weight": effective_trend,
            "base_btc_weight": btc_weight,
            "base_trend_weight": trend_weight,
        },
        "btc_leg": {"weights": btc_weights, **btc_leg_metadata},
        "trend_leg": {"weights": trend_weights, **trend_metadata},
        "regime_off": regime_off,
        "dynamic_mode": dynamic_mode,
        "gross_exposure": sum(combined.values()),
        "selected_count": len(combined),
        "total_equity": total_equity,
    }
    return combined, metadata


def extract_managed_symbols(*args: Any, **kwargs: Any) -> tuple[str, ...]:
    return ("BTCUSDT",)


def compute_signals(
    prices: dict[str, float] | None = None,
    indicators_map: dict[str, Any] | None = None,
    universe_snapshot: dict[str, Any] | None = None,
    benchmark_snapshot: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    translator=None,
    **kwargs: Any,
):
    """Compute combo signals with dynamic regime-based allocation.

    Returns (weights, signal_desc, has_cash_residual, status_desc, metadata).
    """
    weights, metadata = build_target_weights(
        prices=prices,
        indicators_map=indicators_map,
        universe_snapshot=universe_snapshot,
        benchmark_snapshot=benchmark_snapshot,
        portfolio=portfolio,
        state=state,
        translator=translator,
        **kwargs,
    )

    combo_meta = metadata.get("combo", {})
    if isinstance(combo_meta, dict):
        btw = combo_meta.get("btc_weight", 0)
        trw = combo_meta.get("trend_weight", 0)
    else:
        btw, trw = 0, 0

    regime_off = metadata.get("regime_off", False)
    regime_label = "regime_off" if regime_off else "regime_on"
    gross = float(metadata.get("gross_exposure", 0.0))

    if weights:
        selected = ",".join(
            f"{s}({w:.1%})" for s, w in sorted(weights.items(), key=lambda x: -x[1])[:6]
        )
    else:
        selected = "cash"

    signal_desc = translate_with_fallback(
        translator,
        "combo_signal",
        fallback_en=(
            f"combo {regime_label} selected={selected} "
            f"gross={gross:.0%} btc={btw:.0%} trend={trw:.0%}"
        ),
        fallback_zh=(
            f"组合策略 {regime_label} 选中={selected} "
            f"总仓位={gross:.0%} BTC={btw:.0%} 趋势={trw:.0%}"
        ),
    )
    status_desc = translate_with_fallback(
        translator,
        "combo_status",
        fallback_en=f"{regime_label} | btc={btw:.0%} trend={trw:.0%} | {len(weights)} positions",
        fallback_zh=f"{regime_label} | BTC={btw:.0%} 趋势={trw:.0%} | {len(weights)} 个仓位",
    )

    has_cash_residual = gross < 0.999

    return (
        weights,
        signal_desc,
        has_cash_residual,
        status_desc,
        {
            **metadata,
            "managed_symbols": extract_managed_symbols(),
            "status_icon": STATUS_ICON,
            "signal_source": SIGNAL_SOURCE,
            "actionable": True,
        },
    )

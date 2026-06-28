"""Crypto equity combo strategy — BTC DCA + Trend Rotation.

Combines two crypto sub-strategies (BTC DCA and Trend Rotation) into a single
weight-allocated portfolio.

Static mode
-----------
Fixed weights per leg (default: 30/70 BTC/trend).

Dynamic mode
------------
Regime-based adjustment: when the benchmark snapshot signals regime_off,
reduce the trend leg weight by 50 % and re-allocate the freed budget to BTC.

Usage
-----
from crypto_strategies.strategies.crypto_equity_combo import compute_signals
"""

from __future__ import annotations

from typing import Any


from crypto_strategies.strategies import crypto_btc_dca
from crypto_strategies.strategies import crypto_trend_rotation

CRYPTO_EQUITY_DOMAIN = "crypto_equity"
SIGNAL_SOURCE = "combo"
STATUS_ICON = "\U0001f500"
PROFILE_NAME = "crypto_equity_combo"

# Default static weights
DEFAULT_BTC_WEIGHT = 0.30
DEFAULT_TREND_WEIGHT = 0.70

# Dynamic mode thresholds
DYNAMIC_REGIME_OFF_CUT = 0.50  # reduce trend by 50 % when regime_off

# BTC leg defaults
BTC_DEFAULT_CONFIG: dict[str, Any] = {}

# Trend rotation leg defaults
TREND_DEFAULT_CONFIG: dict[str, Any] = {}


def _clean_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Remove combo-level keys before passing to sub-strategies."""
    ignored = {
        "btc_weight",
        "trend_weight",
        "dynamic_mode",
        "translator",
        "signal_text_fn",
        "execution_cash_reserve_ratio",
        "rebalance_frequency",
        "run_as_of",
    }
    return {k: v for k, v in kwargs.items() if k not in ignored}


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
    btc_config: dict[str, Any] | None = None,
    trend_config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[dict[str, float], dict[str, object]]:
    """Compute combined target weights from both sub-strategies.

    Parameters
    ----------
    prices : dict or None
        Current price map (symbol -> float).
    indicators_map : dict or None
        Derived indicator map (symbol -> dict).
    universe_snapshot : dict or None
        Trend universe snapshot.
    benchmark_snapshot : dict or None
        Benchmark snapshot used for regime detection.
    portfolio : dict or None
        Current portfolio snapshot.
    state : dict or None
        Strategy state (may be mutated by sub-strategies).
    btc_weight, trend_weight : float
        Allocation weights for each leg.  Should sum to 1.0.
    dynamic_mode : bool
        If True, reduce trend allocation when the benchmark snapshot
        signals ``regime_on == False``.
    btc_config, trend_config : dict or None
        Overrides passed to each sub-strategy's ``build_target_weights``.
    **kwargs : Any
        Ignored (compatibility with runtime entrypoint).
    """
    resolved_btc = dict(BTC_DEFAULT_CONFIG)
    resolved_btc.update(btc_config or {})

    resolved_trend = dict(TREND_DEFAULT_CONFIG)
    resolved_trend.update(trend_config or {})

    # Compute each leg
    btc_weights: dict[str, float] = {}
    trend_raw_weights: dict[str, float] = {}
    trend_metadata: dict[str, object] = {}

    if prices is not None and indicators_map is not None and benchmark_snapshot is not None:
        try:
            btc_weights, _ = crypto_btc_dca.build_target_weights(
                prices=prices,
                indicators_map=indicators_map,
                benchmark_snapshot=benchmark_snapshot,
                portfolio=portfolio,
                state=state,
                **resolved_btc,
            )
        except Exception:
            btc_weights = {}

        try:
            trend_raw_weights, _, trend_metadata = crypto_trend_rotation.build_target_weights(
                prices=prices,
                indicators_map=indicators_map,
                universe_snapshot=universe_snapshot,
                benchmark_snapshot=benchmark_snapshot,
                portfolio=portfolio,
                state=state,
                **resolved_trend,
            )
        except Exception:
            trend_raw_weights = {}

    # Determine effective weights (dynamic adjustment)
    regime_off = bool(benchmark_snapshot.get("regime_on", True)) is False if benchmark_snapshot else False
    if dynamic_mode and regime_off:
        effective_btc = btc_weight + trend_weight * DYNAMIC_REGIME_OFF_CUT
        effective_trend = trend_weight * (1.0 - DYNAMIC_REGIME_OFF_CUT)
    else:
        effective_btc = btc_weight
        effective_trend = trend_weight

    # Combine weights
    all_symbols = set(btc_weights) | set(trend_raw_weights)
    combined: dict[str, float] = {}
    for symbol in all_symbols:
        bw = btc_weights.get(symbol, 0.0)
        tw = trend_raw_weights.get(symbol, 0.0)
        combined[symbol] = bw * effective_btc + tw * effective_trend

    # Normalize to ensure sum <= 1.0
    total = sum(combined.values())
    if total > 0.0:
        scale = min(1.0, 1.0 / total) if total > 1.0 else 1.0
        if scale < 1.0:
            combined = {s: w * scale for s, w in combined.items()}

    metadata: dict[str, object] = {
        "combo": {
            "btc_weight": effective_btc,
            "trend_weight": effective_trend,
        },
        "legs": {
            "btc": {"weights": btc_weights, "configured_weight": btc_weight},
            "trend": {
                "weights": trend_raw_weights,
                "configured_weight": trend_weight,
            },
        },
        "regime_off": regime_off,
        "dynamic_mode": dynamic_mode,
        "gross_exposure": sum(combined.values()),
        "selected_count": len(combined),
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
    **kwargs: Any,
):
    kwargs.pop("translator", None)
    kwargs.pop("signal_text_fn", None)
    kwargs.pop("execution_cash_reserve_ratio", None)

    weights, metadata = build_target_weights(
        prices=prices,
        indicators_map=indicators_map,
        universe_snapshot=universe_snapshot,
        benchmark_snapshot=benchmark_snapshot,
        portfolio=portfolio,
        state=state,
        **kwargs,
    )

    combo_meta = metadata.get("combo", {})
    regime_off = metadata.get("regime_off", False)
    regime_label = "regime_off" if regime_off else "regime_on"
    selected = ",".join(weights.keys()) if weights else "cash"
    signal_desc = (
        f"combo {regime_label} selected={selected} "
        f"gross={metadata['gross_exposure']:.0%} "
        f"btc={combo_meta.get('btc_weight', 0):.0%} "
        f"trend={combo_meta.get('trend_weight', 0):.0%}"
    )
    status_desc = (
        f"{regime_label} | "
        f"btc={combo_meta.get('btc_weight', 0):.0%} "
        f"trend={combo_meta.get('trend_weight', 0):.0%}"
    )
    has_cash_residual = metadata["gross_exposure"] < 0.999

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

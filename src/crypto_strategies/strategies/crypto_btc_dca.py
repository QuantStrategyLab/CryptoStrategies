"""BTC DCA standalone strategy with smart sizing and cycle-aware exit.

Profile: crypto_btc_dca
Domain: crypto
Source: derived_indicators + portfolio_snapshot

This is a smart DCA strategy targeting BTCUSDT. It supports:

- **Smart multiplier**: AHR999 cycle indicator (priority) or drawdown-based sizing.
  When enabled, the base DCA amount is scaled by a regime-dependent multiplier
  (0.0x → skip, 1.0x → normal, up to 3.0x → aggressive).
- **Z-score exit (逃顶)**: MVRV Z-Score driven position control that reduces BTC
  exposure to 50 % (risk_reduced) or 25 % (risk_off), parking freed capital in
  USDT.
- **Execution window**: Monthly (day 25 ± 5 days), weekly (Thursday ± 4 days),
  or quarterly with configurable windows.
- **i18n**: Chinese / English bilingual diagnostics.

Strategy Contract
-----------------
``compute_signals`` returns a dict compatible with the combo strategy:

    {
        "signals": {"BTCUSDT": {"target_weight": 1.0, "btc_target_ratio": ...}},
        "total_equity": ...,
        "btc_target_ratio": ...,
        "profile": "crypto_btc_dca",
        "metadata": { ... full diagnostics ... },
    }

``build_target_weights`` returns {"BTCUSDT": 1.0} — the ratio is applied
externally by the entrypoint / combo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
import logging
import math
from typing import Any

import pandas as pd

from crypto_strategies._utils import (
    as_clamped_ratio,
    coerce_bool,
    coerce_float,
    normalize_symbol,
    payload_numeric,
    translate_with_fallback,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRYPTO_DOMAIN: str = "crypto"
SIGNAL_SOURCE: str = "derived_indicators+portfolio_snapshot"
STATUS_ICON: str = "₿"
PROFILE_NAME: str = "crypto_btc_dca"

DEFAULT_SIGNAL_SYMBOL = "BTCUSDT"
DEFAULT_PARKING_SYMBOL = "USDT"
BITCOIN_GENESIS_DATE = pd.Timestamp("2009-01-03")

ZSCORE_EXIT_PROFILE = "btc_zscore_exit"
ZSCORE_EXIT_POSITION_ROUTES = frozenset({"normal", "risk_on", "risk_reduced", "risk_off"})


# ---------------------------------------------------------------------------
# Immutable Plan Configuration Value Objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionWindowSpec:
    """Execution cadence and calendar window parameters."""

    cadence: str = "monthly"
    monthly_day: int = 25
    monthly_window_calendar_days: int = 5
    weekly_day: int = 4
    weekly_window_calendar_days: int = 4
    quarterly_months: object = (1, 4, 7, 10)
    quarterly_day: int = 25
    quarterly_window_calendar_days: int = 5


@dataclass(frozen=True, slots=True)
class DrawdownTierSpec:
    """Drawdown, SMA200 gap, and RSI thresholds with pullback multipliers."""

    mild_drawdown_threshold: float = 0.12
    deep_drawdown_threshold: float = 0.25
    severe_drawdown_threshold: float = 0.40
    mild_discount_gap: float = 0.08
    deep_discount_gap: float = 0.18
    expensive_gap: float = 0.30
    very_expensive_gap: float = 0.60
    shallow_drawdown_threshold: float = 0.05
    overbought_rsi: float = 75.0
    mild_pullback_multiplier: float = 1.50
    deep_pullback_multiplier: float = 2.25
    severe_pullback_multiplier: float = 3.0
    expensive_multiplier: float = 1.0
    very_expensive_multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class Ahr999Spec:
    """AHR999 cycle valuation thresholds and multipliers."""

    bottom_threshold: float = 0.45
    accumulation_threshold: float = 0.80
    dca_threshold: float = 1.20
    bottom_multiplier: float = 3.0
    accumulation_multiplier: float = 2.25
    dca_multiplier: float = 1.50
    expensive_multiplier: float = 0.0


@dataclass(frozen=True, slots=True)
class ZScoreExitSpec:
    """MVRV Z-Score exit overlay parameters."""

    enabled: bool = True
    parking_symbol: str = DEFAULT_PARKING_SYMBOL
    risk_reduced_exposure: float = 0.50
    risk_off_exposure: float = 0.25
    allow_outside_execution_window: bool = True


_TOP_LEVEL_PLAN_FIELDS: frozenset[str] = frozenset({
    "signal_symbol",
    "base_investment_usd",
    "max_investment_usd",
    "cash_reserve_usd",
    "min_investment_usd",
    "smart_multiplier_enabled",
    "cycle_indicator_enabled",
    "base_multiplier",
})

_EXECUTION_WINDOW_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(ExecutionWindowSpec)
)

_DRAWDOWN_TIER_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(DrawdownTierSpec)
)

_AHR999_KWARG_TO_FIELD: dict[str, str] = {
    "ahr999_bottom_threshold": "bottom_threshold",
    "ahr999_accumulation_threshold": "accumulation_threshold",
    "ahr999_dca_threshold": "dca_threshold",
    "ahr999_bottom_multiplier": "bottom_multiplier",
    "ahr999_accumulation_multiplier": "accumulation_multiplier",
    "ahr999_dca_multiplier": "dca_multiplier",
    "ahr999_expensive_multiplier": "expensive_multiplier",
}

_ZSCORE_EXIT_KWARG_TO_FIELD: dict[str, str] = {
    "zscore_exit_enabled": "enabled",
    "zscore_exit_parking_symbol": "parking_symbol",
    "zscore_exit_risk_reduced_exposure": "risk_reduced_exposure",
    "zscore_exit_risk_off_exposure": "risk_off_exposure",
    "zscore_exit_allow_outside_execution_window": "allow_outside_execution_window",
}

_SUPPORTED_PLAN_KWARGS: frozenset[str] = (
    _TOP_LEVEL_PLAN_FIELDS
    | _EXECUTION_WINDOW_FIELDS
    | _DRAWDOWN_TIER_FIELDS
    | frozenset(_AHR999_KWARG_TO_FIELD)
    | frozenset(_ZSCORE_EXIT_KWARG_TO_FIELD)
)


def _build_spec_from_mapped_kwargs(
    spec_cls: type[Any],
    kwarg_to_field: Mapping[str, str],
    kwargs: Mapping[str, Any],
) -> Any:
    unknown = set(kwargs) - set(kwarg_to_field)
    if unknown:
        raise TypeError(f"unexpected keyword argument for {spec_cls.__name__}: {sorted(unknown)[0]!r}")
    mapped = {
        field_name: kwargs[kwarg_name]
        for kwarg_name, field_name in kwarg_to_field.items()
        if kwarg_name in kwargs
    }
    return spec_cls(**mapped)


@dataclass(frozen=True, slots=True)
class BtcDcaPlanConfig:
    """Aggregated immutable configuration for ``build_rebalance_plan``."""

    signal_symbol: str = DEFAULT_SIGNAL_SYMBOL
    base_investment_usd: float = 100.0
    max_investment_usd: float | None = None
    cash_reserve_usd: float = 0.0
    min_investment_usd: float = 5.0
    smart_multiplier_enabled: bool = True
    cycle_indicator_enabled: bool = True
    base_multiplier: float = 1.0
    execution_window: ExecutionWindowSpec = field(default_factory=ExecutionWindowSpec)
    drawdown_tiers: DrawdownTierSpec = field(default_factory=DrawdownTierSpec)
    ahr999: Ahr999Spec = field(default_factory=Ahr999Spec)
    zscore_exit: ZScoreExitSpec = field(default_factory=ZScoreExitSpec)

    @classmethod
    def supported_kwarg_names(cls) -> frozenset[str]:
        """Return all flat configuration keyword names accepted by ``from_kwargs``."""
        return _SUPPORTED_PLAN_KWARGS

    @classmethod
    def from_kwargs(cls, **kwargs: Any) -> BtcDcaPlanConfig:
        """Construct a ``BtcDcaPlanConfig`` from flat manifest or legacy kwargs."""
        unknown = set(kwargs) - _SUPPORTED_PLAN_KWARGS
        if unknown:
            raise TypeError(f"unexpected BTC DCA plan keyword argument: {sorted(unknown)[0]!r}")
        if not kwargs:
            return DEFAULT_BTC_DCA_PLAN_CONFIG

        top_kwargs: dict[str, Any] = {
            k: v for k, v in kwargs.items() if k in _TOP_LEVEL_PLAN_FIELDS
        }
        window_kwargs = {k: v for k, v in kwargs.items() if k in _EXECUTION_WINDOW_FIELDS}
        drawdown_kwargs = {k: v for k, v in kwargs.items() if k in _DRAWDOWN_TIER_FIELDS}
        ahr999_raw = {k: v for k, v in kwargs.items() if k in _AHR999_KWARG_TO_FIELD}
        zscore_raw = {k: v for k, v in kwargs.items() if k in _ZSCORE_EXIT_KWARG_TO_FIELD}

        if window_kwargs:
            top_kwargs["execution_window"] = ExecutionWindowSpec(**window_kwargs)
        if drawdown_kwargs:
            top_kwargs["drawdown_tiers"] = DrawdownTierSpec(**drawdown_kwargs)
        if ahr999_raw:
            top_kwargs["ahr999"] = _build_spec_from_mapped_kwargs(
                Ahr999Spec, _AHR999_KWARG_TO_FIELD, ahr999_raw,
            )
        if zscore_raw:
            top_kwargs["zscore_exit"] = _build_spec_from_mapped_kwargs(
                ZScoreExitSpec, _ZSCORE_EXIT_KWARG_TO_FIELD, zscore_raw,
            )
        return cls(**top_kwargs)


DEFAULT_BTC_DCA_PLAN_CONFIG = BtcDcaPlanConfig()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_timestamp(value: object) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp.normalize()


def _localized_regime(regime: str, translator) -> str:
    labels: dict[str, tuple[str, str]] = {
        "ordinary_dca": ("ordinary DCA", "普通定投"),
        "normal": ("normal", "正常"),
        "expensive": ("expensive", "偏贵"),
        "very_expensive_overbought": ("very expensive and overbought", "极贵且超买"),
        "mild_pullback": ("mild pullback", "温和回撤"),
        "deep_pullback": ("deep pullback", "深度回撤"),
        "severe_pullback": ("severe pullback", "严重回撤"),
        "ahr999_bottom": ("AHR999 bottom zone", "AHR999 底部区"),
        "ahr999_accumulation": ("AHR999 accumulation zone", "AHR999 囤币区"),
        "ahr999_dca": ("AHR999 DCA zone", "AHR999 定投区"),
        "ahr999_expensive": ("AHR999 expensive zone", "AHR999 偏贵区"),
    }
    fallback_en, fallback_zh = labels.get(regime, (regime, regime))
    return translate_with_fallback(
        translator,
        f"btc_dca_regime_{regime}",
        fallback_en=fallback_en,
        fallback_zh=fallback_zh,
    )


def _localized_skip_reason(skip_reason: str, translator) -> str:
    labels: dict[str, tuple[str, str]] = {
        "outside_execution_window": ("outside execution window", "不在执行窗口"),
        "valuation_too_expensive": ("valuation too expensive", "估值过贵"),
        "insufficient_cash": ("insufficient cash", "可投资现金不足"),
    }
    fallback_en, fallback_zh = labels.get(skip_reason, (skip_reason, skip_reason))
    return translate_with_fallback(
        translator,
        f"btc_dca_skip_{skip_reason}",
        fallback_en=fallback_en,
        fallback_zh=fallback_zh,
    )


# ---------------------------------------------------------------------------
# Execution window
# ---------------------------------------------------------------------------


def _is_in_execution_window(
    as_of: object,
    *,
    spec: ExecutionWindowSpec | None = None,
    **legacy_kwargs: Any,
) -> tuple[bool, str]:
    if spec is not None and legacy_kwargs:
        raise TypeError("cannot combine 'spec' with legacy execution window keyword arguments")
    if spec is None:
        spec = ExecutionWindowSpec(**legacy_kwargs)
    timestamp = _as_timestamp(as_of)
    cadence_key = str(spec.cadence or "monthly").strip().lower()

    if cadence_key == "weekly":
        day = int(max(0, min(6, spec.weekly_day)))
        window = int(max(1, min(7, spec.weekly_window_calendar_days)))
        days_since_start = (int(timestamp.weekday()) - day) % 7
        return days_since_start < window, f"weekly_day={day} window={window}d"

    if cadence_key == "quarterly":
        months = _normalize_quarterly_months(spec.quarterly_months)
        start_day = int(max(1, min(31, spec.quarterly_day)))
        window = int(max(1, spec.quarterly_window_calendar_days))
        months_text = ",".join(str(m) for m in months)
        return (
            timestamp.month in months and start_day <= int(timestamp.day) < start_day + window,
            f"quarterly_months={months_text} day={start_day} window={window}d",
        )

    if cadence_key != "monthly":
        raise ValueError("cadence must be 'monthly', 'weekly', or 'quarterly'")

    start_day = int(max(1, min(31, spec.monthly_day)))
    window = int(max(1, spec.monthly_window_calendar_days))
    return (
        start_day <= int(timestamp.day) < start_day + window,
        f"monthly_day={start_day} window={window}d",
    )


def _normalize_quarterly_months(raw_months: object) -> tuple[int, ...]:
    if raw_months is None:
        candidates: object = (1, 4, 7, 10)
    elif isinstance(raw_months, str):
        candidates = raw_months.replace(";", ",").split(",")
    else:
        candidates = raw_months
    months: list[int] = []
    try:
        iterator = iter(candidates)  # type: ignore[arg-type]
    except TypeError:
        iterator = iter((candidates,))
    for item in iterator:
        try:
            month = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12 and month not in months:
            months.append(month)
    return tuple(months) or (1, 4, 7, 10)


# ---------------------------------------------------------------------------
# Dynamic BTC target ratio (from crypto_live_pool_rotation.core)
# ---------------------------------------------------------------------------


def get_dynamic_btc_target_ratio(total_equity: float) -> float:
    """Compute the target BTC allocation ratio based on total equity.

    Formula: 0.14 + 0.16 * log1p(equity / 10000)
    Clamped to [0.0, 0.65].
    """
    safe_equity = max(float(total_equity), 1.0)
    ratio = 0.14 + 0.16 * math.log1p(safe_equity / 10000.0)
    return min(0.65, max(0.0, ratio))


# ---------------------------------------------------------------------------
# Indicator extraction
# ---------------------------------------------------------------------------


def _resolve_indicator_payload(
    indicator_snapshot: Mapping[str, object] | None,
    symbol: str,
) -> Mapping[str, object] | None:
    if not isinstance(indicator_snapshot, Mapping):
        return None
    # Direct match: snapshot itself contains indicator keys
    if any(
        str(key).lower() in {"ahr999", "ahr_999", "ahr999_gma", "close", "price"}
        for key in indicator_snapshot
    ):
        return indicator_snapshot

    candidates = {
        symbol, symbol.upper(),
        symbol.replace("-", ""), symbol.replace("-", "").upper(),
        "BTC", "BTC-USD", "BTCUSDT",
    }
    for key in candidates:
        value = indicator_snapshot.get(key)
        if isinstance(value, Mapping):
            return value
    normalized_snapshot = {
        str(key).strip().upper().replace("-", ""): value
        for key, value in indicator_snapshot.items()
    }
    for key in candidates:
        value = normalized_snapshot.get(key.upper().replace("-", ""))
        if isinstance(value, Mapping):
            return value
    return None


def _indicator_from_payload(symbol: str, payload: Mapping[str, object]) -> dict[str, float | None]:
    price = payload_numeric(payload, "close", "price", "last", "last_price")
    sma200 = payload_numeric(payload, "sma200", "ma200", "sma_200")
    high252 = payload_numeric(payload, "high252", "high_252", "high252d", "high_252d")
    if pd.isna(price) or pd.isna(sma200):
        return {}
    if pd.isna(high252):
        high252 = max(price, sma200)
    drawdown = payload_numeric(payload, "drawdown_252d", "drawdown252", "drawdown")
    if pd.isna(drawdown):
        drawdown = 0.0 if high252 <= 0.0 else max(0.0, 1.0 - price / high252)
    sma_gap = payload_numeric(payload, "sma200_gap", "gap_vs_sma200", "price_vs_sma200")
    if pd.isna(sma_gap) and sma200 > 0.0:
        sma_gap = price / sma200 - 1.0
    rsi14 = payload_numeric(payload, "rsi14", "rsi_14", "rsi")
    return {
        "price": float(price),
        "sma200": float(sma200),
        "high252": float(high252),
        "drawdown_252d": float(drawdown if not pd.isna(drawdown) else 0.0),
        "sma200_gap": float(sma_gap if not pd.isna(sma_gap) else 0.0),
        "rsi14": None if pd.isna(rsi14) else float(rsi14),
    }


# ---------------------------------------------------------------------------
# Cycle metrics (AHR999, Mayer Multiple)
# ---------------------------------------------------------------------------


def _bitcoin_age_estimate_price(as_of: object) -> float:
    timestamp = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    age_days = max(1, int((timestamp.normalize() - BITCOIN_GENESIS_DATE).days))
    return float(10 ** (5.84 * math.log10(age_days) - 17.01))


def _cycle_metrics_from_payload(
    payload: Mapping[str, object] | None,
    *,
    as_of: object,
) -> dict[str, float | str]:
    if not isinstance(payload, Mapping):
        return {}
    ahr999_gma = payload_numeric(payload, "ahr999_gma", "ahr999", "ahr_999", "ahr999_index")
    ahr999_sma = payload_numeric(payload, "ahr999_sma", "ahr999_sma200")
    mayer = payload_numeric(payload, "mayer_multiple", "mayer", "price_sma200_ratio")
    price = payload_numeric(payload, "close", "price", "last", "last_price")
    sma200 = payload_numeric(payload, "sma200", "ma200", "sma_200")
    estimate_price = payload_numeric(payload, "ahr999_estimate_price", "estimate_price")
    if pd.isna(estimate_price):
        estimate_price = _bitcoin_age_estimate_price(as_of)
    if pd.isna(mayer) and not pd.isna(price) and not pd.isna(sma200) and sma200 > 0.0:
        mayer = price / sma200
    if pd.isna(ahr999_sma) and not pd.isna(price) and not pd.isna(sma200) and sma200 > 0.0 and estimate_price > 0.0:
        ahr999_sma = (price / sma200) * (price / estimate_price)
    if pd.isna(ahr999_gma):
        ahr999_gma = ahr999_sma
    if pd.isna(ahr999_gma):
        return {}
    metrics: dict[str, float | str] = {"ahr999": float(ahr999_gma)}
    if not pd.isna(ahr999_sma):
        metrics["ahr999_sma"] = float(ahr999_sma)
    if not pd.isna(mayer):
        metrics["mayer_multiple"] = float(mayer)
    if not pd.isna(estimate_price):
        metrics["ahr999_estimate_price"] = float(estimate_price)
    if metrics:
        metrics["cycle_indicator_source"] = "derived_indicators"
    return metrics


# ---------------------------------------------------------------------------
# Multiplier logic
# ---------------------------------------------------------------------------


def _determine_multiplier(
    indicator: dict[str, float | None],
    *,
    spec: DrawdownTierSpec | None = None,
    base_multiplier: float = 1.0,
    **legacy_kwargs: Any,
) -> tuple[float, str, dict[str, float]]:
    if spec is not None and legacy_kwargs:
        raise TypeError("cannot combine 'spec' with legacy drawdown tier keyword arguments")
    if spec is None:
        spec = DrawdownTierSpec(**legacy_kwargs)
    drawdown = float(indicator.get("drawdown_252d", 0.0) or 0.0)
    sma_gap = float(indicator.get("sma200_gap", 0.0) or 0.0)
    rsi14 = indicator.get("rsi14")
    rsi_value = float(rsi14) if rsi14 is not None else float("nan")

    metrics = {
        "drawdown_252d": drawdown,
        "sma200_gap": sma_gap,
        "rsi14": rsi_value,
    }

    if drawdown >= spec.severe_drawdown_threshold:
        return float(spec.severe_pullback_multiplier), "severe_pullback", metrics
    if drawdown >= spec.deep_drawdown_threshold or sma_gap <= -abs(float(spec.deep_discount_gap)):
        return float(spec.deep_pullback_multiplier), "deep_pullback", metrics
    if drawdown >= spec.mild_drawdown_threshold or sma_gap <= -abs(float(spec.mild_discount_gap)):
        return float(spec.mild_pullback_multiplier), "mild_pullback", metrics
    if (
        sma_gap >= spec.very_expensive_gap
        and drawdown <= spec.shallow_drawdown_threshold
        and not pd.isna(rsi_value)
        and rsi_value >= spec.overbought_rsi
    ):
        return float(spec.very_expensive_multiplier), "very_expensive_overbought", metrics
    if sma_gap >= spec.expensive_gap and drawdown <= spec.shallow_drawdown_threshold:
        return float(spec.expensive_multiplier), "expensive", metrics
    return float(base_multiplier), "normal", metrics


def _determine_cycle_multiplier(
    cycle_metrics: Mapping[str, object],
    *,
    spec: Ahr999Spec | None = None,
    base_multiplier: float = 1.0,
    **legacy_kwargs: Any,
) -> tuple[float, str]:
    if spec is not None and legacy_kwargs:
        raise TypeError("cannot combine 'spec' with legacy AHR999 keyword arguments")
    if spec is None:
        spec = _build_spec_from_mapped_kwargs(Ahr999Spec, _AHR999_KWARG_TO_FIELD, legacy_kwargs)
    ahr999 = coerce_float(cycle_metrics.get("ahr999"), default=float("nan"))
    if pd.isna(ahr999):
        return float(base_multiplier), "normal"
    if ahr999 <= float(spec.bottom_threshold):
        return float(spec.bottom_multiplier), "ahr999_bottom"
    if ahr999 <= float(spec.accumulation_threshold):
        return float(spec.accumulation_multiplier), "ahr999_accumulation"
    if ahr999 <= float(spec.dca_threshold):
        return float(spec.dca_multiplier), "ahr999_dca"
    return float(spec.expensive_multiplier), "ahr999_expensive"


# ---------------------------------------------------------------------------
# Z-score exit (逃顶)
# ---------------------------------------------------------------------------


def _find_zscore_exit_payload(
    metadata: Mapping[str, object] | None,
    explicit_context: Mapping[str, object] | None,
) -> tuple[Mapping[str, object] | None, str]:
    sources: list[tuple[str, object, bool]] = []
    if isinstance(explicit_context, Mapping):
        sources.append(("explicit", explicit_context, True))
    if isinstance(metadata, Mapping):
        sources.append(("metadata.btc_zscore_exit", metadata.get(ZSCORE_EXIT_PROFILE), True))
        sources.append(("metadata.strategy_plugins", metadata.get("strategy_plugins"), False))

    for source_name, source, _allow_pluginless in sources:
        if not isinstance(source, (Mapping, Sequence)) or isinstance(source, (str, bytes, bytearray)):
            continue
        for payload in _iter_mapping_payloads(source):
            plugin = str(payload.get("plugin") or payload.get("profile") or "").strip().lower()
            if plugin == ZSCORE_EXIT_PROFILE:
                return payload, source_name
            if not _allow_pluginless and not plugin:
                continue
            if bool({"position_control", "target_allocations", "route", "canonical_route"} & set(payload)):
                return payload, source_name
    return None, ""


def _iter_mapping_payloads(value, *, _depth: int = 0):
    if _depth > 4:
        return
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _iter_mapping_payloads(item, _depth=_depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_mapping_payloads(item, _depth=_depth + 1)


def _resolve_zscore_exit_context(
    portfolio_metadata: Mapping[str, object] | None,
    *,
    spec: ZScoreExitSpec | None = None,
    explicit_context: Mapping[str, object] | None = None,
    **legacy_kwargs: Any,
) -> dict[str, object]:
    if spec is not None and legacy_kwargs:
        raise TypeError("cannot combine 'spec' with legacy Z-score exit keyword arguments")
    if spec is None:
        if any(k in _ZSCORE_EXIT_KWARG_TO_FIELD for k in legacy_kwargs):
            spec = _build_spec_from_mapped_kwargs(
                ZScoreExitSpec, _ZSCORE_EXIT_KWARG_TO_FIELD, legacy_kwargs,
            )
        else:
            spec = ZScoreExitSpec(**legacy_kwargs)
    config_enabled = coerce_bool(spec.enabled, default=True)
    parking_symbol = normalize_symbol(spec.parking_symbol) or DEFAULT_PARKING_SYMBOL

    payload, source = _find_zscore_exit_payload(portfolio_metadata, explicit_context)
    if not isinstance(payload, Mapping):
        return {
            "enabled": bool(config_enabled),
            "found": False, "source": "", "active": False, "applied": False,
            "route": "", "parking_symbol": parking_symbol,
            "target_btc_exposure": 1.0, "target_parking_exposure": 0.0,
        }

    position_control = payload.get("position_control")
    if not isinstance(position_control, Mapping):
        position_control = {}
    route = str(
        position_control.get("final_route")
        or position_control.get("route")
        or payload.get("canonical_route")
        or payload.get("route")
        or ""
    ).strip().lower()

    target_allocations = (
        position_control.get("target_allocations")
        or position_control.get("target_exposure")
        or payload.get("target_allocations")
    )
    if isinstance(target_allocations, Mapping):
        target_allocations = dict(target_allocations)
    else:
        target_allocations = {}

    target_btc_exposure = as_clamped_ratio(
        coerce_float(target_allocations.get("BTCUSDT"), default=float("nan")),
        default=1.0,
    )
    if pd.isna(coerce_float(target_allocations.get("BTCUSDT"), default=float("nan"))):
        for raw_value in (
            position_control.get("target_btc_exposure"),
            payload.get("target_btc_exposure"),
        ):
            numeric = coerce_float(raw_value, default=float("nan"))
            if not pd.isna(numeric):
                target_btc_exposure = as_clamped_ratio(numeric, default=1.0)
                break
        if route == "risk_reduced":
            target_btc_exposure = as_clamped_ratio(float(spec.risk_reduced_exposure), default=0.50)
        elif route == "risk_off":
            target_btc_exposure = as_clamped_ratio(float(spec.risk_off_exposure), default=0.25)

    active = route in ZSCORE_EXIT_POSITION_ROUTES

    return {
        "enabled": bool(config_enabled),
        "found": True, "source": source, "active": bool(active),
        "applied": bool(config_enabled and active),
        "route": route,
        "parking_symbol": parking_symbol,
        "target_btc_exposure": float(target_btc_exposure),
        "target_parking_exposure": float(1.0 - target_btc_exposure),
    }


# ---------------------------------------------------------------------------
# Portfolio helpers
# ---------------------------------------------------------------------------


def _portfolio_metrics(snapshot) -> dict[str, float]:
    """Extract key portfolio metrics from a snapshot object or dict."""
    if isinstance(snapshot, Mapping):
        total_equity = float(snapshot.get("total_equity", 0.0) or 0.0)
        buying_power = float(snapshot.get("buying_power", snapshot.get("cash_balance", 0.0)) or 0.0)
        positions = snapshot.get("positions", ())
    else:
        total_equity = float(getattr(snapshot, "total_equity", 0.0) or 0.0)
        buying_power = float(
            getattr(snapshot, "buying_power", getattr(snapshot, "cash_balance", 0.0)) or 0.0
        )
        positions = getattr(snapshot, "positions", ())

    btc_value = 0.0
    for pos in positions or ():
        if isinstance(pos, Mapping):
            symbol = str(pos.get("symbol", "")).strip().upper()
            market_value = float(pos.get("market_value", 0.0) or 0.0)
        else:
            symbol = str(getattr(pos, "symbol", "")).strip().upper()
            market_value = float(getattr(pos, "market_value", 0.0) or 0.0)
        if symbol == "BTCUSDT":
            btc_value += market_value

    if isinstance(snapshot, Mapping):
        metadata = snapshot.get("metadata", {}) or {}
    else:
        metadata = getattr(snapshot, "metadata", {}) or {}
    if isinstance(metadata, Mapping):
        btc_value = float(metadata.get("dca_value", btc_value))

    return {
        "total_equity": total_equity,
        "buying_power": max(0.0, buying_power),
        "btc_value": max(0.0, btc_value),
    }


# ---------------------------------------------------------------------------
# Core: build_rebalance_plan and step helpers
# ---------------------------------------------------------------------------


def _resolve_plan_config(
    config: BtcDcaPlanConfig | None,
    legacy_kwargs: Mapping[str, Any],
) -> BtcDcaPlanConfig:
    if config is not None:
        if legacy_kwargs:
            unexpected = ", ".join(sorted(legacy_kwargs))
            raise TypeError(
                f"build_rebalance_plan() cannot combine 'config' with legacy keyword arguments: {unexpected}"
            )
        return config
    if not legacy_kwargs:
        return DEFAULT_BTC_DCA_PLAN_CONFIG
    return BtcDcaPlanConfig.from_kwargs(**legacy_kwargs)


def _compute_smart_multiplier(
    plan_config: BtcDcaPlanConfig,
    *,
    symbol: str,
    as_of: object,
    derived_indicators: Mapping[str, object] | None,
) -> tuple[bool, float, str, dict[str, float | str]]:
    smart_enabled = coerce_bool(plan_config.smart_multiplier_enabled, default=True)
    cycle_metrics: dict[str, float | str] = {}
    regime = "ordinary_dca"
    multiplier = 1.0
    aggregate_metrics: dict[str, float | str] = {
        "drawdown_252d": float("nan"),
        "sma200_gap": float("nan"),
        "rsi14": float("nan"),
    }

    if smart_enabled:
        payload = _resolve_indicator_payload(derived_indicators, symbol)
        indicator = _indicator_from_payload(symbol, payload) if payload else {}

        if coerce_bool(plan_config.cycle_indicator_enabled, default=True):
            cycle_metrics = _cycle_metrics_from_payload(payload, as_of=as_of)

        if cycle_metrics and not pd.isna(coerce_float(cycle_metrics.get("ahr999"), default=float("nan"))):
            multiplier, regime = _determine_cycle_multiplier(
                cycle_metrics,
                spec=plan_config.ahr999,
                base_multiplier=float(plan_config.base_multiplier),
            )
            if indicator:
                aggregate_metrics.update({
                    "drawdown_252d": float(indicator.get("drawdown_252d", 0.0) or 0.0),
                    "sma200_gap": float(indicator.get("sma200_gap", 0.0) or 0.0),
                    "rsi14": float(indicator.get("rsi14") or float("nan")),
                })
        elif indicator:
            multiplier, regime, drawdown_metrics = _determine_multiplier(
                indicator,
                spec=plan_config.drawdown_tiers,
                base_multiplier=float(plan_config.base_multiplier),
            )
            aggregate_metrics.update(drawdown_metrics)
        # else: no indicator data available, use base_multiplier

    if cycle_metrics:
        aggregate_metrics.update({
            "ahr999": float(coerce_float(cycle_metrics.get("ahr999"), default=float("nan"))),
            "ahr999_sma": float(coerce_float(cycle_metrics.get("ahr999_sma"), default=float("nan"))),
            "mayer_multiple": float(coerce_float(cycle_metrics.get("mayer_multiple"), default=float("nan"))),
            "cycle_indicator_source": str(cycle_metrics.get("cycle_indicator_source", "none")),
        })

    regime_multiplier = float(multiplier if smart_enabled else 1.0)
    return smart_enabled, regime_multiplier, regime, aggregate_metrics


def _resolve_investment_plan(
    plan_config: BtcDcaPlanConfig,
    *,
    symbol: str,
    is_window: bool,
    buying_power: float,
    btc_value: float,
    regime_multiplier: float,
    zscore_exit: dict[str, object],
    strict_buy_window: bool,
) -> dict[str, object]:
    reserved_cash = max(0.0, coerce_float(plan_config.cash_reserve_usd))
    investable_cash = max(0.0, buying_power - reserved_cash)
    requested_investment = max(0.0, float(plan_config.base_investment_usd) * max(0.0, regime_multiplier))
    if plan_config.max_investment_usd is not None:
        requested_investment = min(requested_investment, max(0.0, float(plan_config.max_investment_usd)))
    planned_investment = min(requested_investment, investable_cash)
    cash_capped = investable_cash < requested_investment

    skip_reason: str | None = None
    actionable = True
    if not is_window:
        skip_reason = "outside_execution_window"
        actionable = False
    elif regime_multiplier <= 0.0 or requested_investment <= 0.0:
        skip_reason = "valuation_too_expensive"
        actionable = False
    elif planned_investment < float(plan_config.min_investment_usd):
        skip_reason = "insufficient_cash"
        actionable = False

    zscore_overlay_applied = bool(zscore_exit["applied"])
    if zscore_overlay_applied and not is_window and not coerce_bool(
        plan_config.zscore_exit.allow_outside_execution_window, default=True,
    ):
        zscore_overlay_applied = False
        zscore_exit["applied"] = False

    # Standalone validation permits an out-of-window exit, never an extra buy.
    planned_investment = float(
        planned_investment if actionable or (zscore_overlay_applied and not strict_buy_window) else 0.0
    )

    target_btc_value = btc_value
    if zscore_overlay_applied:
        controlled_value = btc_value + planned_investment
        target_btc_value = controlled_value * float(zscore_exit["target_btc_exposure"])
        actionable = True
        skip_reason = None
    elif actionable:
        target_btc_value = btc_value + planned_investment

    displayed_planned = float(planned_investment if actionable else 0.0)
    return {
        "reserved_cash": reserved_cash,
        "investable_cash": investable_cash,
        "requested_investment": requested_investment,
        "planned_investment": planned_investment,
        "displayed_planned": displayed_planned,
        "cash_capped": cash_capped,
        "actionable": actionable,
        "skip_reason": skip_reason,
        "target_values": {symbol: target_btc_value} if actionable else {},
    }


def _format_plan_descriptions(
    *,
    smart_enabled: bool,
    regime: str,
    regime_multiplier: float,
    planned_investment: float,
    displayed_planned: float,
    requested_investment: float,
    investable_cash: float,
    cash_capped: bool,
    skip_reason: str | None,
    zscore_exit: Mapping[str, object],
    window_text: str,
    aggregate_metrics: Mapping[str, float | str],
    translator: Any,
) -> tuple[str, str]:
    localized_regime = _localized_regime(regime, translator)
    cash_for_display = float(investable_cash)

    if smart_enabled:
        signal_desc = translate_with_fallback(
            translator,
            "btc_dca_smart_signal",
            fallback_en=(
                "BTC Smart DCA {regime}: multiplier {multiplier}, "
                "planned buy ${planned_investment} from cash ${available_cash}"
            ),
            fallback_zh=(
                "BTC 智能定投 {regime}: 倍数 {multiplier}，计划买入 ${planned_investment}，"
                "现金 ${available_cash}"
            ),
            regime=localized_regime,
            multiplier=f"{regime_multiplier:.2f}x",
            planned_investment=f"{displayed_planned:,.2f}",
            available_cash=f"{cash_for_display:,.2f}",
        )
    else:
        signal_desc = translate_with_fallback(
            translator,
            "btc_dca_ordinary_signal",
            fallback_en="BTC ordinary DCA: planned buy ${planned_investment} from cash ${available_cash}",
            fallback_zh="BTC 普通定投：计划买入 ${planned_investment}，现金 ${available_cash}",
            planned_investment=f"{displayed_planned:,.2f}",
            available_cash=f"{cash_for_display:,.2f}",
        )

    if cash_capped and planned_investment > 0.0:
        signal_desc = translate_with_fallback(
            translator,
            "btc_dca_cash_capped",
            fallback_en="{signal} | cash capped from ${requested_investment}",
            fallback_zh="{signal} | 因现金限制，低于请求金额 ${requested_investment}",
            signal=signal_desc,
            requested_investment=f"{requested_investment:,.2f}",
        )

    if skip_reason:
        signal_desc = translate_with_fallback(
            translator,
            "btc_dca_skip",
            fallback_en="{signal} | skip: {skip_reason}",
            fallback_zh="{signal} | 跳过：{skip_reason}",
            signal=signal_desc,
            skip_reason=_localized_skip_reason(skip_reason, translator),
        )

    if zscore_exit["found"]:
        signal_desc = translate_with_fallback(
            translator,
            "btc_dca_zscore_overlay",
            fallback_en=(
                "{signal} | Z-Score exit: {route}, target {btc_pct} BTC / "
                "{parking_pct} {parking_symbol}"
            ),
            fallback_zh=(
                "{signal} | Z-Score 逃顶: {route}，目标 {btc_pct} BTC / "
                "{parking_pct} {parking_symbol}"
            ),
            signal=signal_desc,
            route=str(zscore_exit["route"] or "inactive"),
            btc_pct=f"{float(zscore_exit['target_btc_exposure']):.0%}",
            parking_pct=f"{float(zscore_exit['target_parking_exposure']):.0%}",
            parking_symbol=str(zscore_exit["parking_symbol"]),
        )

    if smart_enabled:
        cycle_text = ""
        ahr999_val = aggregate_metrics.get("ahr999")
        if ahr999_val is not None and not pd.isna(ahr999_val):
            cycle_text = f", AHR999 {float(ahr999_val):.2f}"
        status_desc = translate_with_fallback(
            translator,
            "btc_dca_status",
            fallback_en="{window} | drawdown {drawdown}, gap vs SMA200 {sma_gap}{cycle_text}",
            fallback_zh="{window} | 回撤 {drawdown}，SMA200偏离 {sma_gap}{cycle_text}",
            window=window_text,
            drawdown=f"{float(aggregate_metrics.get('drawdown_252d', float('nan'))):.1%}",
            sma_gap=f"{float(aggregate_metrics.get('sma200_gap', float('nan'))):.1%}",
            cycle_text=cycle_text,
        )
    else:
        status_desc = translate_with_fallback(
            translator,
            "btc_dca_status_ordinary",
            fallback_en="{window} | ordinary DCA",
            fallback_zh="{window} | 普通定投",
            window=window_text,
        )
    return signal_desc, status_desc


def build_rebalance_plan(
    portfolio: Any,
    *,
    as_of: object = None,
    config: BtcDcaPlanConfig | None = None,
    derived_indicators: Mapping[str, object] | None = None,
    zscore_exit_context: Mapping[str, object] | None = None,
    translator: Any = None,
    strict_buy_window: bool = False,
    **legacy_kwargs: Any,
) -> dict[str, object]:
    """Produce a full rebalance plan for the BTC DCA strategy."""
    plan_config = _resolve_plan_config(config, legacy_kwargs)
    symbol = normalize_symbol(plan_config.signal_symbol) or DEFAULT_SIGNAL_SYMBOL

    is_window, window_text = _is_in_execution_window(
        as_of,
        spec=plan_config.execution_window,
    )

    pm = _portfolio_metrics(portfolio)
    total_equity = pm["total_equity"]
    buying_power = pm["buying_power"]
    btc_value = pm["btc_value"]

    if isinstance(portfolio, Mapping):
        portfolio_metadata = portfolio.get("metadata", {}) or {}
    else:
        portfolio_metadata = getattr(portfolio, "metadata", {}) or {}

    zscore_exit = _resolve_zscore_exit_context(
        portfolio_metadata,
        spec=plan_config.zscore_exit,
        explicit_context=zscore_exit_context,
    )

    smart_enabled, regime_multiplier, regime, aggregate_metrics = _compute_smart_multiplier(
        plan_config,
        symbol=symbol,
        as_of=as_of,
        derived_indicators=derived_indicators,
    )

    sizing = _resolve_investment_plan(
        plan_config,
        symbol=symbol,
        is_window=is_window,
        buying_power=buying_power,
        btc_value=btc_value,
        regime_multiplier=regime_multiplier,
        zscore_exit=zscore_exit,
        strict_buy_window=strict_buy_window,
    )

    signal_desc, status_desc = _format_plan_descriptions(
        smart_enabled=smart_enabled,
        regime=regime,
        regime_multiplier=regime_multiplier,
        planned_investment=float(sizing["planned_investment"]),
        displayed_planned=float(sizing["displayed_planned"]),
        requested_investment=float(sizing["requested_investment"]),
        investable_cash=float(sizing["investable_cash"]),
        cash_capped=bool(sizing["cash_capped"]),
        skip_reason=sizing["skip_reason"],  # type: ignore[arg-type]
        zscore_exit=zscore_exit,
        window_text=window_text,
        aggregate_metrics=aggregate_metrics,
        translator=translator,
    )

    return {
        "actionable": bool(sizing["actionable"]),
        "skip_reason": sizing["skip_reason"],
        "target_values": sizing["target_values"],
        "managed_symbols": (symbol,),
        "signal_symbol": symbol,
        "signal_description": signal_desc,
        "status_description": status_desc,
        "regime": regime,
        "multiplier": float(regime_multiplier),
        "regime_multiplier": float(regime_multiplier),
        "smart_multiplier_enabled": bool(smart_enabled),
        "base_investment_usd": float(plan_config.base_investment_usd),
        "requested_investment_usd": float(sizing["requested_investment"]),
        "planned_investment_usd": float(sizing["displayed_planned"]),
        "available_cash": float(buying_power),
        "reserved_cash": float(sizing["reserved_cash"]),
        "investable_cash": float(sizing["investable_cash"]),
        "cash_capped": bool(sizing["cash_capped"]),
        "min_investment_usd": float(plan_config.min_investment_usd),
        "execution_window": window_text,
        "in_execution_window": bool(is_window),
        "zscore_exit": zscore_exit,
        "total_equity": float(total_equity),
        "btc_value": float(btc_value),
        **aggregate_metrics,
    }


# ---------------------------------------------------------------------------
# Public API (contract-compatible with existing callers)
# ---------------------------------------------------------------------------


def build_target_weights(
    prices: dict[str, float | None],
    portfolio: Any,
    total_equity: float,
) -> dict[str, float]:
    """Build target weights mapping.

    Always returns {BTCUSDT: 1.0} since this is a single-asset DCA strategy.
    The BTC target ratio is computed via get_dynamic_btc_target_ratio but the
    weight returned here always reflects a 100% allocation of the available
    DCA budget to BTCUSDT. The ratio is applied externally.
    """
    return {"BTCUSDT": 1.0}


def compute_signals(
    prices: dict[str, float | None],
    portfolio: Any,
    total_equity: float,
    state: dict[str, Any] | None = None,
    *,
    config: BtcDcaPlanConfig | None = None,
    derived_indicators: Mapping[str, object] | None = None,
    translator=None,
    strict_plan: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute DCA signals for BTC.

    Returns a dict compatible with both the standalone entrypoint and the
    combo strategy.
    """
    if state is None:
        state = {}

    plan: dict[str, object] = {}
    portfolio_for_plan = portfolio
    call_kwargs = dict(kwargs)
    as_of = call_kwargs.pop("as_of", None)
    zscore_exit_context = call_kwargs.pop("zscore_exit_context", None)

    if config is not None:
        smart_enabled = coerce_bool(config.smart_multiplier_enabled, default=True)
    else:
        smart_enabled = coerce_bool(
            call_kwargs.get("smart_multiplier_enabled", True), default=True,
        )
        base_amount = coerce_float(
            call_kwargs.get("base_investment_usd", 100.0), default=100.0,
        )
        call_kwargs["smart_multiplier_enabled"] = smart_enabled
        call_kwargs["base_investment_usd"] = base_amount

    try:
        if config is not None:
            if call_kwargs:
                unexpected = ", ".join(sorted(call_kwargs))
                raise TypeError(
                    f"compute_signals() cannot combine 'config' with legacy keyword arguments: {unexpected}"
                )
            plan_config = config
        else:
            plan_config = BtcDcaPlanConfig.from_kwargs(**call_kwargs)
        plan = build_rebalance_plan(
            portfolio_for_plan,
            as_of=as_of,
            config=plan_config,
            derived_indicators=derived_indicators,
            zscore_exit_context=zscore_exit_context,
            translator=translator,
            strict_buy_window=strict_plan,
        )
    except (ValueError, TypeError) as exc:
        if strict_plan:
            raise
        logger.warning("btc_dca build_rebalance_plan failed, falling back: %s", exc)
        plan = {}

    ratio = get_dynamic_btc_target_ratio(total_equity)

    result: dict[str, Any] = {
        "signals": {
            "BTCUSDT": {
                "target_weight": 1.0,
                "btc_target_ratio": ratio,
            }
        },
        "total_equity": total_equity,
        "btc_target_ratio": ratio,
        "profile": PROFILE_NAME,
    }

    if plan:
        result["metadata"] = {
            "actionable": plan.get("actionable", True),
            "target_values": plan.get("target_values", {}),
            "btc_value": plan.get("btc_value"),
            "regime": plan.get("regime", "ordinary_dca"),
            "multiplier": plan.get("multiplier", 1.0),
            "smart_multiplier_enabled": plan.get("smart_multiplier_enabled", smart_enabled),
            "planned_investment_usd": plan.get("planned_investment_usd", 0.0),
            "signal_description": plan.get("signal_description", ""),
            "status_description": plan.get("status_description", ""),
            "zscore_exit": plan.get("zscore_exit", {}),
            "in_execution_window": plan.get("in_execution_window", True),
            "execution_window": plan.get("execution_window", ""),
            "ahr999": plan.get("ahr999", float("nan")),
            "mayer_multiple": plan.get("mayer_multiple", float("nan")),
        }

    return result


def extract_managed_symbols(
    state: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return the single managed symbol for this strategy."""
    return ("BTCUSDT",)

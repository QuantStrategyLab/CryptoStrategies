"""Shared utilities for crypto strategies (no cross-package dependency)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(numeric):
        return default
    return numeric


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def as_clamped_ratio(value: Any, *, default: float) -> float:
    numeric = coerce_float(value, default=float("nan"))
    if pd.isna(numeric):
        return float(default)
    return max(0.0, min(1.0, float(numeric)))


def payload_numeric(payload: dict[str, Any], *keys: str) -> float:
    lowered = {str(key).strip().lower(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        numeric = coerce_float(value, default=float("nan"))
        if not pd.isna(numeric):
            return numeric
    return float("nan")


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------


def translate_with_fallback(
    translator,
    key: str,
    *,
    fallback_en: str,
    fallback_zh: str,
    **kwargs: object,
) -> str:
    if translator is None:
        template = fallback_zh
    else:
        try:
            translated = translator(key, **kwargs)
        except Exception:
            translated = key
        if translated == key:
            template = fallback_zh if translator_uses_zh(translator) else fallback_en
        else:
            return str(translated)
    try:
        return template.format(**{str(k): v for k, v in kwargs.items()})
    except (KeyError, ValueError):
        return template


def translator_uses_zh(translator) -> bool:
    try:
        sample = str(translator("no_trades"))
    except Exception:
        return False
    return any("一" <= char <= "鿿" for char in sample)

"""Unified crypto strategy entrypoints and package exports."""

from __future__ import annotations

from ._common import apply_risk_gate, record_strategy_decision
from ._metrics import (
    _default_symbol_state,
    _load_legacy_modules,
    _merge_runtime_config,
    _require_market_data,
    _resolve_account_metrics,
    _resolve_portfolio_snapshot,
    _resolve_state_helpers,
    _resolve_translator,
)
from .btc_dca import crypto_btc_dca_entrypoint, evaluate_crypto_btc_dca
from .equity_combo import crypto_equity_combo_entrypoint, evaluate_crypto_equity_combo
from .live_pool_rotation import (
    _resolve_held_risk_symbols,
    crypto_live_pool_rotation_entrypoint,
    evaluate_crypto_live_pool_rotation,
)
from .trend_rotation import (
    crypto_trend_rotation_entrypoint,
    evaluate_crypto_trend_rotation,
)

__all__ = [
    "crypto_live_pool_rotation_entrypoint",
    "evaluate_crypto_live_pool_rotation",
    "crypto_btc_dca_entrypoint",
    "evaluate_crypto_btc_dca",
    "crypto_trend_rotation_entrypoint",
    "evaluate_crypto_trend_rotation",
    "crypto_equity_combo_entrypoint",
    "evaluate_crypto_equity_combo",
]

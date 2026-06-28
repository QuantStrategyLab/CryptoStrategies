"""BTC DCA standalone strategy.

Profile: crypto_btc_dca
Domain: crypto
Source: market_prices

This is a pure DCA strategy targeting only BTCUSDT with a dynamic target
ratio based on total portfolio equity. It produces a single PositionTarget
and BudgetIntent per evaluation.
"""

from __future__ import annotations

import math
from typing import Any

CN_EQUITY_DOMAIN: str = "crypto"
SIGNAL_SOURCE: str = "market_prices"
STATUS_ICON: str = "\U0001f351"  # BTC emoji (peach)
PROFILE_NAME: str = "crypto_btc_dca"


def get_dynamic_btc_target_ratio(total_equity: float) -> float:
    """Compute the target BTC allocation ratio based on total equity.

    Formula: 0.14 + 0.16 * log1p(equity / 10000)
    Clamped to [0.0, 0.65].
    """
    safe_equity = max(float(total_equity), 1.0)
    ratio = 0.14 + 0.16 * math.log1p(safe_equity / 10000.0)
    return min(0.65, max(0.0, ratio))


def build_target_weights(
    prices: dict[str, float | None],
    portfolio: Any,
    total_equity: float,
) -> dict[str, float]:
    """Build target weights mapping.

    Always returns {BTCUSDT: 1.0} since this is a single-asset DCA strategy.
    The BTC target ratio is computed via get_dynamic_btc_target_ratio but the
    weight returned here always reflects a 100% allocation of the available
    DCA budget to BTCUSDT. The ratio is applied externally (e.g. in budget
    computation).
    """
    return {"BTCUSDT": 1.0}


def compute_signals(
    prices: dict[str, float | None],
    portfolio: Any,
    total_equity: float,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute DCA signals for BTC.

    Returns a dict with BTC target weight, the dynamic ratio, and the equity.
    """
    if state is None:
        state = {}
    ratio = get_dynamic_btc_target_ratio(total_equity)
    return {
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


def extract_managed_symbols(
    state: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return the single managed symbol for this strategy."""
    return ("BTCUSDT",)

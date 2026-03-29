import math
from typing import Any

import pandas as pd


DEFAULT_POOL_SCORE_WEIGHTS = {
    "trend_rank": 0.24,
    "persistence_rank": 0.20,
    "liq_rank": 0.18,
    "stability_rank": 0.14,
    "rel_core_rank": 0.14,
    "risk_adj_rank": 0.10,
}


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def get_dynamic_btc_target_ratio(total_equity: float) -> float:
    safe_equity = max(float(total_equity), 1.0)
    ratio = 0.14 + 0.16 * math.log1p(safe_equity / 10000.0)
    return min(0.65, max(0.0, ratio))


def get_dynamic_btc_base_order(total_equity: float) -> float:
    return max(15.0, float(total_equity) * 0.0012)


def compute_allocation_budgets(total_equity: float, cash_usdt: float, trend_val: float, dca_val: float):
    total_equity = float(total_equity)
    cash_usdt = max(0.0, float(cash_usdt))
    trend_val = max(0.0, float(trend_val))
    dca_val = max(0.0, float(dca_val))

    btc_target_ratio = get_dynamic_btc_target_ratio(total_equity)
    trend_target_ratio = 1.0 - btc_target_ratio
    trend_usdt_pool = max(0.0, min(cash_usdt, (total_equity * trend_target_ratio) - trend_val))
    remaining_cash = max(0.0, cash_usdt - trend_usdt_pool)
    dca_usdt_pool = max(0.0, min(remaining_cash, (total_equity * btc_target_ratio) - dca_val))
    trend_layer_equity = trend_val + trend_usdt_pool
    return {
        "btc_target_ratio": btc_target_ratio,
        "trend_target_ratio": trend_target_ratio,
        "trend_usdt_pool": trend_usdt_pool,
        "dca_usdt_pool": dca_usdt_pool,
        "trend_layer_equity": trend_layer_equity,
    }


def rank_normalize(values):
    if not values:
        return {}
    series = pd.Series(values, dtype=float)
    ranked = series.rank(method="average")
    denom = max(len(series) - 1, 1)
    normalized = (ranked - 1) / denom
    return normalized.to_dict()


def build_rotation_pool_ranking(
    indicators_map,
    btc_snapshot,
    previous_pool,
    *,
    min_history_days,
    min_avg_quote_vol_180,
    membership_bonus,
    score_weights=None,
):
    required_btc_fields = ["btc_roc20", "btc_roc60", "btc_roc120"]
    if any(is_missing(btc_snapshot.get(field)) for field in required_btc_fields):
        return []

    records = []
    for symbol, indicators in indicators_map.items():
        if indicators is None:
            continue

        required_fields = [
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
        ]
        if any(is_missing(indicators.get(field)) for field in required_fields):
            continue

        age_days = safe_float(indicators["age_days"])
        avg_quote_vol_180 = safe_float(indicators["avg_quote_vol_180"])
        vol20 = safe_float(indicators["vol20"])
        if age_days < float(min_history_days):
            continue
        if avg_quote_vol_180 < float(min_avg_quote_vol_180):
            continue
        if vol20 <= 0:
            continue

        close = safe_float(indicators["close"])
        sma20 = safe_float(indicators["sma20"])
        sma60 = safe_float(indicators["sma60"])
        sma200 = safe_float(indicators["sma200"])
        roc20 = safe_float(indicators["roc20"])
        roc60 = safe_float(indicators["roc60"])
        roc120 = safe_float(indicators["roc120"])
        rel_20 = roc20 - safe_float(btc_snapshot["btc_roc20"])
        rel_60 = roc60 - safe_float(btc_snapshot["btc_roc60"])
        rel_120 = roc120 - safe_float(btc_snapshot["btc_roc120"])
        price_vs_ma20 = close / sma20 - 1.0
        price_vs_ma60 = close / sma60 - 1.0
        price_vs_ma200 = close / sma200 - 1.0
        abs_momentum = 0.5 * roc20 + 0.3 * roc60 + 0.2 * roc120

        avg_quote_vol_30 = safe_float(indicators["avg_quote_vol_30"])
        avg_quote_vol_90 = safe_float(indicators["avg_quote_vol_90"])
        liquidity_floor = min(avg_quote_vol_30, avg_quote_vol_90, avg_quote_vol_180)
        liquidity_ceiling = max(avg_quote_vol_30, avg_quote_vol_90, avg_quote_vol_180)
        if liquidity_ceiling <= 0:
            continue
        liquidity_stability = liquidity_floor / liquidity_ceiling

        records.append(
            {
                "symbol": symbol,
                "liquidity": math.log1p(avg_quote_vol_180),
                "stability": liquidity_stability,
                "relative_strength_fast": 0.55 * rel_20 + 0.30 * rel_60 + 0.15 * rel_120,
                "relative_strength_core": 0.20 * rel_20 + 0.45 * rel_60 + 0.35 * rel_120,
                "trend_quality": 0.25 * price_vs_ma20 + 0.35 * price_vs_ma60 + 0.40 * price_vs_ma200,
                "breakout_strength": 0.60 * price_vs_ma20 + 0.40 * roc20,
                "acceleration": roc20 - roc60,
                "persistence": safe_float(indicators["trend_persist_90"]),
                "risk_adjusted_momentum": abs_momentum / vol20,
                "bonus": float(membership_bonus) if symbol in previous_pool else 0.0,
            }
        )

    if not records:
        return []

    liq_rank = rank_normalize({item["symbol"]: item["liquidity"] for item in records})
    stability_rank = rank_normalize({item["symbol"]: item["stability"] for item in records})
    rel_fast_rank = rank_normalize({item["symbol"]: item["relative_strength_fast"] for item in records})
    rel_core_rank = rank_normalize({item["symbol"]: item["relative_strength_core"] for item in records})
    trend_rank = rank_normalize({item["symbol"]: item["trend_quality"] for item in records})
    breakout_rank = rank_normalize({item["symbol"]: item["breakout_strength"] for item in records})
    accel_rank = rank_normalize({item["symbol"]: item["acceleration"] for item in records})
    persistence_rank = rank_normalize({item["symbol"]: item["persistence"] for item in records})
    risk_adj_rank = rank_normalize({item["symbol"]: item["risk_adjusted_momentum"] for item in records})

    effective_weights = dict(DEFAULT_POOL_SCORE_WEIGHTS)
    if score_weights:
        effective_weights.update(score_weights)

    ranking = []
    for item in records:
        symbol = item["symbol"]
        enriched = dict(item)
        enriched.update(
            {
                "liq_rank": liq_rank[symbol],
                "stability_rank": stability_rank[symbol],
                "rel_fast_rank": rel_fast_rank[symbol],
                "rel_core_rank": rel_core_rank[symbol],
                "trend_rank": trend_rank[symbol],
                "breakout_rank": breakout_rank[symbol],
                "accel_rank": accel_rank[symbol],
                "persistence_rank": persistence_rank[symbol],
                "risk_adj_rank": risk_adj_rank[symbol],
            }
        )
        score = enriched["bonus"]
        for rank_key, weight in effective_weights.items():
            if rank_key in enriched:
                score += safe_float(weight) * safe_float(enriched[rank_key])
        enriched["score"] = score
        ranking.append(enriched)

    ranking.sort(
        key=lambda item: (
            item["score"],
            item["relative_strength_core"],
            item["trend_quality"],
            item["risk_adjusted_momentum"],
        ),
        reverse=True,
    )
    return ranking


def build_stable_quality_pool(
    indicators_map,
    btc_snapshot,
    previous_pool,
    *,
    pool_size,
    min_history_days,
    min_avg_quote_vol_180,
    membership_bonus,
    score_weights=None,
):
    ranking = build_rotation_pool_ranking(
        indicators_map,
        btc_snapshot,
        previous_pool,
        min_history_days=min_history_days,
        min_avg_quote_vol_180=min_avg_quote_vol_180,
        membership_bonus=membership_bonus,
        score_weights=score_weights,
    )
    selected_pool = [item["symbol"] for item in ranking[: max(1, int(pool_size))]]
    return selected_pool, ranking


def select_rotation_weights(indicators_map, prices, btc_snapshot, candidate_pool, top_n, *, weight_mode="inverse_vol"):
    if not btc_snapshot.get("regime_on"):
        return {}
    required_btc_fields = ["btc_roc20", "btc_roc60", "btc_roc120"]
    if any(is_missing(btc_snapshot.get(field)) for field in required_btc_fields):
        return {}

    candidates = []
    for symbol in candidate_pool:
        indicators = indicators_map.get(symbol)
        if indicators is None:
            continue

        required_fields = ["sma20", "sma60", "sma200", "roc20", "roc60", "roc120", "vol20"]
        if any(is_missing(indicators.get(field)) for field in required_fields):
            continue

        raw_price = prices.get(symbol)
        if is_missing(raw_price):
            continue
        price = safe_float(raw_price)
        vol20 = safe_float(indicators["vol20"])
        if (
            price <= safe_float(indicators["sma20"])
            or price <= safe_float(indicators["sma60"])
            or price <= safe_float(indicators["sma200"])
            or vol20 <= 0
        ):
            continue

        rel_20 = safe_float(indicators["roc20"]) - safe_float(btc_snapshot["btc_roc20"])
        rel_60 = safe_float(indicators["roc60"]) - safe_float(btc_snapshot["btc_roc60"])
        rel_120 = safe_float(indicators["roc120"]) - safe_float(btc_snapshot["btc_roc120"])
        abs_momentum = (
            0.5 * safe_float(indicators["roc20"])
            + 0.3 * safe_float(indicators["roc60"])
            + 0.2 * safe_float(indicators["roc120"])
        )
        relative_score = (0.5 * rel_20 + 0.3 * rel_60 + 0.2 * rel_120) / vol20

        if relative_score > 0 and abs_momentum > 0:
            candidates.append(
                {
                    "symbol": symbol,
                    "relative_score": relative_score,
                    "vol20": vol20,
                    "abs_momentum": abs_momentum,
                }
            )

    candidates.sort(key=lambda item: item["relative_score"], reverse=True)
    selected = candidates[: max(1, int(top_n))]
    if not selected:
        return {}

    if weight_mode == "equal":
        weight = 1.0 / len(selected)
        return {
            item["symbol"]: {
                "weight": weight,
                "relative_score": item["relative_score"],
                "abs_momentum": item["abs_momentum"],
            }
            for item in selected
        }

    inv_vol_sum = sum(1.0 / item["vol20"] for item in selected)
    if inv_vol_sum <= 0:
        return {}
    return {
        item["symbol"]: {
            "weight": (1.0 / item["vol20"]) / inv_vol_sum,
            "relative_score": item["relative_score"],
            "abs_momentum": item["abs_momentum"],
        }
        for item in selected
    }


def allocate_trend_buy_budget(selected_candidates, buyable_symbols, total_budget):
    if total_budget <= 0 or not buyable_symbols:
        return {}

    raw_weights = {
        symbol: max(0.0, safe_float(selected_candidates.get(symbol, {}).get("weight", 0.0)))
        for symbol in buyable_symbols
    }
    weight_sum = sum(raw_weights.values())
    if weight_sum <= 0:
        return {}

    return {
        symbol: float(total_budget) * raw_weights[symbol] / weight_sum
        for symbol in buyable_symbols
    }

"""Tests for the enhanced crypto_btc_dca strategy module."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect
import math
import unittest


from crypto_strategies.manifests import crypto_btc_dca_manifest
from crypto_strategies.strategies.crypto_btc_dca import (
    PROFILE_NAME,
    CRYPTO_DOMAIN,
    SIGNAL_SOURCE,
    STATUS_ICON,
    DEFAULT_SIGNAL_SYMBOL,
    DEFAULT_PARKING_SYMBOL,
    Ahr999Spec,
    BtcDcaPlanConfig,
    DrawdownTierSpec,
    ExecutionWindowSpec,
    ZScoreExitSpec,
    build_rebalance_plan,
    build_target_weights,
    compute_signals,
    extract_managed_symbols,
    get_dynamic_btc_target_ratio,
    _determine_cycle_multiplier,
    _determine_multiplier,
    _is_in_execution_window,
)


# ---------------------------------------------------------------------------
# Portfolio stub
# ---------------------------------------------------------------------------


class FakePortfolio:
    def __init__(
        self,
        total_equity: float = 50000.0,
        buying_power: float = 5000.0,
        btc_units: float = 0.0,
        btc_price: float = 60000.0,
        metadata: dict | None = None,
    ):
        self.total_equity = total_equity
        self.buying_power = buying_power
        self.cash_balance = buying_power
        self.positions = (
            [type("Pos", (), {"symbol": "BTCUSDT", "quantity": btc_units,
                               "market_value": btc_units * btc_price})()]
            if btc_units > 0
            else ()
        )
        self.metadata = metadata or {}


def _zh_translator(key: str, **kwargs) -> str:
    return key


# ---------------------------------------------------------------------------
# Core module tests
# ---------------------------------------------------------------------------


class CryptoBtcDcaModuleTest(unittest.TestCase):
    def test_profile_name(self) -> None:
        self.assertEqual(PROFILE_NAME, "crypto_btc_dca")

    def test_domain(self) -> None:
        self.assertEqual(CRYPTO_DOMAIN, "crypto")

    def test_signal_source(self) -> None:
        self.assertEqual(SIGNAL_SOURCE, "derived_indicators+portfolio_snapshot")

    def test_status_icon(self) -> None:
        self.assertEqual(STATUS_ICON, "₿")

    def test_default_signal_symbol(self) -> None:
        self.assertEqual(DEFAULT_SIGNAL_SYMBOL, "BTCUSDT")

    def test_default_parking_symbol(self) -> None:
        self.assertEqual(DEFAULT_PARKING_SYMBOL, "USDT")


# ---------------------------------------------------------------------------
# Dynamic BTC target ratio
# ---------------------------------------------------------------------------


class DynamicBtcTargetRatioTest(unittest.TestCase):
    def test_at_minimum_equity(self) -> None:
        ratio = get_dynamic_btc_target_ratio(0.0)
        expected = 0.14 + 0.16 * math.log1p(1.0 / 10000.0)
        self.assertAlmostEqual(ratio, expected)

    def test_at_maximum_equity(self) -> None:
        ratio = get_dynamic_btc_target_ratio(1_000_000_000.0)
        self.assertAlmostEqual(ratio, 0.65)

    def test_mid_equity(self) -> None:
        ratio = get_dynamic_btc_target_ratio(10_000.0)
        expected = 0.14 + 0.16 * math.log1p(1.0)
        self.assertAlmostEqual(ratio, expected, places=6)

    def test_negative_equity(self) -> None:
        ratio = get_dynamic_btc_target_ratio(-1.0)
        expected = 0.14 + 0.16 * math.log1p(1.0 / 10000.0)
        self.assertAlmostEqual(ratio, expected)

    def test_monotonic(self) -> None:
        ratios = [get_dynamic_btc_target_ratio(e) for e in [0, 1_000, 10_000, 100_000, 1_000_000]]
        for prev, curr in zip(ratios, ratios[1:]):
            self.assertGreaterEqual(curr, prev)


# ---------------------------------------------------------------------------
# Execution window
# ---------------------------------------------------------------------------


class ExecutionWindowTest(unittest.TestCase):
    def test_monthly_day25_in_window(self) -> None:
        in_window, text = _is_in_execution_window(
            "2026-05-26", cadence="monthly", monthly_day=25,
            monthly_window_calendar_days=5, weekly_day=4,
            weekly_window_calendar_days=4, quarterly_months=(1, 4, 7, 10),
            quarterly_day=25, quarterly_window_calendar_days=5,
        )
        self.assertTrue(in_window)
        self.assertIn("monthly_day=25", text)

    def test_monthly_day30_outside_window(self) -> None:
        in_window, _ = _is_in_execution_window(
            "2026-05-30", cadence="monthly", monthly_day=25,
            monthly_window_calendar_days=5, weekly_day=4,
            weekly_window_calendar_days=4, quarterly_months=(1, 4, 7, 10),
            quarterly_day=25, quarterly_window_calendar_days=5,
        )
        self.assertFalse(in_window)

    def test_monthly_day25_start_of_window(self) -> None:
        in_window, _ = _is_in_execution_window(
            "2026-05-25", cadence="monthly", monthly_day=25,
            monthly_window_calendar_days=5, weekly_day=4,
            weekly_window_calendar_days=4, quarterly_months=(1, 4, 7, 10),
            quarterly_day=25, quarterly_window_calendar_days=5,
        )
        self.assertTrue(in_window)

    def test_weekly_thursday_in_window(self) -> None:
        # 2026-05-28 is a Thursday (weekday=3)
        in_window, text = _is_in_execution_window(
            "2026-05-28", cadence="weekly", weekly_day=3,
            weekly_window_calendar_days=4, monthly_day=25,
            monthly_window_calendar_days=5, quarterly_months=(1, 4, 7, 10),
            quarterly_day=25, quarterly_window_calendar_days=5,
        )
        self.assertTrue(in_window)
        self.assertIn("weekly_day=3", text)

    def test_quarterly_in_window(self) -> None:
        in_window, text = _is_in_execution_window(
            "2026-04-26", cadence="quarterly", quarterly_months=(1, 4, 7, 10),
            quarterly_day=25, quarterly_window_calendar_days=5,
            monthly_day=25, monthly_window_calendar_days=5,
            weekly_day=4, weekly_window_calendar_days=4,
        )
        self.assertTrue(in_window)
        self.assertIn("quarterly_months=1,4,7,10", text)


# ---------------------------------------------------------------------------
# Multiplier logic
# ---------------------------------------------------------------------------


class MultiplierTest(unittest.TestCase):
    def test_ahr999_bottom(self) -> None:
        mult, regime = _determine_cycle_multiplier(
            {"ahr999": 0.30},
            ahr999_bottom_threshold=0.45, ahr999_accumulation_threshold=0.80,
            ahr999_dca_threshold=1.20, ahr999_bottom_multiplier=3.0,
            ahr999_accumulation_multiplier=2.25, ahr999_dca_multiplier=1.50,
            ahr999_expensive_multiplier=0.0, base_multiplier=1.0,
        )
        self.assertEqual(mult, 3.0)
        self.assertEqual(regime, "ahr999_bottom")

    def test_ahr999_accumulation(self) -> None:
        mult, regime = _determine_cycle_multiplier(
            {"ahr999": 0.70},
            ahr999_bottom_threshold=0.45, ahr999_accumulation_threshold=0.80,
            ahr999_dca_threshold=1.20, ahr999_bottom_multiplier=3.0,
            ahr999_accumulation_multiplier=2.25, ahr999_dca_multiplier=1.50,
            ahr999_expensive_multiplier=0.0, base_multiplier=1.0,
        )
        self.assertEqual(mult, 2.25)
        self.assertEqual(regime, "ahr999_accumulation")

    def test_ahr999_expensive_skips(self) -> None:
        mult, regime = _determine_cycle_multiplier(
            {"ahr999": 1.50},
            ahr999_bottom_threshold=0.45, ahr999_accumulation_threshold=0.80,
            ahr999_dca_threshold=1.20, ahr999_bottom_multiplier=3.0,
            ahr999_accumulation_multiplier=2.25, ahr999_dca_multiplier=1.50,
            ahr999_expensive_multiplier=0.0, base_multiplier=1.0,
        )
        self.assertEqual(mult, 0.0)
        self.assertEqual(regime, "ahr999_expensive")

    def test_ahr999_missing_returns_base(self) -> None:
        mult, regime = _determine_cycle_multiplier(
            {},
            ahr999_bottom_threshold=0.45, ahr999_accumulation_threshold=0.80,
            ahr999_dca_threshold=1.20, ahr999_bottom_multiplier=3.0,
            ahr999_accumulation_multiplier=2.25, ahr999_dca_multiplier=1.50,
            ahr999_expensive_multiplier=0.0, base_multiplier=1.0,
        )
        self.assertEqual(mult, 1.0)
        self.assertEqual(regime, "normal")

    def test_drawdown_severe(self) -> None:
        mult, regime, _ = _determine_multiplier(
            {"drawdown_252d": 0.45, "sma200_gap": -0.30, "rsi14": None},
            mild_drawdown_threshold=0.12, deep_drawdown_threshold=0.25,
            severe_drawdown_threshold=0.40, mild_discount_gap=0.08,
            deep_discount_gap=0.18, expensive_gap=0.30, very_expensive_gap=0.60,
            shallow_drawdown_threshold=0.05, overbought_rsi=75.0,
            mild_pullback_multiplier=1.50, deep_pullback_multiplier=2.25,
            severe_pullback_multiplier=3.0, expensive_multiplier=1.0,
            very_expensive_multiplier=1.0, base_multiplier=1.0,
        )
        self.assertEqual(mult, 3.0)
        self.assertEqual(regime, "severe_pullback")


# ---------------------------------------------------------------------------
# build_rebalance_plan tests
# ---------------------------------------------------------------------------


class BuildRebalancePlanTest(unittest.TestCase):
    def test_ordinary_dca_defaults(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(),
            as_of="2026-05-26",
            smart_multiplier_enabled=False,
        )
        self.assertTrue(plan["actionable"])
        self.assertEqual(plan["regime"], "ordinary_dca")
        self.assertEqual(plan["multiplier"], 1.0)
        self.assertAlmostEqual(plan["planned_investment_usd"], 100.0)
        self.assertIn("BTCUSDT", plan["target_values"])

    def test_smart_multiplier_enabled_by_default(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(),
            as_of="2026-05-26",
        )
        self.assertTrue(plan["smart_multiplier_enabled"])

    def test_ahr999_bottom_with_indicator_data(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(buying_power=5000.0),
            as_of="2026-05-26",
            smart_multiplier_enabled=True,
            derived_indicators={
                "BTCUSDT": {
                    "ahr999": 0.29,
                    "mayer_multiple": 0.85,
                }
            },
        )
        self.assertTrue(plan["actionable"])
        self.assertEqual(plan["regime"], "ahr999_bottom")
        self.assertEqual(plan["multiplier"], 3.0)
        self.assertAlmostEqual(plan["requested_investment_usd"], 300.0)

    def test_ahr999_expensive_skips(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(buying_power=5000.0),
            as_of="2026-05-26",
            smart_multiplier_enabled=True,
            derived_indicators={
                "BTCUSDT": {
                    "ahr999": 1.50,
                }
            },
        )
        self.assertFalse(plan["actionable"])
        self.assertEqual(plan["skip_reason"], "valuation_too_expensive")
        self.assertEqual(plan["regime"], "ahr999_expensive")
        self.assertEqual(plan["multiplier"], 0.0)

    def test_skips_outside_execution_window(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(),
            as_of="2026-05-30",
        )
        self.assertFalse(plan["actionable"])
        self.assertEqual(plan["skip_reason"], "outside_execution_window")

    def test_skips_insufficient_cash(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(buying_power=2.0),
            as_of="2026-05-26",
        )
        self.assertFalse(plan["actionable"])
        self.assertEqual(plan["skip_reason"], "insufficient_cash")

    def test_zscore_exit_reduces_btc_exposure(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(buying_power=500.0, btc_price=60000.0, btc_units=1.0),
            as_of="2026-05-26",
            zscore_exit_enabled=True,
            zscore_exit_context={
                "plugin": "btc_zscore_exit",
                "canonical_route": "risk_reduced",
                "position_control": {
                    "final_route": "risk_reduced",
                    "target_allocations": {"BTCUSDT": 0.50, "USDT": 0.50},
                },
            },
        )
        self.assertTrue(plan["zscore_exit"]["applied"])
        self.assertEqual(plan["zscore_exit"]["route"], "risk_reduced")
        self.assertAlmostEqual(plan["zscore_exit"]["target_btc_exposure"], 0.50)

    def test_i18n_chinese_signal(self) -> None:
        plan = build_rebalance_plan(
            FakePortfolio(buying_power=2.0),
            as_of="2026-05-26",
            translator=_zh_translator,
        )
        self.assertIn("BTC", plan["signal_description"])
        self.assertIn("skip", plan["signal_description"])


# ---------------------------------------------------------------------------
# compute_signals tests
# ---------------------------------------------------------------------------


class ComputeSignalsTest(unittest.TestCase):
    def test_returns_btcusdt(self) -> None:
        prices = {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0}
        result = compute_signals(prices=prices, portfolio=None, total_equity=50000.0)
        self.assertIn("BTCUSDT", result["signals"])
        self.assertAlmostEqual(result["signals"]["BTCUSDT"]["target_weight"], 1.0)
        self.assertIn("btc_target_ratio", result)
        self.assertEqual(result["profile"], PROFILE_NAME)

    def test_with_state(self) -> None:
        prices = {"BTCUSDT": 60000.0}
        result = compute_signals(
            prices=prices, portfolio=None, total_equity=10000.0, state={"k": "v"},
        )
        self.assertIn("BTCUSDT", result["signals"])

    def test_with_derived_indicators_enables_smart_mode(self) -> None:
        prices = {"BTCUSDT": 60000.0}
        portfolio = FakePortfolio(buying_power=5000.0)
        result = compute_signals(
            prices=prices,
            portfolio=portfolio,
            total_equity=50000.0,
            derived_indicators={"BTCUSDT": {"ahr999": 0.30}},
            as_of="2026-05-26",
        )
        metadata = result.get("metadata", {})
        self.assertEqual(metadata.get("regime"), "ahr999_bottom")
        self.assertEqual(metadata.get("multiplier"), 3.0)
        self.assertTrue(metadata.get("smart_multiplier_enabled"))

    def test_btc_target_ratio_matches_function(self) -> None:
        equity = 75_000.0
        result = compute_signals(
            prices={"BTCUSDT": 60000.0}, portfolio=None, total_equity=equity,
        )
        expected = get_dynamic_btc_target_ratio(equity)
        self.assertAlmostEqual(result["btc_target_ratio"], expected)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PublicApiTest(unittest.TestCase):
    def test_build_target_weights_returns_btc(self) -> None:
        weights = build_target_weights(
            prices={"BTCUSDT": 60000.0},
            portfolio=None,
            total_equity=50000.0,
        )
        self.assertEqual(weights, {"BTCUSDT": 1.0})

    def test_extract_managed_symbols(self) -> None:
        self.assertEqual(extract_managed_symbols(), ("BTCUSDT",))


class BtcDcaPlanConfigTest(unittest.TestCase):
    def test_specs_and_plan_config_are_frozen_slotted_value_objects(self) -> None:
        instances = (
            ExecutionWindowSpec(),
            DrawdownTierSpec(),
            Ahr999Spec(),
            ZScoreExitSpec(),
            BtcDcaPlanConfig(),
        )
        for instance in instances:
            self.assertFalse(hasattr(instance, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            instances[0].cadence = "weekly"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            instances[1].mild_drawdown_threshold = 0.2  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            instances[2].bottom_threshold = 0.5  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            instances[3].enabled = False  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            instances[4].base_investment_usd = 200.0  # type: ignore[misc]

    def test_build_rebalance_plan_converged_signature_and_equivalence(self) -> None:
        params = list(inspect.signature(build_rebalance_plan).parameters)
        self.assertEqual(
            params,
            [
                "portfolio",
                "as_of",
                "config",
                "derived_indicators",
                "zscore_exit_context",
                "translator",
                "strict_buy_window",
                "legacy_kwargs",
            ],
        )
        flat_kwargs = {
            "base_investment_usd": 250.0,
            "max_investment_usd": 400.0,
            "cash_reserve_usd": 100.0,
            "cadence": "weekly",
            "weekly_day": 3,
            "weekly_window_calendar_days": 2,
            "ahr999_bottom_threshold": 0.50,
            "ahr999_bottom_multiplier": 2.0,
            "zscore_exit_enabled": True,
            "zscore_exit_risk_reduced_exposure": 0.40,
        }
        config = BtcDcaPlanConfig.from_kwargs(**flat_kwargs)
        self.assertEqual(config.execution_window.cadence, "weekly")
        self.assertEqual(config.execution_window.weekly_day, 3)
        self.assertEqual(config.ahr999.bottom_threshold, 0.50)
        self.assertEqual(config.ahr999.bottom_multiplier, 2.0)
        self.assertEqual(config.zscore_exit.risk_reduced_exposure, 0.40)

        portfolio = FakePortfolio(buying_power=5000.0, btc_price=60000.0, btc_units=0.5)
        indicators = {"BTCUSDT": {"close": 60000.0, "sma200": 50000.0, "ahr999": 0.40}}
        exit_ctx = {"plugin": "btc_zscore_exit", "canonical_route": "risk_reduced"}

        plan_from_config = build_rebalance_plan(
            portfolio,
            as_of="2026-05-28",
            config=config,
            derived_indicators=indicators,
            zscore_exit_context=exit_ctx,
        )
        plan_from_legacy = build_rebalance_plan(
            portfolio,
            as_of="2026-05-28",
            derived_indicators=indicators,
            zscore_exit_context=exit_ctx,
            **flat_kwargs,
        )
        self.assertEqual(
            {k: v for k, v in plan_from_config.items() if not (isinstance(v, float) and math.isnan(v))},
            {k: v for k, v in plan_from_legacy.items() if not (isinstance(v, float) and math.isnan(v))},
        )

        signals_from_config = compute_signals(
            prices={"BTCUSDT": 60000.0},
            portfolio=portfolio,
            total_equity=50000.0,
            config=config,
            derived_indicators=indicators,
            as_of="2026-05-28",
            zscore_exit_context=exit_ctx,
        )
        signals_from_legacy = compute_signals(
            prices={"BTCUSDT": 60000.0},
            portfolio=portfolio,
            total_equity=50000.0,
            derived_indicators=indicators,
            as_of="2026-05-28",
            zscore_exit_context=exit_ctx,
            **flat_kwargs,
        )
        self.assertEqual(
            signals_from_config["metadata"]["planned_investment_usd"],
            signals_from_legacy["metadata"]["planned_investment_usd"],
        )
        self.assertEqual(
            signals_from_config["metadata"]["target_values"],
            signals_from_legacy["metadata"]["target_values"],
        )

    def test_from_kwargs_covers_manifest_and_rejects_unknown_or_mixed_args(self) -> None:
        legacy_ratio_keys = {
            "target_ratio_min",
            "target_ratio_max",
            "ratio_base",
            "ratio_scale",
            "equity_normalizer",
        }
        manifest_plan_keys = set(crypto_btc_dca_manifest.default_config) - legacy_ratio_keys
        self.assertLessEqual(manifest_plan_keys, BtcDcaPlanConfig.supported_kwarg_names())

        with self.assertRaises(TypeError):
            BtcDcaPlanConfig.from_kwargs(unknown_field=123)
        with self.assertRaises(TypeError):
            build_rebalance_plan(FakePortfolio(), unknown_field=123)
        with self.assertRaises(TypeError):
            build_rebalance_plan(
                FakePortfolio(),
                config=BtcDcaPlanConfig(),
                base_investment_usd=200.0,
            )


if __name__ == "__main__":
    unittest.main()

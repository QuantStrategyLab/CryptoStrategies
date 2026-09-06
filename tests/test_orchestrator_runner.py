"""Tests for CryptoLivePoolBacktestRunner + BacktestOrchestrator integration."""

from __future__ import annotations

import math

import pandas as pd

import tempfile
import unittest
from datetime import date

from crypto_strategies.backtest.orchestrator_runner import (
    COMBO_DEFAULT_MIN_HISTORY_DAYS,
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    CryptoEquityComboBacktestRunner,
    CryptoLivePoolBacktestRunner,
    build_backtest_runner,
)
from crypto_strategies.strategies.crypto_equity_combo import PROFILE_NAME as CRYPTO_EQUITY_COMBO_PROFILE


class CryptoOrchestratorRunnerTests(unittest.TestCase):
    def test_supported_profile(self) -> None:
        self.assertIn(PROFILE_NAME, SUPPORTED_PROFILES)

    def test_supported_profile_includes_equity_combo(self) -> None:
        self.assertIn(CRYPTO_EQUITY_COMBO_PROFILE, SUPPORTED_PROFILES)

    def test_build_backtest_runner_dispatches_combo(self) -> None:
        runner = build_backtest_runner(CRYPTO_EQUITY_COMBO_PROFILE, synthetic_days=1600)
        self.assertIsInstance(runner, CryptoEquityComboBacktestRunner)

    def test_run_returns_backtest_result(self) -> None:
        runner = CryptoLivePoolBacktestRunner(synthetic_days=1600)
        result = runner.run(
            PROFILE_NAME,
            {"min_history_days": 120, "top_n": 2, "rebalance_every": 7},
            start_date=date(2023, 6, 1),
            end_date=date(2024, 6, 1),
        )
        self.assertEqual(result.strategy_profile, PROFILE_NAME)
        self.assertEqual(result.domain, "crypto")
        self.assertGreater(result.observation_count, 0)
        self.assertFalse(runner.last_daily_returns.empty)
        self.assertGreaterEqual(runner.last_daily_returns.index.min().date(), date(2023, 6, 1))
        self.assertLessEqual(runner.last_daily_returns.index.max().date(), date(2024, 6, 1))
        self.assertEqual(result.observation_count, len(runner.last_daily_returns))
        self.assertEqual(len(runner.run_return_history), 1)

    def test_walk_forward_produces_one_result_per_window(self) -> None:
        from pathlib import Path
        from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
        from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

        with tempfile.TemporaryDirectory() as tmp:
            store = PerformanceStore(local_root=Path(tmp))
            orchestrator = BacktestOrchestrator(store=store)
            orchestrator.register_runner("crypto", CryptoLivePoolBacktestRunner(synthetic_days=1600))
            windows = (
                (date(2023, 6, 1), date(2023, 12, 31)),
                (date(2024, 1, 1), date(2024, 6, 30)),
            )
            results = orchestrator.walk_forward(
                PROFILE_NAME,
                domain="crypto",
                params={"min_history_days": 120, "top_n": 2, "rebalance_every": 7},
                windows=windows,
            )
            self.assertEqual(len(results), 2)


class CryptoEquityComboBacktestRunnerTests(unittest.TestCase):
    def test_run_returns_backtest_result(self) -> None:
        runner = CryptoEquityComboBacktestRunner(synthetic_days=1600)
        result = runner.run(
            CRYPTO_EQUITY_COMBO_PROFILE,
            {"min_history_days": COMBO_DEFAULT_MIN_HISTORY_DAYS, "combo_mode": "dynamic"},
            start_date=date(2023, 6, 1),
            end_date=date(2024, 6, 1),
        )
        self.assertEqual(result.strategy_profile, CRYPTO_EQUITY_COMBO_PROFILE)
        self.assertEqual(result.domain, "crypto")
        self.assertGreater(result.observation_count, 0)
        self.assertFalse(runner.last_daily_returns.empty)
        self.assertGreaterEqual(runner.last_daily_returns.index.min().date(), date(2023, 6, 1))
        self.assertLessEqual(runner.last_daily_returns.index.max().date(), date(2024, 6, 1))
        self.assertEqual(result.observation_count, len(runner.last_daily_returns))
        self.assertEqual(len(runner.run_return_history), 1)

    def test_invalid_combo_mode_raises(self) -> None:
        runner = CryptoEquityComboBacktestRunner(synthetic_days=1600)
        with self.assertRaises(ValueError):
            runner.run(
                CRYPTO_EQUITY_COMBO_PROFILE,
                {"min_history_days": COMBO_DEFAULT_MIN_HISTORY_DAYS, "combo_mode": "invalid"},
            )

    def test_walk_forward_combo_profile(self) -> None:
        from pathlib import Path
        from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
        from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

        with tempfile.TemporaryDirectory() as tmp:
            store = PerformanceStore(local_root=Path(tmp))
            orchestrator = BacktestOrchestrator(store=store)
            orchestrator.register_runner(
                "crypto",
                CryptoEquityComboBacktestRunner(synthetic_days=1600),
            )
            windows = (
                (date(2023, 6, 1), date(2023, 12, 31)),
                (date(2024, 1, 1), date(2024, 6, 30)),
            )
            results = orchestrator.walk_forward(
                CRYPTO_EQUITY_COMBO_PROFILE,
                domain="crypto",
                params={"min_history_days": COMBO_DEFAULT_MIN_HISTORY_DAYS, "combo_mode": "dynamic"},
                windows=windows,
            )
            self.assertEqual(len(results), 2)
            self.assertTrue(all(item.strategy_profile == CRYPTO_EQUITY_COMBO_PROFILE for item in results))



class AccountingMetricsRegressionTests(unittest.TestCase):
    """QSL-20260906-006 / 007: initial NAV drawdown + fee-constrained share ledger."""

    def test_max_drawdown_includes_initial_nav(self) -> None:
        from crypto_strategies.backtest.live_pool_simulator import _performance_metrics

        cases = (
            ([-0.1], -0.1),
            ([-0.1, 0.0], -0.1),
            ([-0.1, 0.1], -0.1),
            ([0.1, -0.2], -0.2),
        )
        for returns, expected in cases:
            with self.subTest(returns=returns):
                metrics = _performance_metrics(pd.Series(returns, dtype=float))
                self.assertAlmostEqual(metrics["Max Drawdown"], expected)

    def test_cash_entry_fee_constrains_buyable_shares(self) -> None:
        from crypto_strategies.backtest.live_pool_simulator import run_live_pool_rotation_backtest

        panel = pd.DataFrame(
            [
                {"date": "2024-01-01", "symbol": "A", "in_universe": True, "open": 100.0, "final_score": 1.0},
                {"date": "2024-01-02", "symbol": "A", "in_universe": True, "open": 100.0, "final_score": 1.0},
                {"date": "2024-01-03", "symbol": "A", "in_universe": True, "open": 110.0, "final_score": 1.0},
            ]
        )
        panel["date"] = pd.to_datetime(panel["date"])
        panel = panel.set_index(["date", "symbol"])
        result = run_live_pool_rotation_backtest(
            panel, top_n=1, rebalance_every=1, fee_bps=100, slippage_bps=0.0
        )
        # Buyable notional is 1/1.01; +10% mark => 1.1/1.01 - 1.
        self.assertAlmostEqual(float(result.returns.iloc[0]), 1.1 / 1.01 - 1.0)
        self.assertAlmostEqual(float(result.trade_log.loc[0, "fee"]), 0.01 / 1.01)
        self.assertAlmostEqual(float(result.trade_log.loc[0, "turnover"]), 0.5 / 1.01)

    def test_no_trade_days_drift_without_self_financing(self) -> None:
        from crypto_strategies.backtest.live_pool_simulator import run_live_pool_rotation_backtest

        panel = pd.DataFrame(
            [
                {"date": "2024-01-01", "symbol": "A", "in_universe": True, "open": 100.0, "final_score": 1.0},
                {"date": "2024-01-01", "symbol": "B", "in_universe": True, "open": 100.0, "final_score": 0.0},
                {"date": "2024-01-02", "symbol": "A", "in_universe": True, "open": 100.0, "final_score": 1.0},
                {"date": "2024-01-02", "symbol": "B", "in_universe": True, "open": 100.0, "final_score": 0.0},
                {"date": "2024-01-03", "symbol": "A", "in_universe": True, "open": 200.0, "final_score": 1.0},
                {"date": "2024-01-03", "symbol": "B", "in_universe": True, "open": 100.0, "final_score": 0.0},
                {"date": "2024-01-04", "symbol": "A", "in_universe": True, "open": 100.0, "final_score": 1.0},
                {"date": "2024-01-04", "symbol": "B", "in_universe": True, "open": 100.0, "final_score": 0.0},
            ]
        )
        panel["date"] = pd.to_datetime(panel["date"])
        panel = panel.set_index(["date", "symbol"])
        result = run_live_pool_rotation_backtest(
            panel, top_n=2, rebalance_every=7, fee_bps=0.0
        )
        # Equal-weight: +0.5 then -1/3, terminal equity returns to 1.
        self.assertEqual(len(result.returns), 2)
        self.assertAlmostEqual(float(result.returns.iloc[0]), 0.5)
        self.assertAlmostEqual(float(result.returns.iloc[1]), -1.0 / 3.0)
        self.assertAlmostEqual(float((1.0 + result.returns).prod()), 1.0)


if __name__ == "__main__":
    unittest.main()

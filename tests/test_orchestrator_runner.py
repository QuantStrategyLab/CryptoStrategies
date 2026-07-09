"""Tests for CryptoLivePoolBacktestRunner + BacktestOrchestrator integration."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()

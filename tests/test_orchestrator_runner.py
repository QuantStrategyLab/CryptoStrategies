"""Tests for CryptoLivePoolBacktestRunner + BacktestOrchestrator integration."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from crypto_strategies.backtest.orchestrator_runner import (
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    CryptoLivePoolBacktestRunner,
)


class CryptoOrchestratorRunnerTests(unittest.TestCase):
    def test_supported_profile(self) -> None:
        self.assertIn(PROFILE_NAME, SUPPORTED_PROFILES)

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


if __name__ == "__main__":
    unittest.main()

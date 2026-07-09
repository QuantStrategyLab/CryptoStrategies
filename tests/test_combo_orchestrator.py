from __future__ import annotations

import unittest

import pandas as pd

from crypto_strategies.backtest.combo_simulator import (
    CryptoComboBacktestConfig,
    run_combo_backtest,
)
from crypto_strategies.backtest.orchestrator_research import run_combo_profile_backtest
from crypto_strategies.strategies.crypto_equity_combo import PROFILE_NAME as CRYPTO_EQUITY_COMBO_PROFILE


def _fixture_history(*, days: int = 400) -> pd.DataFrame:
    rows = []
    for day in pd.date_range("2022-01-01", periods=days, freq="D"):
        rows.append({"date": day, "symbol": "BTCUSDT", "close": 30000.0 + hash(day) % 1000})
        rows.append({"date": day, "symbol": "ETHUSDT", "close": 2000.0 + hash(day) % 100})
    return pd.DataFrame(rows)


class ComboSimulatorTests(unittest.TestCase):
    def test_run_combo_backtest_static_mode(self) -> None:
        result = run_combo_backtest(
            _fixture_history(),
            combo_config=CryptoComboBacktestConfig(combo_mode="static", min_history_days=260),
        )
        self.assertGreater(result.metrics["Trading Days"], 0)
        self.assertIn("Sharpe", result.metrics)

    def test_run_combo_backtest_dynamic_mode(self) -> None:
        result = run_combo_backtest(
            _fixture_history(),
            combo_config=CryptoComboBacktestConfig(combo_mode="dynamic", min_history_days=260),
        )
        self.assertGreater(result.metrics["Trading Days"], 0)


class ComboOrchestratorResearchTests(unittest.TestCase):
    def test_run_combo_profile_backtest_with_fixture_history(self) -> None:
        payload = run_combo_profile_backtest(
            CRYPTO_EQUITY_COMBO_PROFILE,
            market_history=_fixture_history(),
            params={"min_history_days": 260, "combo_mode": "static"},
        )
        self.assertEqual(payload["profile"], CRYPTO_EQUITY_COMBO_PROFILE)
        self.assertEqual(payload["source"], "CryptoEquityComboBacktestRunner")
        self.assertGreater(payload["metrics"]["days"], 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

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

    def test_flat_prices_with_daily_contributions_have_zero_return(self) -> None:
        dates = pd.date_range("2024-01-01", periods=365, freq="D")
        history = pd.DataFrame(
            [{"date": day, "symbol": symbol, "close": 100.0}
             for day in dates for symbol in ("BTCUSDT", "ETHUSDT")]
        )
        zero_alt_returns = pd.DataFrame(0.0, index=dates, columns=("ETH", "SOL", "AVAX", "MATIC", "DOT"))
        with patch("crypto_strategies.backtest.combo_simulator._simulate_alt_returns", return_value=zero_alt_returns):
            result = run_combo_backtest(
                history,
                combo_config=CryptoComboBacktestConfig(
                    combo_mode="static", min_history_days=260, dca_amount_usd=100.0
                ),
            )
        self.assertAlmostEqual(result.metrics["ending_equity"], 36_500.0)
        self.assertAlmostEqual(result.metrics["net_profit"], 0.0)
        self.assertEqual(result.metrics["CAGR"], 0.0)
        self.assertEqual(result.metrics["Sharpe"], 0.0)
        self.assertEqual(result.metrics["TWR_total_return"], 0.0)
        self.assertEqual(result.metrics["XIRR"], 0.0)
        self.assertEqual(result.cost_status, "not_modelled")
        self.assertEqual(result.accounting_status, "computed")

    def test_unused_combo_budget_stays_in_cash_and_overallocation_is_rejected(self) -> None:
        dates = pd.date_range("2024-01-01", periods=365, freq="D")
        history = pd.DataFrame(
            [{"date": day, "symbol": symbol, "close": 100.0}
             for day in dates for symbol in ("BTCUSDT", "ETHUSDT")]
        )
        zero_alt_returns = pd.DataFrame(0.0, index=dates, columns=("ETH", "SOL", "AVAX", "MATIC", "DOT"))
        with patch("crypto_strategies.backtest.combo_simulator._simulate_alt_returns", return_value=zero_alt_returns):
            result = run_combo_backtest(
                history,
                combo_config=CryptoComboBacktestConfig(
                    btc_weight=0.3, trend_weight=0.5, combo_mode="static", min_history_days=260
                ),
            )
            self.assertAlmostEqual(result.metrics["ending_equity"], 36_500.0)
            with self.assertRaisesRegex(ValueError, "sum to at most 1.0"):
                run_combo_backtest(
                    history,
                    combo_config=CryptoComboBacktestConfig(
                        btc_weight=0.6, trend_weight=0.5, min_history_days=260
                    ),
                )
            with self.assertRaisesRegex(ValueError, "dca_amount_usd must be finite and positive"):
                run_combo_backtest(
                    history,
                    combo_config=CryptoComboBacktestConfig(dca_amount_usd=float("nan"), min_history_days=260),
                )


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
        self.assertEqual(payload["cost_status"], "not_modelled")
        self.assertEqual(payload["simulation_model"], "synthetic_alt_proxy_not_strategy_replay")
        self.assertEqual(payload["accounting"]["flow_timing"], "end_of_day")
        self.assertIn("cumulative_contributions", payload["accounting"])
        self.assertIn("xirr_status", payload["accounting"])

    def test_direct_and_orchestrator_combo_returns_share_twr_accounting(self) -> None:
        from crypto_strategies.backtest.orchestrator_runner import CryptoEquityComboBacktestRunner

        history = _fixture_history()
        config = CryptoComboBacktestConfig(min_history_days=260, combo_mode="static")
        direct = run_combo_backtest(history, combo_config=config)
        runner = CryptoEquityComboBacktestRunner(market_history=history)
        runner.run(
            CRYPTO_EQUITY_COMBO_PROFILE,
            {"min_history_days": 260, "combo_mode": "static"},
        )
        pd.testing.assert_series_equal(runner.last_daily_returns, direct.returns)
        self.assertAlmostEqual(
            runner.last_accounting_metrics["TWR_total_return"],
            direct.metrics["TWR_total_return"],
        )

    def test_legacy_metrics_exclude_flat_asset_contributions(self) -> None:
        from scripts.research_crypto_combo_backtest import _compute_metrics

        dates = pd.date_range("2021-01-01", "2026-06-28", freq="D")
        flows = pd.Series(100.0, index=dates)
        equity = flows.cumsum()
        equity.attrs["external_flows"] = flows
        result = _compute_metrics(equity, "flat proxy")
        full = result["Full Period"]
        self.assertEqual(full["total_return"], 0.0)
        self.assertEqual(full["twr_total_return"], 0.0)
        self.assertEqual(full["annual_return"], 0.0)
        self.assertEqual(full["net_profit"], 0.0)
        self.assertEqual(full["xirr"], 0.0)
        self.assertEqual(full["cost_status"], "not_modelled")

    def test_legacy_backtest_labels_proxy_and_unmodelled_costs(self) -> None:
        from scripts.research_crypto_combo_backtest import run_backtest

        dates = pd.date_range("2021-01-01", periods=600, freq="D")
        prices = pd.DataFrame(
            {"btc_close": 100.0, "eth_close": 50.0},
            index=dates,
        )
        with patch("scripts.research_crypto_combo_backtest._simulate_alt_returns") as simulate:
            simulate.return_value = pd.DataFrame(
                0.0,
                index=dates[1:],
                columns=("ETH", "SOL", "AVAX", "MATIC", "DOT"),
            )
            result = run_backtest(prices, orchestrator=False)
        self.assertEqual(result["Static Combo"]["simulation_model"], "synthetic_alt_proxy_not_strategy_replay")
        self.assertEqual(result["Dynamic Combo"]["simulation_model"], "synthetic_alt_proxy_not_strategy_replay")
        self.assertTrue(all(item["cost_status"] == "not_modelled" for item in result.values()))

    def test_main_json_preserves_orchestrator_accounting_and_legacy_status(self) -> None:
        from scripts import research_crypto_combo_backtest as research

        prices = pd.DataFrame({"btc_close": [1.0], "eth_close": [1.0]}, index=pd.date_range("2024-01-01", periods=1))
        orchestrator_payload = {
            "profile": CRYPTO_EQUITY_COMBO_PROFILE,
            "metrics": {"total_return": 0.1},
            "accounting": {"xirr": 0.1, "net_profit": 10.0},
            "cost_status": "not_modelled",
            "simulation_model": "synthetic_alt_proxy_not_strategy_replay",
            "source": "CryptoEquityComboBacktestRunner",
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["research_crypto_combo_backtest.py"]),
            patch.object(research, "load_crypto_data", return_value=prices),
            patch.object(research, "run_backtest", return_value={"orchestrator": orchestrator_payload}),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            research.main()
        emitted = json.loads(stdout.getvalue())
        self.assertEqual(emitted["accounting"], orchestrator_payload["accounting"])
        self.assertEqual(emitted["cost_status"], "not_modelled")
        self.assertEqual(emitted["simulation_model"], "synthetic_alt_proxy_not_strategy_replay")

        legacy_payload = {
            name: {
                "metrics": {},
                "simulation_model": model,
                "cost_status": "not_modelled",
            }
            for name, model in (
                ("Pure BTC DCA", "historical_btc_dca_no_cost_model"),
                ("Static Combo", "synthetic_alt_proxy_not_strategy_replay"),
                ("Dynamic Combo", "synthetic_alt_proxy_not_strategy_replay"),
            )
        }
        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", ["research_crypto_combo_backtest.py", "--legacy", "--json-output"]),
            patch.object(research, "load_crypto_data", return_value=prices),
            patch.object(research, "run_backtest", return_value=legacy_payload),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            research.main()
        emitted = json.loads(stdout.getvalue())
        self.assertEqual(emitted["Static Combo"]["simulation_model"], "synthetic_alt_proxy_not_strategy_replay")
        self.assertEqual(emitted["Static Combo"]["cost_status"], "not_modelled")


if __name__ == "__main__":
    unittest.main()

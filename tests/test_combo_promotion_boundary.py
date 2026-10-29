from datetime import date
import pytest

from crypto_strategies.backtest.orchestrator_runner import CryptoEquityComboBacktestRunner
from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
from quant_platform_kit.strategy_lifecycle.contracts import PromotionCostModel, PurgedWalkForwardFold
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore


def test_synthetic_combo_cannot_enter_promotion_protocol(tmp_path):
    orchestrator = BacktestOrchestrator(store=PerformanceStore(local_root=tmp_path))
    orchestrator.register_runner('crypto', CryptoEquityComboBacktestRunner(synthetic_days=400))
    folds = [PurgedWalkForwardFold(date(2015,1,1), date(2015,12,31), date(2016,1,3), date(2016,6,30)),
             PurgedWalkForwardFold(date(2016,7,3), date(2017,6,30), date(2017,7,3), date(2017,12,31)),
             PurgedWalkForwardFold(date(2018,1,3), date(2018,12,31), date(2019,1,3), date(2019,6,30))]
    with pytest.raises(RuntimeError, match="runner_kind='real'"):
        orchestrator.run_promotion('crypto_equity_combo', domain='crypto', params={}, folds=folds,
            locked_oos_start=date(2019,7,3), locked_oos_end=date(2020,7,3), purge_days=1, embargo_days=1,
            source_revision='a'*40, cost_model=PromotionCostModel(model_id='synthetic', commission_bps=1,
                slippage_bps=2, market_impact_bps=0.5))

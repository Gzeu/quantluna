"""
QuantLuna — Tests: risk/dashboard_engine.py
Sprint 27  |  8 tests
"""
from __future__ import annotations

import math
import pytest

from risk.dashboard_engine import RiskDashboardEngine


@pytest.fixture
def engine():
    return RiskDashboardEngine(initial_capital=10_000.0, sharpe_window=5)


class TestRiskDashboardEngine:

    def test_initial_state(self, engine):
        snap = engine.snapshot()
        assert snap["equity_usd"]   == 10_000.0
        assert snap["pnl_usd"]      == 0.0
        assert snap["total_trades"] == 0
        assert snap["rolling_sharpe"] == 0.0
        assert snap["current_dd"]   == 0.0

    def test_record_win(self, engine):
        engine.record_trade(pair="BTC/ETH", pnl_usd=100.0, fees_usd=1.0, is_win=True)
        snap = engine.snapshot()
        assert snap["equity_usd"]   == pytest.approx(10_099.0)
        assert snap["total_trades"] == 1
        assert snap["win_rate"]     == 1.0
        assert snap["current_dd"]   == 0.0  # equity above peak

    def test_record_loss_drawdown(self, engine):
        engine.record_trade(pair="BTC/ETH", pnl_usd=-200.0, fees_usd=0.0, is_win=False)
        snap = engine.snapshot()
        assert snap["current_dd"] > 0
        assert snap["max_dd"] == snap["current_dd"]
        assert snap["win_rate"] == 0.0

    def test_rolling_sharpe_positive_returns(self, engine):
        for _ in range(5):
            engine.record_trade(pair="X/Y", pnl_usd=50.0, fees_usd=0.0, is_win=True)
        assert engine.rolling_sharpe > 0

    def test_rolling_sharpe_insufficient_data(self, engine):
        engine.record_trade(pair="X/Y", pnl_usd=10.0, fees_usd=0.0, is_win=True)
        assert engine.rolling_sharpe == 0.0  # < 2 data points

    def test_exposure_tracking(self, engine):
        engine.update_exposure("BTC/ETH", 5000.0)
        engine.update_exposure("SOL/BNB", 3000.0)
        assert engine.total_exposure_usd == pytest.approx(8000.0)
        assert engine.exposure_pct       == pytest.approx(0.8)

    def test_per_pair_stats(self, engine):
        engine.record_trade("BTC/ETH", pnl_usd=50.0, fees_usd=0.5, is_win=True)
        engine.record_trade("BTC/ETH", pnl_usd=-20.0, fees_usd=0.5, is_win=False)
        ps = engine.pair_snapshot("BTC/ETH")
        assert ps["trade_count"] == 2
        assert ps["win_rate"]    == pytest.approx(0.5)
        assert ps["total_pnl_usd"] == pytest.approx(30.0)

    def test_equity_curve_grows(self, engine):
        for i in range(3):
            engine.record_trade("X/Y", pnl_usd=10.0, fees_usd=0.0, is_win=True)
        curve = engine.equity_curve
        assert len(curve) == 4  # initial + 3 trades
        assert curve[-1]["equity"] > curve[0]["equity"]

"""
Integration test: full backtest on synthetic cointegrated pair
"""
import pytest
import numpy as np
import pandas as pd

from config.settings import QuantLunaConfig
from backtest.engine import BacktestEngine


def make_cointegrated(n=1000, beta=1.3, noise=0.03):
    np.random.seed(7)
    x = pd.Series(
        np.cumsum(np.random.randn(n) * 0.005) + 10,
        index=pd.date_range("2024-01-01", periods=n, freq="1h")
    )
    y = beta * x + np.random.randn(n) * noise
    y.index = x.index
    return y, x


class TestBacktestEngine:

    def test_backtest_runs_without_error(self):
        y, x = make_cointegrated()
        cfg = QuantLunaConfig()
        engine = BacktestEngine(cfg=cfg)
        result = engine.run(y, x)
        assert "metrics" in result
        assert "trades" in result
        assert "equity" in result

    def test_metrics_present(self):
        y, x = make_cointegrated()
        cfg = QuantLunaConfig()
        engine = BacktestEngine(cfg=cfg)
        result = engine.run(y, x)
        metrics = result["metrics"]
        for key in ["sharpe", "max_drawdown", "win_rate", "n_trades"]:
            assert key in metrics, f"Missing metric: {key}"

    def test_equity_curve_positive(self):
        y, x = make_cointegrated(n=1000, noise=0.01)
        cfg = QuantLunaConfig()
        engine = BacktestEngine(cfg=cfg)
        result = engine.run(y, x)
        final_equity = result["equity"][-1]
        initial_equity = result["equity"][0]
        # On clean synthetic data, should not lose everything
        assert final_equity > initial_equity * 0.5, f"Severe loss: {final_equity:.0f}"

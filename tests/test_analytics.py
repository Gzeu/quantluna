"""
tests/test_analytics.py  —  PerformanceAnalytics unit tests
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.analytics import PerformanceAnalytics


@pytest.fixture
def flat_equity():
    """Flat equity curve — all metrics near zero."""
    idx = pd.date_range("2025-01-01", periods=100, freq="1h", tz="UTC")
    return pd.Series(10_000.0, index=idx)


@pytest.fixture
def growing_equity():
    """Steadily growing equity — good Sharpe, low DD."""
    idx = pd.date_range("2025-01-01", periods=500, freq="1h", tz="UTC")
    v = 10_000 * np.cumprod(1 + np.random.default_rng(42).normal(0.0002, 0.001, 500))
    return pd.Series(v, index=idx)


@pytest.fixture
def mock_trades(sample_trades):
    return sample_trades


class TestPerformanceAnalytics:
    def test_returns_dict(self, growing_equity, mock_trades):
        m = PerformanceAnalytics.compute(growing_equity, mock_trades)
        assert isinstance(m, dict)

    def test_required_keys(self, growing_equity, mock_trades):
        m = PerformanceAnalytics.compute(growing_equity, mock_trades)
        for key in ["sharpe", "sortino", "calmar", "max_drawdown", "win_rate",
                    "n_trades", "profit_factor", "ann_return", "ann_vol"]:
            assert key in m, f"Key {key!r} missing from metrics"

    def test_flat_equity_returns_empty(self, flat_equity):
        # Flat equity — only 1 unique return value, too short after dropna
        m = PerformanceAnalytics.compute(flat_equity.iloc[:1], [])
        assert m == {}

    def test_max_drawdown_is_negative(self, growing_equity, mock_trades):
        m = PerformanceAnalytics.compute(growing_equity, mock_trades)
        assert m["max_drawdown"] <= 0

    def test_win_rate_between_0_and_1(self, growing_equity, mock_trades):
        m = PerformanceAnalytics.compute(growing_equity, mock_trades)
        assert 0.0 <= m["win_rate"] <= 1.0

    def test_no_trades_gives_zero_stats(self, growing_equity):
        m = PerformanceAnalytics.compute(growing_equity, [])
        assert m["n_trades"] == 0
        assert m["win_rate"] == 0.0

    def test_metrics_are_finite(self, growing_equity, mock_trades):
        m = PerformanceAnalytics.compute(growing_equity, mock_trades)
        for k, v in m.items():
            if isinstance(v, float):
                assert np.isfinite(v), f"Metric {k!r} is not finite: {v}"

"""
Unit tests for KalmanHedgeRatio
"""
import pytest
import numpy as np
import pandas as pd

from core.kalman_filter import KalmanHedgeRatio


def make_synthetic_pair(n=500, true_beta=1.5, noise=0.02):
    """Generate synthetic cointegrated pair."""
    np.random.seed(42)
    x = np.cumsum(np.random.randn(n) * 0.01) + 10
    y = true_beta * x + 5 + np.random.randn(n) * noise
    ts = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.Series(y, index=ts), pd.Series(x, index=ts)


class TestKalmanHedgeRatio:

    def test_init(self):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-2)
        assert kf.beta == 1.0
        assert not kf.is_warm

    def test_single_update(self):
        kf = KalmanHedgeRatio()
        state = kf.update(y=3000.0, x=60000.0)
        assert state.beta is not None
        assert state.kalman_gain_beta >= 0

    def test_convergence_to_true_beta(self):
        y, x = make_synthetic_pair(n=500, true_beta=1.5)
        kf = KalmanHedgeRatio(delta=1e-4)
        result = kf.fit(y, x)
        final_beta = result["beta"].iloc[-1]
        # Should converge to true beta within 10%
        assert abs(final_beta - 1.5) < 0.15, f"Beta {final_beta:.4f} far from 1.5"

    def test_warm_flag(self):
        kf = KalmanHedgeRatio()
        y, x = make_synthetic_pair(n=100)
        kf.fit(y, x)
        assert kf.is_warm

    def test_uncertainty_decreases(self):
        kf = KalmanHedgeRatio()
        y, x = make_synthetic_pair(n=200)
        result = kf.fit(y, x)
        early_unc = result["P_beta"].iloc[10]
        late_unc = result["P_beta"].iloc[-1]
        assert late_unc < early_unc, "Uncertainty should decrease as filter gains confidence"

    def test_reset(self):
        kf = KalmanHedgeRatio()
        y, x = make_synthetic_pair(n=100)
        kf.fit(y, x)
        kf.reset()
        assert not kf.is_warm
        assert len(kf._history) == 0

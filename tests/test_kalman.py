"""
Unit tests for KalmanHedgeRatio.

Uses log-price synthetic data (realistic scale for crypto pairs).
log(BTC) ~ 11, log(ETH) = beta * log(BTC) + noise
"""
import pytest
import numpy as np
import pandas as pd
from core.kalman_filter import KalmanHedgeRatio

RNG = np.random.default_rng(42)


def make_log_price_pair(
    n: int = 1000,
    true_beta: float = 0.85,
    alpha: float = 0.0,
    noise_std: float = 0.003,
):
    """
    Simulate log(BTC) as a random walk, log(ETH) = beta*log(BTC) + noise.
    Scale: log(60_000) ~= 11 — realistic for crypto log-prices.
    """
    x = np.log(60_000) + np.cumsum(RNG.standard_normal(n) * 0.002)
    y = true_beta * x + alpha + RNG.standard_normal(n) * noise_std
    ts = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.Series(y, index=ts), pd.Series(x, index=ts)


class TestKalmanHedgeRatio:
    def test_init(self):
        kf = KalmanHedgeRatio()
        assert kf.delta > 0
        assert kf.observation_noise > 0  # public alias
        assert kf.R > 0                  # internal shorthand
        assert kf.warm_up > 0

    def test_single_update(self):
        kf = KalmanHedgeRatio()
        y, x = make_log_price_pair(n=2)
        state = kf.update(float(y.iloc[0]), float(x.iloc[0]))
        assert hasattr(state, "beta")
        assert hasattr(state, "kalman_gain")       # property alias
        assert hasattr(state, "kalman_gain_beta")  # direct field
        assert np.isfinite(state.beta)
        assert np.isfinite(state.kalman_gain)

    def test_convergence_to_true_beta(self):
        """After 1000 observations, beta must be within 5% of true value."""
        true_beta = 0.85
        y, x = make_log_price_pair(n=1000, true_beta=true_beta, noise_std=0.003)
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3)
        result = kf.fit(y, x)
        final_beta = result["beta"].iloc[-1]
        tol = true_beta * 0.05
        assert abs(final_beta - true_beta) < tol, (
            f"Beta {final_beta:.4f} not within 5% of true {true_beta}"
        )

    def test_warm_flag(self):
        """is_warm must be False before warm_up bars, True after."""
        kf = KalmanHedgeRatio(warm_up=50)
        y, x = make_log_price_pair(n=200)
        for i in range(49):
            state = kf.update(float(y.iloc[i]), float(x.iloc[i]))
            assert not state.is_warm, f"Should not be warm at bar {i}"
        state = kf.update(float(y.iloc[49]), float(x.iloc[49]))
        assert state.is_warm, "Should be warm after 50 updates"

    def test_uncertainty_decreases_early(self):
        """
        Posterior variance P_beta should decrease from bar 5 to bar 50
        (before process-noise injection starts to dominate in steady-state).
        Very small delta so Q is tiny relative to initial cov.
        """
        kf = KalmanHedgeRatio(delta=1e-5, observation_noise=1e-3)
        y, x = make_log_price_pair(n=200)
        result = kf.fit(y, x)
        p_bar5  = result["P_beta"].iloc[5]
        p_bar50 = result["P_beta"].iloc[50]
        assert p_bar50 < p_bar5, (
            f"P_beta should drop from bar5={p_bar5:.6f} to bar50={p_bar50:.6f}"
        )

    def test_reset(self):
        kf = KalmanHedgeRatio()
        y, x = make_log_price_pair(n=50)
        kf.fit(y, x)
        kf.reset()
        assert not kf._is_warm
        assert kf._n_updates == 0
        assert len(kf._history) == 0

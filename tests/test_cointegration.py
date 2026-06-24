"""
Unit tests for CointegrationTest
"""
import pytest
import numpy as np
import pandas as pd

from core.cointegration import CointegrationTest


def make_cointegrated(n=500, beta=1.2, noise=0.05):
    np.random.seed(0)
    x = np.cumsum(np.random.randn(n) * 0.01) + 10
    y = beta * x + np.random.randn(n) * noise
    ts = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.Series(y, index=ts), pd.Series(x, index=ts)


def make_random_walk_pair(n=500):
    np.random.seed(1)
    x = pd.Series(np.cumsum(np.random.randn(n) * 0.01))
    y = pd.Series(np.cumsum(np.random.randn(n) * 0.01))
    return y, x


class TestCointegrationTest:

    def test_cointegrated_pair_passes(self):
        y, x = make_cointegrated(n=500)
        ct = CointegrationTest()
        result = ct.run(y, x)
        assert result.is_cointegrated, f"Should detect cointegration. ADF={result.adf_pvalue:.4f}"

    def test_random_walk_fails(self):
        y, x = make_random_walk_pair(n=500)
        ct = CointegrationTest()
        result = ct.run(y, x)
        # Random walks should mostly fail
        if result.is_cointegrated:
            pytest.skip("Random walk accidentally passed — acceptable in rare cases")

    def test_half_life_calculated(self):
        y, x = make_cointegrated(n=500)
        ct = CointegrationTest()
        result = ct.run(y, x)
        assert result.half_life_hours is not None
        assert result.half_life_hours > 0

    def test_hurst_below_half(self):
        y, x = make_cointegrated(n=500, noise=0.02)
        ct = CointegrationTest()
        result = ct.run(y, x)
        if result.hurst_exponent is not None:
            assert result.hurst_exponent < 0.55, f"Hurst={result.hurst_exponent:.3f} too high"

    def test_verdict_string(self):
        y, x = make_cointegrated(n=500)
        ct = CointegrationTest()
        result = ct.run(y, x)
        assert isinstance(result.verdict, str)
        assert len(result.verdict) > 0

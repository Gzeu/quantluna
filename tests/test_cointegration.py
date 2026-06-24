"""
Unit tests for CointegrationTest.

Uses AR(1)-driven spread fixtures for controlled half-life.
rho=0.95 → theoretical HL = -log(2)/log(0.95) ≈ 13.5 bars.
"""
import pytest
import numpy as np
import pandas as pd
from core.cointegration import CointegrationTest

RNG = np.random.default_rng(42)


def make_ar1_spread_pair(
    n: int = 1000,
    rho: float = 0.95,
    beta: float = 1.2,
    noise_std: float = 0.08,
):
    """
    Build a cointegrated pair where the spread follows AR(1) with coefficient rho.
    Theoretical half-life = -log(2) / log(rho) bars.
    Uses log-price scale for realistic crypto values.
    """
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(rho * spread[-1] + RNG.standard_normal() * noise_std)
    spread = np.array(spread)
    x = np.log(60_000) + np.cumsum(RNG.standard_normal(n) * 0.001)
    y = beta * x + spread
    return pd.Series(y), pd.Series(x)


def make_random_walk_pair(n: int = 600):
    """Two independent random walks — should NOT be cointegrated."""
    x = np.cumsum(RNG.standard_normal(n))
    y = np.cumsum(RNG.standard_normal(n))
    return pd.Series(y), pd.Series(x)


class TestCointegrationTest:
    def test_cointegrated_pair_passes(self):
        """AR(1) spread with rho=0.95 should be detected as cointegrated."""
        y, x = make_ar1_spread_pair(n=1000, rho=0.95, noise_std=0.08)
        ct = CointegrationTest(min_half_life=5.0, max_half_life=500.0)
        result = ct.run(y, x, freq_hours=1.0)
        assert result.is_cointegrated, (
            f"Should detect cointegration. "
            f"ADF p={result.adf_pvalue:.4f}, "
            f"HL={result.half_life_hours:.2f}h, "
            f"verdict={result.verdict}"
        )

    def test_random_walk_fails(self):
        """Two independent random walks must NOT pass cointegration."""
        y, x = make_random_walk_pair(n=600)
        ct = CointegrationTest()
        result = ct.run(y, x)
        assert not result.is_cointegrated, "Independent RWs must not be cointegrated"

    def test_half_life_calculated(self):
        """Half-life must be positive and finite for a cointegrated pair."""
        y, x = make_ar1_spread_pair(n=1000, rho=0.90)
        ct = CointegrationTest(min_half_life=1.0, max_half_life=500.0)
        result = ct.run(y, x, freq_hours=1.0)
        assert result.half_life_hours > 0
        assert np.isfinite(result.half_life_hours)

    def test_half_life_in_correct_range(self):
        """
        rho=0.95 => theoretical HL ~13.5 bars.
        Accept HL in [5, 50] given estimation noise at n=1000.
        """
        y, x = make_ar1_spread_pair(n=1000, rho=0.95)
        ct = CointegrationTest(min_half_life=5.0, max_half_life=500.0)
        result = ct.run(y, x, freq_hours=1.0)
        assert 5 < result.half_life_hours < 50, (
            f"HL={result.half_life_hours:.2f}h outside expected [5, 50]"
        )

    def test_hurst_below_half(self):
        y, x = make_ar1_spread_pair(n=1000, rho=0.90)
        ct = CointegrationTest(min_half_life=1.0, max_half_life=500.0)
        result = ct.run(y, x, freq_hours=1.0)
        assert result.hurst_exponent < 0.55, (
            f"Hurst={result.hurst_exponent:.3f} should be < 0.55 for mean-reverting spread"
        )

    def test_verdict_string(self):
        y, x = make_ar1_spread_pair(n=1000, rho=0.95, noise_std=0.08)
        ct = CointegrationTest(min_half_life=5.0, max_half_life=500.0)
        result = ct.run(y, x, freq_hours=1.0)
        assert isinstance(result.verdict, str)
        assert len(result.verdict) > 0

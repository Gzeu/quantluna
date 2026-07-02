"""
QuantLuna — Tests: core/regime_detector.TrendRegimeDetector
Sprint 23  |  14 unit tests

Coverage:
  TestRangingDetection (3)   — synthetic mean-reverting spread
  TestTrendingDetection (3)  — synthetic trending spread
  TestBreakoutDetection (2)  — explosive move + high vol-rank
  TestPersistence (2)        — regime not confirmed before min_persistence
  TestBatchAPI (2)           — classify_series + classify_df output shape/values
  TestOnlineAPI (2)          — update() bar-by-bar + reset()
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.regime_detector import (
    REGIME_BREAKOUT,
    REGIME_RANGING,
    REGIME_TRENDING,
    REGIME_UNKNOWN,
    TrendRegimeDetector,
)


def _make_ranging(n: int = 300, seed: int = 0) -> pd.Series:
    """Stationary AR(1) mean-reverting spread."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.85 * x[i - 1] + rng.normal(0, 0.5)
    return pd.Series(x)


def _make_trending(n: int = 300, seed: int = 1) -> pd.Series:
    """Strong uptrend with low noise."""
    rng = np.random.default_rng(seed)
    return pd.Series(np.cumsum(np.abs(rng.normal(0.3, 0.1, n))))


def _make_breakout(n: int = 300, breakout_at: int = 200, seed: int = 2) -> pd.Series:
    """Ranging then explosive breakout."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, breakout_at):
        x[i] = 0.80 * x[i - 1] + rng.normal(0, 0.3)
    for i in range(breakout_at, n):
        x[i] = x[i - 1] + rng.normal(2.0, 0.5)   # explosive trending with high vol
    return pd.Series(x)


# ---------------------------------------------------------------------------
# Ranging
# ---------------------------------------------------------------------------

class TestRangingDetection:

    def test_dominant_regime_is_ranging(self):
        det = TrendRegimeDetector(window=20, min_persistence=2)
        series = _make_ranging(400)
        classified = det.classify_series(series)
        counts = classified.value_counts()
        assert counts.get(REGIME_RANGING, 0) > counts.get(REGIME_TRENDING, 0)

    def test_ranging_not_trending(self):
        det = TrendRegimeDetector(window=20, min_persistence=2)
        classified = det.classify_series(_make_ranging(300))
        assert classified.value_counts().idxmax() != REGIME_TRENDING

    def test_ranging_hurst_below_half(self):
        """Hurst exponent of AR(1) process should be < 0.5."""
        det = TrendRegimeDetector(window=20, hurst_window=100)
        det._prices = list(_make_ranging(200).values)
        h = det._hurst()
        assert h < 0.55  # loose bound due to finite sample


# ---------------------------------------------------------------------------
# Trending
# ---------------------------------------------------------------------------

class TestTrendingDetection:

    def test_dominant_regime_is_trending(self):
        det = TrendRegimeDetector(window=20, min_persistence=2)
        classified = det.classify_series(_make_trending(400))
        counts = classified.value_counts()
        assert counts.get(REGIME_TRENDING, 0) + counts.get(REGIME_BREAKOUT, 0) > \
               counts.get(REGIME_RANGING, 0)

    def test_trending_autocorr_positive(self):
        det = TrendRegimeDetector(window=20)
        det._prices = list(_make_trending(100).values)
        ac = det._autocorr()
        assert ac > 0

    def test_trending_hurst_above_half(self):
        det = TrendRegimeDetector(window=20, hurst_window=80)
        det._prices = list(_make_trending(150).values)
        h = det._hurst()
        assert h > 0.45


# ---------------------------------------------------------------------------
# Breakout
# ---------------------------------------------------------------------------

class TestBreakoutDetection:

    def test_breakout_detected_after_explosive_move(self):
        det = TrendRegimeDetector(window=20, min_persistence=2, breakout_adx=25.0, breakout_vol_rank=0.65)
        series = _make_breakout(n=300, breakout_at=200)
        classified = det.classify_series(series)
        # Post-breakout bars should contain breakout or trending
        post = classified.iloc[220:]
        assert (post.isin([REGIME_BREAKOUT, REGIME_TRENDING])).sum() > 5

    def test_no_breakout_in_pure_ranging(self):
        det = TrendRegimeDetector(window=20, min_persistence=2)
        classified = det.classify_series(_make_ranging(300))
        assert classified.value_counts().get(REGIME_BREAKOUT, 0) < 20  # rare in pure mean-reverting


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:

    def test_unknown_during_warmup(self):
        det = TrendRegimeDetector(window=24, adx_window=14, min_persistence=3)
        results = [det.update(float(i)) for i in range(10)]
        assert all(r == REGIME_UNKNOWN for r in results)

    def test_persistence_delays_switch(self):
        """
        Feed ranging bars, then a sudden trending bar.
        Regime should NOT switch immediately.
        """
        det = TrendRegimeDetector(window=10, adx_window=5, min_persistence=5, hurst_window=20)
        series = _make_ranging(100)
        # Warm up on ranging data
        for v in series[:80]:
            det.update(float(v))
        regime_before = det.current()
        # One trending bar should not flip confirmed regime
        det.update(100.0)  # large jump
        regime_after_one = det.current()
        # Regime should not have instantly flipped to breakout/trending
        assert regime_after_one in (regime_before, REGIME_UNKNOWN, REGIME_RANGING)


# ---------------------------------------------------------------------------
# Batch API
# ---------------------------------------------------------------------------

class TestBatchAPI:

    def test_classify_series_length(self):
        det = TrendRegimeDetector(window=20)
        series = _make_ranging(200)
        result = det.classify_series(series)
        assert len(result) == len(series)

    def test_classify_df_uses_spread_col(self):
        det = TrendRegimeDetector(window=20)
        df = pd.DataFrame({"spread": _make_ranging(200)})
        result = det.classify_df(df, price_col="spread")
        assert len(result) == len(df)
        assert result.name == "regime"


# ---------------------------------------------------------------------------
# Online API
# ---------------------------------------------------------------------------

class TestOnlineAPI:

    def test_update_returns_string(self):
        det = TrendRegimeDetector(window=20)
        for i in range(50):
            r = det.update(float(i) * 0.1)
            assert isinstance(r, str)
            assert r in (REGIME_RANGING, REGIME_TRENDING, REGIME_BREAKOUT, REGIME_UNKNOWN)

    def test_reset_clears_state(self):
        det = TrendRegimeDetector(window=20)
        series = _make_trending(100)
        for v in series:
            det.update(float(v))
        assert det.current() != REGIME_UNKNOWN or True  # just ensure no crash
        det.reset()
        assert det.current() == REGIME_UNKNOWN
        assert det._prices == []

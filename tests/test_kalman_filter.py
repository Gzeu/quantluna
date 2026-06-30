"""
tests/test_kalman_filter.py  —  KalmanHedgeRatio unit tests

Covers:
  - Single update mechanics
  - Warm-up threshold
  - Batch fit()
  - FIX: fit() stateful bug — second fit() must reset state
  - FIX: warmup guard when series shorter than warm_up
  - x=0 guard
  - delta setter recomputes Q
  - reset()
  - history deque
  - Joseph form covariance stability
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.kalman_filter import KalmanHedgeRatio, KalmanState


class TestKalmanUpdate:
    def test_single_update_returns_state(self):
        kf = KalmanHedgeRatio()
        s = kf.update(y=100.0, x=50.0)
        assert isinstance(s, KalmanState)
        assert np.isfinite(s.beta)
        assert np.isfinite(s.alpha)

    def test_x_zero_guard_returns_last_state(self):
        kf = KalmanHedgeRatio()
        kf.update(100.0, 50.0)  # set a known state first
        prev_beta = kf.beta
        s = kf.update(100.0, 0.0)  # x=0 — must return gracefully
        assert s.beta == prev_beta  # state unchanged
        assert s.kalman_gain_beta == 0.0

    def test_x_near_zero_guard(self):
        kf = KalmanHedgeRatio()
        s = kf.update(100.0, 1e-15)  # near-zero
        assert np.isfinite(s.beta)

    def test_warmup_flag_false_before_threshold(self):
        kf = KalmanHedgeRatio(warm_up=5)
        for i in range(4):
            s = kf.update(float(100 + i), float(50 + i))
        assert not s.is_warm

    def test_warmup_flag_true_at_threshold(self):
        kf = KalmanHedgeRatio(warm_up=5)
        for i in range(5):
            s = kf.update(float(100 + i), float(50 + i))
        assert s.is_warm

    def test_covariance_positive_definite(self):
        """Joseph form must keep P positive definite after many updates."""
        kf = KalmanHedgeRatio(delta=1e-4)
        rng = np.random.default_rng(7)
        for _ in range(500):
            x = float(rng.uniform(50, 150))
            y = 1.5 * x + 20 + float(rng.normal(0, 1))
            kf.update(y, x)
        eigvals = np.linalg.eigvalsh(kf.P)
        assert np.all(eigvals > 0), f"P not PD: eigvals={eigvals}"

    def test_n_updates_counter(self):
        kf = KalmanHedgeRatio()
        for _ in range(7):
            kf.update(100.0, 50.0)
        assert kf._n_updates == 7


class TestKalmanFit:
    def test_fit_returns_dataframe_with_correct_columns(self, synthetic_prices):
        y, x = synthetic_prices
        kf = KalmanHedgeRatio(warm_up=10)
        df = kf.fit(y, x)
        assert isinstance(df, pd.DataFrame)
        for col in ["beta", "alpha", "spread", "P_beta", "is_warm"]:
            assert col in df.columns, f"Column {col!r} missing"

    def test_fit_length_matches_input(self, synthetic_prices):
        y, x = synthetic_prices
        kf = KalmanHedgeRatio()
        df = kf.fit(y, x)
        assert len(df) == len(y)

    def test_fit_warmup_rows_are_false(self, synthetic_prices):
        y, x = synthetic_prices
        kf = KalmanHedgeRatio(warm_up=20)
        df = kf.fit(y, x)
        assert not df["is_warm"].iloc[0]
        assert df["is_warm"].iloc[-1]

    # -----------------------------------------------------------------------
    # BUG FIX TEST — Sprint 13
    # fit() must reset state before running — otherwise second fit() is stale
    # -----------------------------------------------------------------------
    def test_fit_stateful_bug_second_fit_resets(self, synthetic_prices):
        """
        Second call to fit() on same object must produce identical results
        to first call on a fresh object.
        FAILS before the fix, passes after.
        """
        y, x = synthetic_prices

        kf_fresh = KalmanHedgeRatio(delta=1e-4, warm_up=10)
        df_fresh = kf_fresh.fit(y, x)

        kf_reuse = KalmanHedgeRatio(delta=1e-4, warm_up=10)
        kf_reuse.fit(y[:100], x[:100])   # first fit on different data
        df_reuse = kf_reuse.fit(y, x)    # second fit — must reset state first

        pd.testing.assert_frame_equal(
            df_fresh[["beta", "alpha", "spread"]].round(8),
            df_reuse[["beta", "alpha", "spread"]].round(8),
            check_names=False,
        )

    def test_fit_mismatched_lengths_raises(self):
        kf = KalmanHedgeRatio()
        y = pd.Series([1.0, 2.0, 3.0])
        x = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError, match="same length"):
            kf.fit(y, x)

    # -----------------------------------------------------------------------
    # WARMUP GUARD TEST — Sprint 13
    # -----------------------------------------------------------------------
    def test_fit_short_series_warmup_warning(self, caplog):
        """Series shorter than warm_up should log a warning."""
        import logging
        kf = KalmanHedgeRatio(warm_up=50)
        y = pd.Series(np.random.default_rng(1).uniform(90, 110, 10))
        x = pd.Series(np.random.default_rng(2).uniform(50, 70, 10))
        with caplog.at_level(logging.WARNING):
            df = kf.fit(y, x)
        assert any("warm" in rec.message.lower() or "short" in rec.message.lower()
                   for rec in caplog.records), "Expected warmup warning not logged"
        assert not df["is_warm"].any()


class TestKalmanProperties:
    def test_delta_setter_recomputes_Q(self):
        kf = KalmanHedgeRatio(delta=1e-4)
        old_Q = kf.Q.copy()
        kf.delta = 5e-4
        assert not np.allclose(kf.Q, old_Q)
        # Q = delta/(1-delta)*I
        expected = (5e-4 / (1 - 5e-4)) * np.eye(2)
        np.testing.assert_allclose(kf.Q, expected, rtol=1e-10)

    def test_delta_out_of_range_raises(self):
        kf = KalmanHedgeRatio()
        with pytest.raises(ValueError):
            kf.delta = 1.5

    def test_reset_clears_state(self, synthetic_prices):
        y, x = synthetic_prices
        kf = KalmanHedgeRatio(warm_up=5)
        kf.fit(y, x)
        assert kf._n_updates > 0
        kf.reset()
        assert kf._n_updates == 0
        assert not kf._is_warm
        assert len(kf._history) == 0
        assert kf.beta == kf._beta0

    def test_history_bounded(self):
        """deque(maxlen=10_000) must not grow beyond limit."""
        kf = KalmanHedgeRatio(warm_up=5)
        rng = np.random.default_rng(0)
        for _ in range(10_100):
            kf.update(float(rng.uniform(90, 110)), float(rng.uniform(50, 70)))
        assert len(kf._history) <= 10_000

    def test_get_history_df(self, synthetic_prices):
        y, x = synthetic_prices
        kf = KalmanHedgeRatio(warm_up=5)
        kf.fit(y, x)
        hist = kf.get_history_df()
        assert isinstance(hist, pd.DataFrame)
        assert len(hist) == len(y)

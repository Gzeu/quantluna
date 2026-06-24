"""
Unit tests for SpreadEngine.

Covers:
- Batch fit output shape and columns
- Z-score statistical properties
- NaN handling during warm-up
- Live update_one incremental consistency
- Spread stationarity vs raw price non-stationarity
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.kalman_filter import KalmanHedgeRatio
from core.spread import SpreadEngine


def make_engine(delta: float = 1e-4, R: float = 1e-3,
                window: int = 100, warm: int = 30) -> SpreadEngine:
    kf = KalmanHedgeRatio(delta=delta, observation_noise=R, warm_up=warm)
    return SpreadEngine(kf, zscore_window=window, min_warm_periods=warm)


class TestSpreadEngineBatch:
    def test_output_columns(self, fitted_spread_df):
        required = {"beta", "alpha", "spread", "zscore",
                    "P_beta", "kalman_gain_beta", "is_warm"}
        assert required.issubset(set(fitted_spread_df.columns))

    def test_output_length(self, log_pair, fitted_spread_df):
        y, _ = log_pair
        assert len(fitted_spread_df) == len(y)

    def test_zscore_finite_after_warmup(self, fitted_spread_df):
        warm_df = fitted_spread_df[fitted_spread_df["is_warm"]]
        assert len(warm_df) > 0, "No warm bars found"
        non_nan = warm_df["zscore"].dropna()
        assert len(non_nan) > 0
        assert np.all(np.isfinite(non_nan.values))

    def test_zscore_mean_near_zero(self, fitted_spread_df):
        """Z-score should be approximately zero-mean by construction."""
        z = fitted_spread_df["zscore"].dropna()
        assert abs(z.mean()) < 0.3, f"Z-score mean {z.mean():.3f} too far from 0"

    def test_zscore_std_near_one(self, fitted_spread_df):
        """Z-score std should be close to 1 (rolling standardisation)."""
        z = fitted_spread_df["zscore"].dropna()
        assert 0.6 < z.std() < 1.5, f"Z-score std {z.std():.3f} outside [0.6, 1.5]"

    def test_warmup_bars_have_nan_zscore(self, log_pair):
        """Bars before warm-up should have NaN or zero z-score."""
        y, x = log_pair
        engine = make_engine(warm=50)
        df = engine.fit(y, x)
        cold_df = df[~df["is_warm"]]
        cold_z = cold_df["zscore"].iloc[:10]
        assert cold_z.isna().any() or (cold_z == 0).any()

    def test_spread_more_stationary_than_prices(self, log_pair):
        """
        Spread variance should be << variance of raw Y series.
        Confirms spread extraction is working.
        """
        y, x = log_pair
        engine = make_engine()
        df = engine.fit(y, x)
        spread_var = df["spread"].var()
        y_var = y.var()
        assert spread_var < y_var * 0.30, (
            f"Spread var {spread_var:.6f} not << Y var {y_var:.4f}"
        )

    def test_beta_in_reasonable_range(self, fitted_spread_df):
        """Kalman beta should stay positive and in a sane range."""
        beta = fitted_spread_df["beta"].dropna()
        assert (beta > 0).all(), "Beta should remain positive"
        assert beta.max() < 5.0, f"Beta spike to {beta.max():.2f}"
        assert beta.min() > 0.1, f"Beta collapsed to {beta.min():.4f}"


class TestSpreadEngineLive:
    def test_update_one_returns_required_keys(self, log_pair):
        y, x = log_pair
        engine = make_engine()
        result = engine.update_one(float(y.iloc[0]), float(x.iloc[0]))
        required = {"beta", "alpha", "spread", "zscore",
                    "P_beta", "kalman_gain", "uncertainty", "is_warm"}
        assert required.issubset(set(result.keys()))

    def test_update_one_finite_values(self, log_pair):
        y, x = log_pair
        engine = make_engine()
        for i in range(50):
            res = engine.update_one(float(y.iloc[i]), float(x.iloc[i]))
        assert np.isfinite(res["beta"])
        assert np.isfinite(res["zscore"])
        assert np.isfinite(res["uncertainty"])

    def test_live_warm_flag_transitions(self, log_pair):
        y, x = log_pair
        engine = make_engine(warm=20)
        warm_seen = False
        for i in range(30):
            res = engine.update_one(float(y.iloc[i]), float(x.iloc[i]))
            if res["is_warm"]:
                warm_seen = True
        assert warm_seen, "Filter should become warm by bar 30 with warm_up=20"

    def test_live_zscore_stabilises(self, log_pair):
        """Z-score magnitude should not blow up after many updates."""
        y, x = log_pair
        engine = make_engine()
        for i in range(len(y)):
            res = engine.update_one(float(y.iloc[i]), float(x.iloc[i]))
        assert np.isfinite(res["zscore"])
        assert abs(res["zscore"]) < 10.0, f"Z-score blew up to {res['zscore']:.2f}"

"""
Unit tests for SignalGenerator.

Covers:
- Batch signal generation correctness
- Cold-filter suppression (no signals before warm-up)
- High-uncertainty suppression
- Hard stop triggering
- Live generate_live() path
- Signal state machine (hold logic)
- Confidence range [0, 1]
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.settings import SignalConfig
from core.kalman_filter import KalmanHedgeRatio
from core.spread import SpreadEngine
from strategy.signal import Signal, SignalGenerator, TradeSignal


def make_signal_gen(
    zscore_entry: float = 2.0,
    zscore_exit: float = 0.5,
    zscore_stop: float = 3.5,
    delta: float = 1e-4,
    R: float = 1e-3,
    warm: int = 30,
) -> SignalGenerator:
    cfg = SignalConfig(
        zscore_entry=zscore_entry,
        zscore_exit=zscore_exit,
        zscore_stop=zscore_stop,
    )
    kf = KalmanHedgeRatio(delta=delta, observation_noise=R, warm_up=warm)
    engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=warm)
    return SignalGenerator(engine, cfg)


class TestSignalGeneratorBatch:
    def test_batch_returns_signal_column(self, fitted_spread_df, signal_cfg):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=30)
        engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=30)
        gen = SignalGenerator(engine, signal_cfg)
        result = gen.generate_batch(fitted_spread_df)
        assert "signal" in result.columns
        assert "confidence" in result.columns

    def test_no_entry_signal_before_warmup(self, fitted_spread_df, signal_cfg):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=30)
        engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=30)
        gen = SignalGenerator(engine, signal_cfg)
        result = gen.generate_batch(fitted_spread_df)
        cold_signals = result[~result["is_warm"]]["signal"]
        assert (cold_signals == int(Signal.EXIT)).all(), \
            "Got non-EXIT signal before warm-up"

    def test_long_signal_when_zscore_low(self, signal_cfg):
        """Force a LONG_SPREAD signal via crafted z-scores."""
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=5)
        engine = SpreadEngine(kf, zscore_window=20, min_warm_periods=5)
        gen = SignalGenerator(engine, signal_cfg)

        n = 50
        df = pd.DataFrame({
            "beta": [0.85] * n,
            "alpha": [0.0] * n,
            "spread": np.zeros(n),
            "spread_mean": np.zeros(n),
            "spread_std": np.ones(n),
            "is_warm": [True] * n,
            "P_beta": [0.01] * n,
            "kalman_gain_beta": [0.01] * n,
        })
        df["zscore"] = 0.0
        df.loc[df.index[-5:], "zscore"] = -2.5

        result = gen.generate_batch(df)
        long_signals = result[result["zscore"] < -signal_cfg.zscore_entry]["signal"]
        assert (long_signals == int(Signal.LONG_SPREAD)).any(), \
            "Expected LONG_SPREAD signal for z < -2.0"

    def test_short_signal_when_zscore_high(self, signal_cfg):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=5)
        engine = SpreadEngine(kf, zscore_window=20, min_warm_periods=5)
        gen = SignalGenerator(engine, signal_cfg)

        n = 50
        df = pd.DataFrame({
            "beta": [0.85] * n, "alpha": [0.0] * n,
            "spread": np.zeros(n), "spread_mean": np.zeros(n),
            "spread_std": np.ones(n), "is_warm": [True] * n,
            "P_beta": [0.01] * n, "kalman_gain_beta": [0.01] * n,
        })
        df["zscore"] = 0.0
        df.loc[df.index[-5:], "zscore"] = 2.5

        result = gen.generate_batch(df)
        short_signals = result[result["zscore"] > signal_cfg.zscore_entry]["signal"]
        assert (short_signals == int(Signal.SHORT_SPREAD)).any(), \
            "Expected SHORT_SPREAD signal for z > 2.0"

    def test_hard_stop_overrides_hold(self, signal_cfg):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=5)
        engine = SpreadEngine(kf, zscore_window=20, min_warm_periods=5)
        gen = SignalGenerator(engine, signal_cfg)

        n = 50
        df = pd.DataFrame({
            "beta": [0.85] * n, "alpha": [0.0] * n,
            "spread": np.zeros(n), "spread_mean": np.zeros(n),
            "spread_std": np.ones(n), "is_warm": [True] * n,
            "P_beta": [0.01] * n, "kalman_gain_beta": [0.01] * n,
        })
        df["zscore"] = 0.0
        df.loc[df.index[-3:], "zscore"] = 4.0

        result = gen.generate_batch(df)
        stop_signals = result[result["zscore"] >= signal_cfg.zscore_stop]["signal"]
        assert (stop_signals == int(Signal.EXIT)).all(), \
            "Hard stop must force EXIT"

    def test_high_uncertainty_suppresses_signal(self, signal_cfg):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=5)
        engine = SpreadEngine(kf, zscore_window=20, min_warm_periods=5)
        gen = SignalGenerator(engine, signal_cfg)

        n = 50
        df = pd.DataFrame({
            "beta": [0.85] * n, "alpha": [0.0] * n,
            "spread": np.zeros(n), "spread_mean": np.zeros(n),
            "spread_std": np.ones(n), "is_warm": [True] * n,
            "P_beta": [1.0] * n,
            "kalman_gain_beta": [0.05] * n,
        })
        df["zscore"] = -3.0

        result = gen.generate_batch(df)
        assert (result["signal"] == int(Signal.EXIT)).all(), \
            "High uncertainty (P_beta=1.0) must suppress all signals"

    def test_confidence_bounded(self, fitted_spread_df, signal_cfg):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=30)
        engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=30)
        gen = SignalGenerator(engine, signal_cfg)
        result = gen.generate_batch(fitted_spread_df)
        conf = result["confidence"]
        assert conf.min() >= 0.0
        assert conf.max() <= 1.0

    def test_signal_values_are_valid_enum(self, fitted_spread_df, signal_cfg):
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=30)
        engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=30)
        gen = SignalGenerator(engine, signal_cfg)
        result = gen.generate_batch(fitted_spread_df)
        valid = {int(Signal.LONG_SPREAD), int(Signal.SHORT_SPREAD), int(Signal.EXIT)}
        unique_signals = set(result["signal"].unique())
        assert unique_signals.issubset(valid), \
            f"Invalid signal values found: {unique_signals - valid}"


class TestSignalGeneratorLive:
    def test_live_cold_returns_exit(self, log_pair, signal_cfg):
        """Before warm-up, generate_live must return EXIT with reason."""
        y, x = log_pair
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=100)
        engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=100)
        gen = SignalGenerator(engine, signal_cfg)

        for i in range(5):
            sig = gen.generate_live(float(y.iloc[i]), float(x.iloc[i]))
        assert sig.signal == Signal.EXIT
        assert "warming" in sig.reason or "warm" in sig.reason

    def test_live_returns_trade_signal(self, log_pair, signal_cfg):
        y, x = log_pair
        kf = KalmanHedgeRatio(delta=1e-4, observation_noise=1e-3, warm_up=30)
        engine = SpreadEngine(kf, zscore_window=100, min_warm_periods=30)
        gen = SignalGenerator(engine, signal_cfg)

        sig = None
        for i in range(200):
            sig = gen.generate_live(float(y.iloc[i]), float(x.iloc[i]))

        assert isinstance(sig, TradeSignal)
        assert sig.signal in list(Signal)
        assert 0.0 <= sig.confidence <= 1.0
        assert np.isfinite(sig.zscore)
        assert np.isfinite(sig.beta)

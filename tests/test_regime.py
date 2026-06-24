"""
Tests for strategy/regime_detector.py -- Sprint 3
Covers: vol-ratio thresholds, persistence filter, ADF override,
        regime transitions, sizing multipliers, online update.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy.regime_detector import RegimeDetector, VolRegime, RegimeState


# --- Helpers ------------------------------------------------------------------

def _detector(**kw) -> RegimeDetector:
    defaults = dict(vol_window=5, baseline_window=20, high_vol_threshold=1.5,
                    breakdown_threshold=2.5, min_persistence=3)
    defaults.update(kw)
    return RegimeDetector(**defaults)


def _constant_spread(n: int, vol: float = 0.01, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, vol, n)) + 1000
    return pd.Series(prices)


def _spiked_spread(n_normal: int, n_spike: int, spike_vol: float = 0.1) -> pd.Series:
    rng = np.random.default_rng(42)
    normal = np.cumsum(rng.normal(0, 0.005, n_normal)) + 1000
    spike  = np.cumsum(rng.normal(0, spike_vol, n_spike)) + normal[-1]
    return pd.Series(np.concatenate([normal, spike]))


# --- Constructor validation ---------------------------------------------------

class TestConstructor:
    def test_vol_window_must_be_less_than_baseline(self):
        with pytest.raises(ValueError, match="vol_window"):
            RegimeDetector(vol_window=50, baseline_window=20)

    def test_high_vol_must_be_less_than_breakdown(self):
        with pytest.raises(ValueError, match="high_vol_threshold"):
            RegimeDetector(high_vol_threshold=3.0, breakdown_threshold=2.0)

    def test_valid_constructor_succeeds(self):
        d = _detector()
        assert d.vol_window == 5
        assert d.baseline_window == 20


# --- Regime multipliers -------------------------------------------------------

class TestRegimeMultipliers:
    def test_normal_multiplier_is_one(self):
        d = _detector()
        assert d.get_regime_multiplier(VolRegime.NORMAL) == 1.0

    def test_high_vol_multiplier_is_half(self):
        d = _detector()
        assert d.get_regime_multiplier(VolRegime.HIGH_VOL) == 0.5

    def test_breakdown_multiplier_is_zero(self):
        d = _detector()
        assert d.get_regime_multiplier(VolRegime.BREAKDOWN) == 0.0

    def test_transition_multiplier_is_075(self):
        d = _detector()
        assert d.get_regime_multiplier(VolRegime.TRANSITION) == 0.75

    def test_default_uses_current_regime(self):
        d = _detector()
        assert d.get_regime_multiplier() == 1.0  # default NORMAL


# --- Warm-up: insufficient bars -----------------------------------------------

class TestWarmUp:
    def test_insufficient_bars_returns_normal(self):
        d = _detector(vol_window=5)
        state = d.update_one(0.001)
        assert state.regime == VolRegime.NORMAL
        assert not state.confirmed


# --- Batch classification -----------------------------------------------------

class TestBatch:
    def test_batch_returns_dataframe_with_expected_cols(self):
        spread = _constant_spread(100)
        d = _detector()
        df = d.batch(spread)
        for col in ["regime", "vol_ratio", "current_vol", "baseline_vol", "confirmed", "multiplier"]:
            assert col in df.columns

    def test_batch_length_matches_input(self):
        spread = _constant_spread(200)
        d = _detector()
        df = d.batch(spread)
        assert len(df) == 200

    def test_stable_spread_mostly_normal(self):
        spread = _constant_spread(300, vol=0.0001)
        d = _detector()
        df = d.batch(spread)
        # Most bars should be NORMAL after warm-up
        normal_frac = (df["regime"] == VolRegime.NORMAL.value).mean()
        assert normal_frac > 0.5

    def test_high_vol_spike_detected(self):
        spread = _spiked_spread(n_normal=100, n_spike=60, spike_vol=0.2)
        d = _detector(vol_window=5, baseline_window=20,
                      high_vol_threshold=1.5, breakdown_threshold=3.0,
                      min_persistence=2)
        df = d.batch(spread)
        spike_regime = df.iloc[-30:]["regime"].values
        non_normal = [r for r in spike_regime if r != VolRegime.NORMAL.value]
        assert len(non_normal) > 0, "Spike should trigger non-NORMAL regime"


# --- ADF deterioration override -----------------------------------------------

class TestADFOverride:
    def test_high_adf_pvalue_forces_breakdown(self):
        spread = _constant_spread(100, vol=0.0001)  # low vol normally NORMAL
        adf = pd.Series([0.0] * 80 + [0.15] * 20)  # last 20 bars: ADF deteriorates
        d = _detector(min_persistence=1, adf_deterioration=0.10)
        df = d.batch(spread, adf_pvalues=adf)
        last_20 = df.iloc[-20:]["regime"].values
        assert VolRegime.BREAKDOWN.value in last_20


# --- Persistence filter -------------------------------------------------------

class TestPersistenceFilter:
    def test_single_spike_bar_does_not_switch_immediately(self):
        """With min_persistence=3, a single spike bar should not confirm HIGH_VOL."""
        spread = _constant_spread(200, vol=0.0001)
        d = _detector(min_persistence=5)
        # Feed 150 normal bars, then 2 spike bars, then normal again
        normal_ret = [0.0001] * 150
        spike_ret  = [0.5, 0.5]  # large single-bar spikes
        back_ret   = [0.0001] * 10
        all_ret = normal_ret + spike_ret + back_ret

        d2 = _detector(vol_window=5, baseline_window=20,
                       high_vol_threshold=1.5, min_persistence=5)
        states = [d2.update_one(r) for r in all_ret]
        # After the 2 spike bars + 10 normal, regime should NOT be confirmed HIGH_VOL
        # because persistence=5 was never reached
        last_state = states[-1]
        # Should have reverted before confirming
        assert last_state.persistence_count < 5 or last_state.regime != VolRegime.HIGH_VOL


# --- Online update_one --------------------------------------------------------

class TestOnlineUpdate:
    def test_update_one_returns_regime_state(self):
        d = _detector()
        state = d.update_one(0.001)
        assert isinstance(state, RegimeState)
        assert isinstance(state.regime, VolRegime)

    def test_current_regime_reflects_confirmed(self):
        d = _detector(min_persistence=1)
        for _ in range(30):
            d.update_one(0.0001)
        assert d.current_regime() == VolRegime.NORMAL

    def test_vol_ratio_is_positive(self):
        d = _detector()
        for _ in range(25):
            state = d.update_one(0.001)
        assert state.vol_ratio >= 0.0

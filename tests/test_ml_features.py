"""
tests/test_ml_features.py — Unit tests for FeatureStore (S47).

Tests incremental feature extraction with synthetic bar data.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from strategy.ml.features import FeatureStore


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fs():
    return FeatureStore(maxlen=100)


@pytest.fixture
def sample_bar():
    return {
        "price_y": 50000.0,
        "price_x": 2500.0,
        "volume": 120.0,
        "high": 50100.0,
        "low": 49900.0,
    }


@pytest.fixture
def sample_spread_state():
    return {
        "spread": 20.0,
        "zscore": 1.5,
        "beta": 1.2,
        "uncertainty": 0.05,
        "half_life_hours": 24.0,
        "regime": "ranging",
        "vol_regime": "NORMAL",
        "vol_rank": 0.5,
        "vol_adj_mult": 1.0,
        "spread_width_pct": 0.02,
        "ob_imbalance": 0.5,
        "funding_rate": 0.0001,
    }


# ── Tests ───────────────────────────────────────────────────────────────


class TestFeatureStoreInit:
    def test_default_creation(self):
        fs = FeatureStore(maxlen=100)
        assert not fs.is_warm
        assert fs.bar_count == 0
        assert fs.N_FEATURES == 30

    def test_min_maxlen(self):
        with pytest.raises(ValueError, match="20"):
            FeatureStore(maxlen=10)

    def test_feature_names(self):
        names = FeatureStore.get_feature_names()
        assert len(names) == 30
        assert names[0] == "ret_1"
        assert "rsi_14" in names
        assert "zscore_raw" in names


class TestFeatureUpdate:
    def test_first_update(self, fs, sample_bar, sample_spread_state):
        feats = fs.update(sample_bar, sample_spread_state)
        assert isinstance(feats, dict)
        assert len(feats) == 30
        # After 1 bar, not warm yet
        assert not fs.is_warm
        assert fs.bar_count == 1

    def test_warm_after_20_bars(self, fs, sample_bar, sample_spread_state):
        for _ in range(19):
            fs.update(sample_bar, sample_spread_state)
        assert not fs.is_warm
        fs.update(sample_bar, sample_spread_state)
        assert fs.is_warm
        assert fs.bar_count == 20

    def test_all_features_after_warm(self, fs, sample_bar, sample_spread_state):
        for _ in range(30):
            fs.update(sample_bar, sample_spread_state)
        feats = fs.snapshot()
        for name in FeatureStore.get_feature_names():
            assert name in feats, f"Missing feature: {name}"
            assert not math.isnan(feats[name]), f"NaN in {name}"
            assert math.isfinite(feats[name]), f"Infinite in {name}"

    def test_features_is_dict(self, fs, sample_bar, sample_spread_state):
        fs.update(sample_bar, sample_spread_state)
        feats = fs.snapshot()
        assert isinstance(feats, dict)
        assert all(isinstance(v, float) for v in feats.values())

    def test_feature_values_clipped(self, fs, sample_bar, sample_spread_state):
        # Simulate extreme values
        bar = dict(sample_bar)
        bar["price_y"] = 1e12
        bar["price_x"] = 1e10
        fs.update(bar, sample_spread_state)
        for _ in range(30):
            fs.update(sample_bar, sample_spread_state)
        vec = fs.get_feature_vector()
        assert np.all(np.isfinite(vec))
        assert np.all(np.abs(vec) <= 5.0)


class TestFeatureVector:
    def test_zeros_when_cold(self, fs):
        vec = fs.get_feature_vector()
        assert vec.shape == (30,)
        assert np.all(vec == 0.0)

    def test_nonzero_when_warm(self, fs, sample_bar, sample_spread_state):
        for _ in range(25):
            fs.update(sample_bar, sample_spread_state)
        vec = fs.get_feature_vector()
        assert vec.shape == (30,)
        assert np.any(vec != 0.0)

    def test_custom_feature_names(self, fs, sample_bar, sample_spread_state):
        for _ in range(25):
            fs.update(sample_bar, sample_spread_state)
        names = ["zscore_raw", "rsi_14", "ret_1"]
        vec = fs.get_feature_vector(feature_names=names)
        assert vec.shape == (3,)

    def test_nan_features_become_zero(self, fs):
        # Missing spread_state should produce NaN features → converted to 0
        bar = {"price_y": 50000.0, "price_x": 2500.0, "volume": 100.0,
               "high": 50100.0, "low": 49900.0}
        for _ in range(25):
            fs.update(bar, {})
        vec = fs.get_feature_vector()
        assert np.all(np.isfinite(vec))


class TestReturns:
    def test_ret_1_changes_with_price(self, fs, sample_bar, sample_spread_state):
        fs.update(sample_bar, sample_spread_state)  # bar 0
        bar2 = dict(sample_bar)
        bar2["price_y"] = 51000.0  # +2% from 50000
        fs.update(bar2, sample_spread_state)
        feats = fs.snapshot()
        assert feats["ret_1"] == pytest.approx(0.02, abs=0.001)

    def test_ret_5_requires_5_bars(self, fs, sample_bar, sample_spread_state):
        for _ in range(10):
            fs.update(sample_bar, sample_spread_state)
        feats = fs.snapshot()
        # ret_5 with constant prices should be ~0
        assert abs(feats["ret_5"]) < 0.001


class TestRSI:
    def test_rsi_starts_at_50(self, fs, sample_bar, sample_spread_state):
        fs.update(sample_bar, sample_spread_state)
        feats = fs.snapshot()
        assert feats["rsi_14"] == pytest.approx(50.0, abs=0.1)


class TestFeatureReset:
    def test_reset_clears_state(self, fs, sample_bar, sample_spread_state):
        for _ in range(30):
            fs.update(sample_bar, sample_spread_state)
        assert fs.is_warm
        fs.reset()
        assert not fs.is_warm
        assert fs.bar_count == 0
        vec = fs.get_feature_vector()
        assert np.all(vec == 0.0)


class TestMACD:
    def test_macd_computed(self, fs, sample_bar, sample_spread_state):
        for _ in range(30):
            fs.update(sample_bar, sample_spread_state)
        feats = fs.snapshot()
        assert "macd_line" in feats
        assert "macd_signal" in feats
        assert "macd_hist" in feats
        # With constant price, macd should be near 0
        assert abs(feats["macd_line"]) < 0.01


class TestSnapshot:
    def test_snapshot_empty(self, fs):
        snap = fs.snapshot()
        assert isinstance(snap, dict)

    def test_snapshot_has_all_features(self, fs, sample_bar, sample_spread_state):
        for _ in range(30):
            fs.update(sample_bar, sample_spread_state)
        snap = fs.snapshot()
        assert len(snap) >= 30
        assert all(not math.isnan(v) for v in snap.values())

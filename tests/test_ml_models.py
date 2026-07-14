"""
tests/test_ml_models.py — Unit tests for ML models (S47).

Tests NumpyLogisticRegression, NumpyLinearRegression, ModelRegistry,
and MLInferenceEngine.
"""
from __future__ import annotations

import numpy as np
import pytest

from strategy.ml.config import MLConfig
from strategy.ml.features import FeatureStore
from strategy.ml.models import (
    MLInferenceEngine,
    MLPrediction,
    ModelRegistry,
    NumpyLinearRegression,
    NumpyLogisticRegression,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def cfg():
    return MLConfig()


@pytest.fixture
def rng():
    return np.random.RandomState(42)


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
def sample_ss():
    return {
        "spread": 20.0, "zscore": 1.5, "beta": 1.2,
        "uncertainty": 0.05, "half_life_hours": 24.0,
        "regime": "ranging", "vol_regime": "NORMAL",
        "vol_rank": 0.5, "vol_adj_mult": 1.0,
    }


@pytest.fixture
def feature_store():
    return FeatureStore(maxlen=100)


# ── NumpyLogisticRegression ─────────────────────────────────────────────


class TestNumpyLogisticRegression:
    def test_init(self):
        m = NumpyLogisticRegression(n_features=5)
        assert m.n_features == 5
        assert m.step_count == 0
        assert m.weights.shape == (5,)
        assert np.all(m.weights == 0.0)

    def test_predict_proba_range(self, rng):
        m = NumpyLogisticRegression(n_features=3)
        m.weights = np.array([0.5, -0.3, 0.1])
        m.bias = 0.0
        X = rng.randn(10, 3)
        proba = m.predict_proba(X)
        assert proba.shape == (10,)
        assert np.all(proba >= 0.0) and np.all(proba <= 1.0)

    def test_predict_direction_range(self, rng):
        m = NumpyLogisticRegression(n_features=3)
        m.weights = np.array([0.5, -0.3, 0.1])
        X = rng.randn(10, 3)
        direction = m.predict_direction(X)
        assert direction.shape == (10,)
        assert np.all(direction >= -1.0) and np.all(direction <= 1.0)

    def test_partial_fit_updates_weights(self, rng):
        m = NumpyLogisticRegression(n_features=2, lr=0.1)
        # Simple separable data
        X = np.array([[1.0, 1.0], [-1.0, -1.0], [1.0, -1.0], [-1.0, 1.0]])
        y = np.array([1, 0, 1, 0])
        initial_weights = m.weights.copy()
        for _ in range(50):
            m.partial_fit(X, y)
        assert m.step_count == 50
        assert not np.allclose(m.weights, initial_weights)
        # Should learn to separate
        pred = m.predict(X)
        assert np.mean(pred == y) >= 0.5  # better than random

    def test_serialize_roundtrip(self):
        m = NumpyLogisticRegression(n_features=5)
        m.weights = np.array([0.1, -0.2, 0.3, -0.4, 0.5])
        m.bias = 0.1
        m.step_count = 42
        d = m.to_dict()
        m2 = NumpyLogisticRegression.from_dict(d)
        assert m2.n_features == 5
        assert m2.step_count == 42
        assert np.allclose(m2.weights, m.weights)
        assert m2.bias == pytest.approx(0.1)


# ── NumpyLinearRegression ───────────────────────────────────────────────


class TestNumpyLinearRegression:
    def test_init(self):
        m = NumpyLinearRegression(n_features=5)
        assert m.n_features == 5
        assert m.step_count == 0

    def test_predict(self, rng):
        m = NumpyLinearRegression(n_features=2)
        m.weights = np.array([2.0, -1.0])
        m.bias = 0.5
        X = np.array([[1.0, 0.0], [0.0, 1.0], [2.0, 3.0]])
        pred = m.predict(X)
        assert pred.shape == (3,)
        assert pred[0] == pytest.approx(2.5)   # 2*1 + (-1)*0 + 0.5
        assert pred[1] == pytest.approx(-0.5)  # 2*0 + (-1)*1 + 0.5
        assert pred[2] == pytest.approx(1.5)   # 2*2 + (-1)*3 + 0.5

    def test_partial_fit_converges(self, rng):
        m = NumpyLinearRegression(n_features=1, lr=0.05)
        # y = 3x + 2
        X = np.linspace(-10, 10, 100).reshape(-1, 1)
        y = 3.0 * X.ravel() + 2.0
        for _ in range(200):
            m.partial_fit(X, y)
        # Should approximate the coefficients
        assert m.weights[0] == pytest.approx(3.0, abs=0.5)
        assert m.bias == pytest.approx(2.0, abs=0.5)

    def test_serialize_roundtrip(self):
        m = NumpyLinearRegression(n_features=3)
        m.weights = np.array([1.5, -2.5, 0.0])
        m.bias = 0.75
        m.step_count = 100
        d = m.to_dict()
        m2 = NumpyLinearRegression.from_dict(d)
        assert m2.n_features == 3
        assert np.allclose(m2.weights, m.weights)
        assert m2.bias == pytest.approx(0.75)


# ── ModelRegistry ───────────────────────────────────────────────────────


class TestModelRegistry:
    def test_empty_registry(self, cfg):
        reg = ModelRegistry(cfg)
        assert not reg.has_models
        assert reg.model_count == 0

    def test_register_models(self, cfg):
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))
        assert reg.has_models
        assert reg.model_count == 2

    def test_model_info(self, cfg):
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))
        info = reg.get_model_info()
        assert len(info) == 2
        assert info[0]["id"] == "lr1"
        assert info[0]["type"] == "logistic"

    def test_max_models(self, cfg):
        cfg.max_models_per_type = 2
        reg = ModelRegistry(cfg)
        for i in range(4):
            reg.register_direction(f"lr{i}", NumpyLogisticRegression(30))
        assert len(reg.direction_models) == 2
        # Should keep the last 2
        assert reg.direction_models[0][0] == "lr2"
        assert reg.direction_models[1][0] == "lr3"

    def test_feature_importance(self, cfg):
        reg = ModelRegistry(cfg)
        reg.set_feature_names(["a", "b", "c", "d", "e"])
        m = NumpyLogisticRegression(5)
        m.weights = np.array([0.5, -0.3, 0.0, 0.8, 0.1])
        reg.register_direction("lr1", m)
        m2 = NumpyLinearRegression(5)
        m2.weights = np.array([0.2, -0.1, 0.0, 0.6, 0.0])
        reg.register_confidence("lin1", m2)
        imp = reg.feature_importance()
        assert len(imp) <= 5
        # Feature "d" (index 3) should be most important
        sorted_imp = sorted(imp.items(), key=lambda kv: kv[1], reverse=True)
        # Check that d (index 3) has highest importance
        assert sorted_imp[0][1] > 0

    def test_save_load(self, cfg, tmp_path):
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(5))
        reg.register_confidence("lin1", NumpyLinearRegression(5))
        d = str(tmp_path / "models")
        reg.save(d)
        reg2 = ModelRegistry(cfg)
        loaded = reg2.load(d)
        assert loaded == 2
        assert reg2.has_models


# ── MLInferenceEngine ───────────────────────────────────────────────────


class TestMLInferenceEngine:
    def test_init_cold(self, cfg, feature_store):
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))
        eng = MLInferenceEngine(cfg, reg, feature_store)
        assert not eng.is_warm
        assert eng.bars_seen == 0

    def test_returns_zero_when_cold(self, cfg, feature_store, sample_bar, sample_ss):
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))
        eng = MLInferenceEngine(cfg, reg, feature_store)
        d, c = eng.update(sample_bar, sample_ss)
        assert d == 0.0
        assert c == 0.0
        assert eng.last_prediction.direction == "FLAT"

    def test_warms_up_after_min_bars(self, cfg, feature_store, sample_bar, sample_ss):
        cfg.model_warmup_bars = 100
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))
        eng = MLInferenceEngine(cfg, reg, feature_store)
        for _ in range(100):
            feat = feature_store.update(sample_bar, sample_ss)
        for _ in range(100):
            eng.update(sample_bar, sample_ss)
        assert eng.is_warm

    def test_snapshot(self, cfg, feature_store, sample_bar, sample_ss):
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))
        eng = MLInferenceEngine(cfg, reg, feature_store)
        for _ in range(30):
            eng.update(sample_bar, sample_ss)
        snap = eng.snapshot()
        assert "enabled" in snap
        assert "bars_seen" in snap
        assert "model_count" in snap

    def test_prediction_history_capped(self, cfg, feature_store, sample_bar, sample_ss):
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))
        eng = MLInferenceEngine(cfg, reg, feature_store)
        for _ in range(150):
            eng.update(sample_bar, sample_ss)
        assert len(eng.prediction_history) <= 100

    def test_feature_importance(self, cfg, feature_store, sample_bar, sample_ss):
        reg = ModelRegistry(cfg)
        m = NumpyLogisticRegression(30)
        m.weights = np.random.randn(30) * 0.1
        reg.register_direction("lr1", m)
        m2 = NumpyLinearRegression(30)
        m2.weights = np.random.randn(30) * 0.1
        reg.register_confidence("lin1", m2)
        eng = MLInferenceEngine(cfg, reg, feature_store)
        for _ in range(30):
            eng.update(sample_bar, sample_ss)
        imp = eng.get_feature_importance()
        assert isinstance(imp, dict)
        assert len(imp) >= 1


class TestMLPrediction:
    def test_default(self):
        p = MLPrediction()
        assert p.score == 0.0
        assert p.direction == "FLAT"
        assert p.confidence == 0.0

    def test_as_dict(self):
        p = MLPrediction(
            score=0.8, confidence=0.9, direction="LONG",
            feature_count=30, model_name="test", latency_us=42.0,
        )
        d = p.as_dict()
        assert d["score"] == 0.8
        assert d["direction"] == "LONG"
        assert d["latency_us"] == 42.0

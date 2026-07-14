"""
strategy/ml/models.py — Pure-numpy ML inference for real-time trading.

Provides:
  - NumpyLogisticRegression  — SGD logistic regression (direction predictor)
  - NumpyLinearRegression    — SGD linear regression (confidence predictor)
  - ModelRegistry            — manages direction + confidence model ensembles
  - MLInferenceEngine        — bar-by-bar inference orchestrator
  - MLPrediction             — dataclass for prediction output

All models use SGD + L2 regularisation with online partial_fit().
No sklearn, PyTorch, or TensorFlow dependency — pure numpy only.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ═══════════════════════════════════════════════════════════════════════════════
# Prediction output
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class MLPrediction:
    """Output of one inference step."""

    score: float = 0.0          # [-1, 1]  direction + confidence
    confidence: float = 0.0     # [0, 1]   model confidence
    direction: str = "FLAT"     # "LONG" | "SHORT" | "FLAT"
    feature_count: int = 0
    model_name: str = ""
    latency_us: float = 0.0     # inference time in microseconds

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "direction": self.direction,
            "feature_count": self.feature_count,
            "model_name": self.model_name,
            "latency_us": self.latency_us,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Pure-numpy models
# ═══════════════════════════════════════════════════════════════════════════════


class NumpyLogisticRegression:
    """
    Binary logistic regression (SGD + L2).

    Predicts P(direction=up) → maps to [-1, 1] via 2*P - 1.
    Uses online partial_fit() for incremental learning.
    """

    def __init__(
        self,
        n_features: int,
        lr: float = 0.01,
        l2_reg: float = 0.001,
    ) -> None:
        self.weights: np.ndarray = np.zeros(n_features, dtype=np.float64)
        self.bias: float = 0.0
        self.lr: float = lr
        self.l2: float = l2_reg
        self.step_count: int = 0
        self.n_features: int = n_features

    # ── Inference ───────────────────────────────────────────────────────

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return P(y=1) in [0, 1]."""
        z = X @ self.weights + self.bias
        z = np.clip(z, -30.0, 30.0)
        return 1.0 / (1.0 + np.exp(-z))

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return binary predictions {0, 1}."""
        return (self.predict_proba(X) >= 0.5).astype(np.float64)

    def predict_direction(self, X: np.ndarray) -> np.ndarray:
        """Return direction scores in [-1, 1] (2*P - 1)."""
        return 2.0 * self.predict_proba(X) - 1.0

    # ── Training ────────────────────────────────────────────────────────

    def partial_fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """
        One SGD step on (X, y).

        Parameters
        ----------
        X : np.ndarray  shape (n_samples, n_features)
        y : np.ndarray  shape (n_samples,)  binary {0, 1}
        """
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        y = np.asarray(y, dtype=np.float64).ravel()
        n = X.shape[0]

        proba = self.predict_proba(X)
        error = proba - y           # gradient of log-loss
        grad_w = (X.T @ error) / n + self.l2 * self.weights
        grad_b = float(np.mean(error))

        self.weights -= self.lr * grad_w
        self.bias    -= self.lr * grad_b
        self.step_count += 1

        # Learning-rate decay
        if self.step_count % 100 == 0:
            self.lr *= 0.99

    # ── Persistence ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "type": "logistic",
            "n_features": self.n_features,
            "weights": self.weights.tolist(),
            "bias": self.bias,
            "lr": self.lr,
            "l2": self.l2,
            "step_count": self.step_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NumpyLogisticRegression":
        m = cls(n_features=d["n_features"], lr=d.get("lr", 0.01), l2_reg=d.get("l2", 0.001))
        m.weights = np.array(d["weights"], dtype=np.float64)
        m.bias = float(d.get("bias", 0.0))
        m.step_count = int(d.get("step_count", 0))
        return m


class NumpyLinearRegression:
    """
    Linear regression (SGD + L2).

    Predicts a continuous score.  Used as confidence model.
    Uses online partial_fit() for incremental learning.
    """

    def __init__(
        self,
        n_features: int,
        lr: float = 0.005,
        l2_reg: float = 0.001,
    ) -> None:
        self.weights: np.ndarray = np.zeros(n_features, dtype=np.float64)
        self.bias: float = 0.0
        self.lr: float = lr
        self.l2: float = l2_reg
        self.step_count: int = 0
        self.n_features: int = n_features

    # ── Inference ───────────────────────────────────────────────────────

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return continuous predictions."""
        return X @ self.weights + self.bias

    # ── Training ────────────────────────────────────────────────────────

    def partial_fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """
        One SGD step on (X, y) with MSE loss.

        Parameters
        ----------
        X : np.ndarray  shape (n_samples, n_features)
        y : np.ndarray  shape (n_samples,)  continuous target
        """
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        y = np.asarray(y, dtype=np.float64).ravel()
        n = X.shape[0]

        pred = self.predict(X)
        error = pred - y           # gradient of MSE
        grad_w = (X.T @ error) / n + self.l2 * self.weights
        grad_b = float(np.mean(error))

        self.weights -= self.lr * grad_w
        self.bias    -= self.lr * grad_b
        self.step_count += 1

        if self.step_count % 100 == 0:
            self.lr *= 0.99

    # ── Persistence ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "type": "linear",
            "n_features": self.n_features,
            "weights": self.weights.tolist(),
            "bias": self.bias,
            "lr": self.lr,
            "l2": self.l2,
            "step_count": self.step_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NumpyLinearRegression":
        m = cls(n_features=d["n_features"], lr=d.get("lr", 0.005), l2_reg=d.get("l2", 0.001))
        m.weights = np.array(d["weights"], dtype=np.float64)
        m.bias = float(d.get("bias", 0.0))
        m.step_count = int(d.get("step_count", 0))
        return m


# ═══════════════════════════════════════════════════════════════════════════════
# Model Registry
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class _ModelEntry:
    model_id: str
    model: object
    weight: float


class ModelRegistry:
    """
    Manages a pool of direction + confidence models.

    Provides ensemble predictions via weighted averaging and feature
    importance aggregation across all models.
    """

    def __init__(self, cfg: "MLConfig") -> None:  # noqa: F821
        self._cfg = cfg
        self._direction_models: List[_ModelEntry] = []
        self._confidence_models: List[_ModelEntry] = []
        self._feature_names: List[str] = []

    # ── Registration ────────────────────────────────────────────────────

    def register_direction(
        self, model_id: str, model: NumpyLogisticRegression, weight: float = 1.0,
    ) -> None:
        self._direction_models.append(_ModelEntry(model_id, model, weight))
        if len(self._direction_models) > self._cfg.max_models_per_type:
            self._direction_models.pop(0)

    def register_confidence(
        self, model_id: str, model: NumpyLinearRegression, weight: float = 1.0,
    ) -> None:
        self._confidence_models.append(_ModelEntry(model_id, model, weight))
        if len(self._confidence_models) > self._cfg.max_models_per_type:
            self._confidence_models.pop(0)

    # ── Queries ─────────────────────────────────────────────────────────

    @property
    def has_models(self) -> bool:
        return len(self._direction_models) > 0 and len(self._confidence_models) > 0

    @property
    def direction_models(self) -> List[Tuple[str, NumpyLogisticRegression, float]]:
        return [(e.model_id, e.model, e.weight) for e in self._direction_models]

    @property
    def confidence_models(self) -> List[Tuple[str, NumpyLinearRegression, float]]:
        return [(e.model_id, e.model, e.weight) for e in self._confidence_models]

    @property
    def model_count(self) -> int:
        return len(self._direction_models) + len(self._confidence_models)

    def set_feature_names(self, names: List[str]) -> None:
        self._feature_names = names

    def get_model_info(self) -> List[dict]:
        """Return model info dicts for API/dashboard."""
        info: List[dict] = []
        for e in self._direction_models:
            info.append({
                "id": e.model_id,
                "type": "logistic",
                "weight": e.weight,
                "step": e.model.step_count,
                "lr": round(e.model.lr, 6),
            })
        for e in self._confidence_models:
            info.append({
                "id": e.model_id,
                "type": "linear",
                "weight": e.weight,
                "step": e.model.step_count,
                "lr": round(e.model.lr, 6),
            })
        return info

    def feature_importance(self) -> Dict[str, float]:
        """Aggregated feature importance (mean |weight| across all models)."""
        if not self._feature_names:
            return {}

        n = len(self._feature_names)
        agg = np.zeros(n, dtype=np.float64)
        count = 0

        for e in self._confidence_models:
            w = e.model.weights
            agg[: len(w)] += np.abs(w[:n]) if len(w) >= n else np.abs(w)
            count += 1
        for e in self._direction_models:
            w = e.model.weights
            agg[: len(w)] += np.abs(w[:n]) if len(w) >= n else np.abs(w)
            count += 1

        if count > 0:
            agg /= count

        return {
            self._feature_names[i]: float(agg[i])
            for i in range(n)
            if float(agg[i]) > 1e-9
        }

    # ── Persistence ─────────────────────────────────────────────────────

    def save(self, directory: str) -> None:
        """Save all models to JSON files in directory."""
        p = Path(directory)
        p.mkdir(parents=True, exist_ok=True)
        for i, e in enumerate(self._direction_models):
            with open(p / f"direction_{i:02d}.json", "w") as f:
                json.dump(e.model.to_dict(), f, indent=2)
        for i, e in enumerate(self._confidence_models):
            with open(p / f"confidence_{i:02d}.json", "w") as f:
                json.dump(e.model.to_dict(), f, indent=2)

    def load(self, directory: str) -> int:
        """
        Load models from JSON files.  Returns count of loaded files.
        """
        p = Path(directory)
        if not p.exists():
            return 0

        loaded = 0
        self._direction_models.clear()
        self._confidence_models.clear()

        for path in sorted(p.glob("direction_*.json")):
            try:
                d = json.loads(path.read_text())
                model = NumpyLogisticRegression.from_dict(d)
                self._direction_models.append(
                    _ModelEntry(path.stem, model, 1.0)
                )
                loaded += 1
            except Exception:
                pass

        for path in sorted(p.glob("confidence_*.json")):
            try:
                d = json.loads(path.read_text())
                model = NumpyLinearRegression.from_dict(d)
                self._confidence_models.append(
                    _ModelEntry(path.stem, model, 1.0)
                )
                loaded += 1
            except Exception:
                pass

        return loaded


# ═══════════════════════════════════════════════════════════════════════════════
# ML Inference Engine
# ═══════════════════════════════════════════════════════════════════════════════


class MLInferenceEngine:
    """
    Bar-by-bar inference orchestrator.

    Maintains a FeatureStore, runs ensemble models, and produces
    (direction, confidence) predictions suitable for signal fusion.

    Usage::

        cfg = MLConfig()
        fs  = FeatureStore(maxlen=cfg.feature_lookback)
        reg = ModelRegistry(cfg)
        reg.register_direction("lr1", NumpyLogisticRegression(30))
        reg.register_confidence("lin1", NumpyLinearRegression(30))

        engine = MLInferenceEngine(cfg, reg, fs)

        # Per bar:
        ml_dir, ml_conf = engine.update(bar_dict, spread_state)
        pred = engine.last_prediction  # → MLPrediction
    """

    def __init__(
        self,
        cfg: "MLConfig",        # noqa: F821
        registry: ModelRegistry,
        feature_store: "FeatureStore",  # noqa: F821
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._features = feature_store
        self._feature_names = feature_store.get_feature_names()
        self._registry.set_feature_names(self._feature_names)

        self._last_prediction = MLPrediction()
        self._bars_seen: int = 0
        self._prediction_history: List[MLPrediction] = []

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def last_prediction(self) -> MLPrediction:
        return self._last_prediction

    @property
    def bars_seen(self) -> int:
        return self._bars_seen

    @property
    def prediction_history(self) -> List[MLPrediction]:
        """Last 100 predictions for API/dashboard."""
        return self._prediction_history[-100:]

    @property
    def is_warm(self) -> bool:
        return (
            self._cfg.enabled
            and self._bars_seen >= self._cfg.model_warmup_bars
            and self._registry.has_models
            and self._features.is_warm
        )

    def update(
        self,
        bar: dict,
        spread_state: Optional[dict] = None,
    ) -> Tuple[float, float]:
        """
        Process one bar.  Returns (ml_direction, ml_confidence).

        ml_direction  ∈ [-1, 1]   negative → SHORT, positive → LONG
        ml_confidence ∈ [0, 1]
        """
        t0 = time.perf_counter()
        self._features.update(bar, spread_state)
        self._bars_seen += 1

        # Not ready — return neutral
        if not self._cfg.enabled or not self.is_warm:
            self._last_prediction = MLPrediction(
                score=0.0, confidence=0.0, direction="FLAT",
                feature_count=self._features.N_FEATURES,
                model_name="warmup",
                latency_us=(time.perf_counter() - t0) * 1e6,
            )
            self._prediction_history.append(self._last_prediction)
            if len(self._prediction_history) > 100:
                self._prediction_history.pop(0)
            return (0.0, 0.0)

        X = self._features.get_feature_vector(self._feature_names).reshape(1, -1)

        # ── Direction ensemble ──────────────────────────────────────────
        direction = 0.0
        total_w = 0.0
        for _, model, weight in self._registry.direction_models:
            d = float(model.predict_direction(X)[0])
            direction += weight * d
            total_w += weight
        direction = direction / max(total_w, 1e-9)

        # ── Confidence ensemble ─────────────────────────────────────────
        confidence = 0.0
        total_w = 0.0
        for _, model, weight in self._registry.confidence_models:
            c = float(np.clip(model.predict(X)[0], -1.0, 1.0))
            confidence += weight * abs(c)
            total_w += weight
        confidence = float(np.clip(confidence / max(total_w, 1e-9), 0.0, 1.0))

        # ── Direction label ─────────────────────────────────────────────
        if direction > 0.2:
            dir_label = "LONG"
        elif direction < -0.2:
            dir_label = "SHORT"
        else:
            dir_label = "FLAT"

        elapsed_us = (time.perf_counter() - t0) * 1e6

        pred = MLPrediction(
            score=direction,
            confidence=confidence,
            direction=dir_label,
            feature_count=self._features.N_FEATURES,
            model_name=f"ensemble({self._registry.model_count})",
            latency_us=elapsed_us,
        )

        self._last_prediction = pred
        self._prediction_history.append(pred)
        if len(self._prediction_history) > 100:
            self._prediction_history.pop(0)

        return (direction, confidence)

    # ── Training ────────────────────────────────────────────────────────

    def train_step(
        self,
        X_batch: np.ndarray,
        y_dir: np.ndarray,
        y_conf: np.ndarray,
    ) -> None:
        """
        Online training step on a batch of labeled data.

        Parameters
        ----------
        X_batch : np.ndarray  shape (n_samples, n_features)
        y_dir   : np.ndarray  binary target for direction model
        y_conf  : np.ndarray  continuous target for confidence model
        """
        X = np.atleast_2d(np.asarray(X_batch, dtype=np.float64))
        for _, model, _ in self._registry.direction_models:
            model.partial_fit(X, y_dir)
        for _, model, _ in self._registry.confidence_models:
            model.partial_fit(X, y_conf)

    # ── Feature importance ──────────────────────────────────────────────

    def get_feature_importance(self) -> Dict[str, float]:
        """Sorted feature importance dict (highest first)."""
        imp = self._registry.feature_importance()
        return dict(sorted(imp.items(), key=lambda kv: abs(kv[1]), reverse=True))

    # ── State snapshot ──────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Full state for API / dashboard."""
        return {
            "enabled": self._cfg.enabled,
            "is_warm": self.is_warm,
            "bars_seen": self._bars_seen,
            "model_count": self._registry.model_count,
            "has_models": self._registry.has_models,
            "last_prediction": self._last_prediction.as_dict(),
            "feature_count": self._features.N_FEATURES,
            "warmup_remaining": max(
                0, self._cfg.model_warmup_bars - self._bars_seen
            ),
        }

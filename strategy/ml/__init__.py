"""
strategy/ml/ — AI/ML Signal Layer for QuantLuna (Sprint 47)

Adds ML-driven signal generation on top of the Kalman/Z-score pipeline:
  - FeatureStore:  incremental feature extraction from bars + spread state
  - MLInferenceEngine:  real-time model inference (pure numpy)
  - SignalFusion:  regime-adaptive blending of ML + Z-score signals
  - ModelRegistry:  manages direction + confidence models

Usage::

    from strategy.ml import MLConfig, FeatureStore, ModelRegistry, MLInferenceEngine, SignalFusion

    cfg = MLConfig()
    fs  = FeatureStore(maxlen=cfg.feature_lookback)
    reg = ModelRegistry(cfg)
    reg.register_direction("lr1", NumpyLogisticRegression(n_features=30))
    reg.register_confidence("lin1", NumpyLinearRegression(n_features=30))
    engine = MLInferenceEngine(cfg, reg, fs)
    fusion = SignalFusion(cfg)

    # In the trading loop:
    features = fs.update(bar_dict, spread_state)
    ml_dir, ml_conf = engine.update(bar_dict, spread_state)
    fused = fusion.fuse(ml_dir, ml_conf, zscore_signal, zscore_conf, regime)
"""

from strategy.ml.config import MLConfig  # noqa: F401
from strategy.ml.features import FeatureStore  # noqa: F401
from strategy.ml.models import (  # noqa: F401
    MLInferenceEngine,
    MLPrediction,
    ModelRegistry,
    NumpyLinearRegression,
    NumpyLogisticRegression,
)
from strategy.ml.signal_fusion import FusedSignal, SignalFusion  # noqa: F401

__all__ = [
    "MLConfig",
    "FeatureStore",
    "MLInferenceEngine",
    "MLPrediction",
    "ModelRegistry",
    "NumpyLinearRegression",
    "NumpyLogisticRegression",
    "FusedSignal",
    "SignalFusion",
]

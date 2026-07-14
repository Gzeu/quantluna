"""
strategy/ml/config.py — ML configuration dataclass.

All ML hyperparameters in one place.  Used by FeatureStore, MLInferenceEngine,
SignalFusion, and the dashboard.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MLConfig:
    """Master configuration for the AI/ML signal layer."""

    # ── Top-level ──────────────────────────────────────────────────────────
    enabled: bool = os.getenv("ML_ENABLED", "true").lower() == "true"
    collect_features_only: bool = (
        os.getenv("ML_COLLECT_ONLY", "false").lower() == "true"
    )

    # ── Feature engineering ────────────────────────────────────────────────
    feature_lookback: int = int(os.getenv("ML_FEATURE_LOOKBACK", "100"))
    feature_update_interval: int = 1  # bars between feature recomputation

    # ── Training ───────────────────────────────────────────────────────────
    model_warmup_bars: int = int(os.getenv("ML_WARMUP_BARS", "200"))
    retrain_interval_bars: int = int(os.getenv("ML_RETRAIN_INTERVAL", "500"))

    # ── Direction model (logistic regression) ──────────────────────────────
    lr_learning_rate: float = float(os.getenv("ML_LR_RATE", "0.01"))
    lr_l2_reg: float = float(os.getenv("ML_LR_L2", "0.001"))

    # ── Confidence model (linear regression) ───────────────────────────────
    linear_learning_rate: float = float(os.getenv("ML_LIN_RATE", "0.005"))
    linear_l2_reg: float = float(os.getenv("ML_LIN_L2", "0.001"))

    # ── Model registry ─────────────────────────────────────────────────────
    max_models_per_type: int = int(os.getenv("ML_MAX_MODELS", "3"))

    # ── Fusion ─────────────────────────────────────────────────────────────
    ml_weight_min: float = float(os.getenv("ML_WEIGHT_MIN", "0.0"))
    ml_weight_max: float = float(os.getenv("ML_WEIGHT_MAX", "0.5"))
    zscore_weight_min: float = float(os.getenv("ML_Z_WEIGHT_MIN", "0.5"))
    zscore_weight_max: float = float(os.getenv("ML_Z_WEIGHT_MAX", "1.0"))
    confidence_threshold: float = float(os.getenv("ML_CONF_THRESHOLD", "0.3"))
    fusion_entry_threshold: float = float(os.getenv("ML_FUSION_THRESHOLD", "0.3"))

    # ── Regime-adaptive weights ────────────────────────────────────────────
    trending_ml_weight: float = float(os.getenv("ML_TRENDING_W", "0.40"))
    ranging_ml_weight: float = float(os.getenv("ML_RANGING_W", "0.15"))
    breakout_ml_weight: float = float(os.getenv("ML_BREAKOUT_W", "0.50"))
    unknown_ml_weight: float = float(os.getenv("ML_UNKNOWN_W", "0.20"))

    # ── Persistence ────────────────────────────────────────────────────────
    model_checkpoint_dir: str = os.getenv(
        "ML_CHECKPOINT_DIR", "data/models/"
    )

    def __post_init__(self) -> None:
        if not (0.0 <= self.ml_weight_min <= self.ml_weight_max <= 1.0):
            raise ValueError(
                f"ml_weight_min ({self.ml_weight_min}) must be <= "
                f"ml_weight_max ({self.ml_weight_max}) in [0, 1]"
            )
        if not (0.0 <= self.zscore_weight_min <= self.zscore_weight_max <= 1.0):
            raise ValueError(
                f"zscore_weight_min ({self.zscore_weight_min}) must be <= "
                f"zscore_weight_max ({self.zscore_weight_max}) in [0, 1]"
            )
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(
                f"confidence_threshold ({self.confidence_threshold}) must be in [0, 1]"
            )
        if self.feature_lookback < 20:
            raise ValueError("feature_lookback must be >= 20")
        if self.model_warmup_bars < 50:
            raise ValueError("model_warmup_bars must be >= 50")

    def ensure_checkpoint_dir(self) -> Path:
        """Create checkpoint directory if it doesn't exist, return Path."""
        p = Path(self.model_checkpoint_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def from_env(cls) -> "MLConfig":
        """Build MLConfig exclusively from env vars (explicit override)."""
        return cls()

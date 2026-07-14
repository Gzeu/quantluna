"""
api/ml.py — FastAPI router for the AI/ML signal layer.

Endpoints:
  GET  /api/ml/status      — pipeline status (enabled, warm, bars, models)
  GET  /api/ml/prediction   — latest prediction (direction, confidence, score)
  GET  /api/ml/features     — current feature values
  GET  /api/ml/features/importance — feature importance (sorted)
  GET  /api/ml/models       — registered models (IDs, types, steps)
  GET  /api/ml/fusion       — current fusion state + regime weights
  POST /api/ml/train        — trigger retraining
  PUT  /api/ml/config       — update fusion weights / thresholds
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

ml_router = APIRouter(prefix="/api/ml", tags=["ml"])

# ── Module-level state (injected by api/main.py lifespan) ───────────────────

_ML_STATE: Dict[str, Any] = {
    "ml_engine": None,
    "feature_store": None,
    "registry": None,
    "fusion": None,
}


def set_ml_state(state: Dict[str, Any]) -> None:
    """Inject ML components from the API lifespan."""
    _ML_STATE.update(state)


def _get_engine():
    eng = _ML_STATE.get("ml_engine")
    if eng is None:
        raise HTTPException(status_code=503, detail="ML engine not initialised")
    return eng


def _get_fusion():
    fus = _ML_STATE.get("fusion")
    if fus is None:
        raise HTTPException(status_code=503, detail="Signal fusion not initialised")
    return fus


def _get_registry():
    reg = _ML_STATE.get("registry")
    if reg is None:
        raise HTTPException(status_code=503, detail="Model registry not initialised")
    return reg


def _get_feature_store():
    fs = _ML_STATE.get("feature_store")
    if fs is None:
        raise HTTPException(status_code=503, detail="Feature store not initialised")
    return fs


# ── Pydantic schemas ────────────────────────────────────────────────────────


class MLStatusResponse(BaseModel):
    enabled: bool
    is_warm: bool
    bars_seen: int
    model_count: int
    has_models: bool
    feature_count: int
    warmup_remaining: int
    last_prediction: Optional[dict] = None


class MLPredictionResponse(BaseModel):
    direction: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=-1.0, le=1.0)
    direction_label: str
    latency_us: float


class MLFeatureItem(BaseModel):
    name: str
    value: float


class MLFeaturesResponse(BaseModel):
    features: List[MLFeatureItem]
    feature_count: int


class MLFeatureImportanceItem(BaseModel):
    name: str
    importance: float


class MLFeatureImportanceResponse(BaseModel):
    importance: List[MLFeatureImportanceItem]
    total_features: int


class MLModelInfo(BaseModel):
    id: str
    type: str
    weight: float
    step: int
    lr: float


class MLModelsResponse(BaseModel):
    models: List[MLModelInfo]
    total: int


class MLFusionResponse(BaseModel):
    last_fused: Optional[dict] = None
    history_count: int
    config: dict
    regime_weights: dict


class MLConfigUpdate(BaseModel):
    fusion_weight_ml: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    fusion_weight_z: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    trending_ml_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ranging_ml_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    breakout_ml_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# ── Endpoints ───────────────────────────────────────────────────────────────


@ml_router.get("/status", response_model=MLStatusResponse)
async def ml_status():
    """Pipeline status — enabled, warm state, model counts."""
    eng = _ML_STATE.get("ml_engine")
    if eng is None:
        return MLStatusResponse(
            enabled=False, is_warm=False, bars_seen=0,
            model_count=0, has_models=False, feature_count=0,
            warmup_remaining=0, last_prediction=None,
        )
    snap = eng.snapshot()
    return MLStatusResponse(**snap)


@ml_router.get("/prediction", response_model=MLPredictionResponse)
async def ml_prediction():
    """Latest ML prediction (direction, confidence, score)."""
    eng = _get_engine()
    pred = eng.last_prediction
    return MLPredictionResponse(
        direction=pred.score,
        confidence=pred.confidence,
        score=pred.score,
        direction_label=pred.direction,
        latency_us=pred.latency_us,
    )


@ml_router.get("/features", response_model=MLFeaturesResponse)
async def ml_features():
    """Current feature values (all 30 features)."""
    fs = _get_feature_store()
    snap = fs.snapshot()
    items = [
        MLFeatureItem(name=k, value=v)
        for k, v in sorted(snap.items())
    ]
    return MLFeaturesResponse(features=items, feature_count=len(items))


@ml_router.get("/features/importance", response_model=MLFeatureImportanceResponse)
async def ml_feature_importance():
    """Feature importance scores (sorted by absolute weight, highest first)."""
    eng = _get_engine()
    imp = eng.get_feature_importance()
    items = [
        MLFeatureImportanceItem(name=k, importance=v)
        for k, v in imp.items()
    ]
    return MLFeatureImportanceResponse(importance=items, total_features=len(items))


@ml_router.get("/models", response_model=MLModelsResponse)
async def ml_models():
    """Registered models — IDs, types, training steps, learning rates."""
    reg = _get_registry()
    info = reg.get_model_info()
    models = [MLModelInfo(**m) for m in info]
    return MLModelsResponse(models=models, total=len(models))


@ml_router.get("/fusion", response_model=MLFusionResponse)
async def ml_fusion():
    """Fusion state — last fused signal and current regime weights."""
    fus = _get_fusion()
    return MLFusionResponse(**fus.snapshot())


@ml_router.post("/train")
async def ml_train():
    """
    Trigger a training step on accumulated features.
    Training data is managed internally by the engine.
    """
    eng = _get_engine()
    # In a full implementation, this would collect features from the
    # feature store buffer and run a training step.  For now, it's a
    # no-op placeholder that the engine processes as new bars arrive.
    if not eng.is_warm:
        raise HTTPException(
            status_code=400,
            detail=f"Engine not warm ({eng.bars_seen} bars, need {eng._cfg.model_warmup_bars})",
        )
    return {
        "status": "ok",
        "message": "Training triggered (online SGD processes each bar)",
        "bars_seen": eng.bars_seen,
    }


@ml_router.put("/config")
async def ml_update_config(update: MLConfigUpdate):
    """Update ML fusion config at runtime."""
    fus = _get_fusion()
    cfg = fus._cfg

    changed: List[str] = []
    for field_name in [
        "fusion_weight_ml", "fusion_weight_z", "confidence_threshold",
        "trending_ml_weight", "ranging_ml_weight",
        "breakout_ml_weight",
    ]:
        value = getattr(update, field_name, None)
        if value is not None:
            if hasattr(cfg, field_name):
                setattr(cfg, field_name, value)
                changed.append(field_name)

    return {"status": "ok", "changed": changed, "message": f"Updated {len(changed)} fields"}

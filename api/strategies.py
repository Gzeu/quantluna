"""
Module: api/strategies.py
Sprint: 31 — T (Multi-Strategy Engine)
Description:
    FastAPI router for Multi-Strategy Engine management.
    Endpoints:
        GET  /strategies/list        — list active strategies + metrics
        GET  /strategies/{id}        — metrics for one strategy
        POST /strategies/{id}/pause  — pause a strategy
        POST /strategies/{id}/resume — resume a strategy
        GET  /strategies/signals     — recent strategy signals

Usage:
    from api.strategies import router as strategies_router
    app.include_router(strategies_router, prefix="/strategies", tags=["strategies"])
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from strategy.multi_strategy_engine import MultiStrategyEngine

logger = logging.getLogger(__name__)
router = APIRouter()

# Module-level engine singleton (injected at startup via app state in production)
_engine: MultiStrategyEngine = MultiStrategyEngine()


def get_engine() -> MultiStrategyEngine:
    return _engine


def set_engine(engine: MultiStrategyEngine) -> None:
    global _engine
    _engine = engine


@router.get("/list")
async def list_strategies() -> dict[str, Any]:
    """List all registered strategies with their current metrics."""
    return {"strategies": _engine.get_metrics()}


@router.get("/signals")
async def get_signals(limit: int = 50) -> dict[str, Any]:
    """Return recent strategy signals."""
    return {"signals": _engine.get_signal_history(limit=limit)}


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str) -> dict[str, Any]:
    """Return metrics for a specific strategy."""
    strategy = _engine.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    metrics = strategy.get_metrics()
    return {
        **metrics.__dict__,
        "allocation": _engine._allocations.get(strategy_id, 0.0),
    }


@router.post("/{strategy_id}/pause")
async def pause_strategy(strategy_id: str) -> dict[str, str]:
    """Pause a running strategy."""
    strategy = _engine.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    strategy.pause()
    logger.info("[STRATEGIES_API] Paused %s", strategy_id)
    return {"status": "paused", "strategy_id": strategy_id}


@router.post("/{strategy_id}/resume")
async def resume_strategy(strategy_id: str) -> dict[str, str]:
    """Resume a paused strategy."""
    strategy = _engine.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    strategy.resume()
    logger.info("[STRATEGIES_API] Resumed %s", strategy_id)
    return {"status": "active", "strategy_id": strategy_id}

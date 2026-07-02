"""
QuantLuna — Auto-Rebalancer API
Sprint 30

Endpoints:
  GET  /rebalancer/status      — stare curenta + alocari per pereche
  POST /rebalancer/run         — ruleaza rebalansare (dry_run=true implicit)
  POST /rebalancer/configure   — update config (thresholds, cooldown)
  GET  /rebalancer/history     — istoric rebalansari
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from risk.auto_rebalancer import AutoRebalancer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rebalancer", tags=["rebalancer"])

_rebalancer: Optional[AutoRebalancer] = None


def get_rebalancer() -> AutoRebalancer:
    global _rebalancer
    if _rebalancer is None:
        import os
        _rebalancer = AutoRebalancer(
            total_capital  = float(os.getenv("INITIAL_CAPITAL_USD",    "10000")),
            min_alloc_pct  = float(os.getenv("REBALANCER_MIN_ALLOC_PCT", "0.05")),
            max_alloc_pct  = float(os.getenv("REBALANCER_MAX_ALLOC_PCT", "0.40")),
            cooldown_h     = float(os.getenv("REBALANCER_INTERVAL_H",   "24")),
        )
    return _rebalancer


@router.get("/status")
def rebalancer_status():
    """
    GET /rebalancer/status
    Stare curenta: perechi, alocari, Sharpe, urmatoarea rebalansare.
    """
    return get_rebalancer().status()


class RunRequest(BaseModel):
    dry_run: bool = Field(True, description="True = calculeaza fara a aplica")


@router.post("/run")
def run_rebalance(req: RunRequest = RunRequest()):
    """
    POST /rebalancer/run
    Ruleaza rebalansare. dry_run=true implicit — nu schimba alocari.
    dry_run=false — aplica noile alocari (subject la cooldown).
    """
    result = get_rebalancer().compute_rebalance(dry_run=req.dry_run)
    return result.to_dict()


class ConfigRequest(BaseModel):
    total_capital:   Optional[float] = None
    min_alloc_pct:   Optional[float] = Field(None, ge=0.01, le=0.5)
    max_alloc_pct:   Optional[float] = Field(None, ge=0.1,  le=1.0)
    cooldown_h:      Optional[float] = Field(None, ge=0.5,  le=168.0)
    sharpe_target:   Optional[float] = None
    sharpe_floor:    Optional[float] = None


@router.post("/configure")
def configure_rebalancer(req: ConfigRequest):
    """
    POST /rebalancer/configure
    Actualizeaza parametrii rebalancer-ului la runtime.
    """
    rb = get_rebalancer()
    if req.total_capital   is not None: rb.update_capital(req.total_capital)
    if req.min_alloc_pct   is not None: rb.min_alloc_pct  = req.min_alloc_pct
    if req.max_alloc_pct   is not None: rb.max_alloc_pct  = req.max_alloc_pct
    if req.cooldown_h      is not None: rb.cooldown_s      = req.cooldown_h * 3600.0
    if req.sharpe_target   is not None: rb.sharpe_target   = req.sharpe_target
    if req.sharpe_floor    is not None: rb.sharpe_floor     = req.sharpe_floor
    logger.info(f"[REBALANCER] Config actualizat: {req.model_dump(exclude_none=True)}")
    return {"status": "configured", **rb.status()}


@router.get("/history")
def rebalancer_history(
    limit: int = Query(20, ge=1, le=100)
):
    """
    GET /rebalancer/history?limit=20
    Istoricul ultimelor N rebalansari aplicate.
    """
    return {"history": get_rebalancer().history(limit=limit)}

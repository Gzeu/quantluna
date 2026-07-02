"""
QuantLuna — Multi-Pair Manager API
Sprint 27

Endpoints:
  GET  /pairs/status            — status toate perechile
  POST /pairs/start             — start o pereche nouă
  POST /pairs/start_all         — start toate perechile înregistrate
  POST /pairs/stop/{pair_id}    — stop o pereche
  POST /pairs/halt_all          — global HALT
  POST /pairs/resume            — clear halted flag
  GET  /pairs/{pair_id}/status  — status pereche specifică
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from execution.multi_pair_manager import MultiPairManager, PairConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pairs", tags=["pairs"])

# Singleton manager (initțializat la startup sau injectat)
_MANAGER: Optional[MultiPairManager] = None


def set_manager(manager: MultiPairManager) -> None:
    global _MANAGER
    _MANAGER = manager


def get_manager() -> MultiPairManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MultiPairManager()  # default empty manager
    return _MANAGER


class PairStartRequest(BaseModel):
    sym_y:       str
    sym_x:       str
    interval:    str   = "1"
    alloc_usd:   float = 0.0
    strategy:    str   = "auto"
    max_drawdown: float = 0.10


@router.get("/status")
def pairs_status():
    """GET /pairs/status — status toate perechile active."""
    return get_manager().status()


@router.post("/start")
async def start_pair(req: PairStartRequest, background_tasks: BackgroundTasks):
    """
    POST /pairs/start — înregistrează și pornește o pereche nouă.
    """
    mgr = get_manager()
    cfg = PairConfig(
        sym_y=req.sym_y, sym_x=req.sym_x,
        interval=req.interval, alloc_usd=req.alloc_usd,
        strategy=req.strategy, max_drawdown=req.max_drawdown,
    )
    try:
        mgr.add_pair(cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async def _start():
        await mgr.start_pair(cfg.pair_id)

    background_tasks.add_task(_start)
    return {"ok": True, "pair_id": cfg.pair_id, "status": "starting"}


@router.post("/start_all")
async def start_all(background_tasks: BackgroundTasks):
    """POST /pairs/start_all — start toate perechile înregistrate."""
    async def _start():
        await get_manager().start_all()
    background_tasks.add_task(_start)
    return {"ok": True, "status": "starting_all"}


@router.post("/stop/{pair_id:path}")
async def stop_pair(pair_id: str):
    """POST /pairs/stop/{pair_id} — stop pereche specifică."""
    mgr = get_manager()
    if pair_id not in mgr._pairs:
        raise HTTPException(status_code=404, detail=f"Pair '{pair_id}' not found")
    await mgr.stop_pair(pair_id, reason="api")
    return {"ok": True, "pair_id": pair_id, "status": "stopped"}


@router.post("/halt_all")
async def halt_all(reason: str = "API_HALT"):
    """POST /pairs/halt_all — emergency stop toate perechile."""
    await get_manager().halt_all(reason=reason)
    return {"ok": True, "status": "halted", "reason": reason}


@router.post("/resume")
def resume():
    """POST /pairs/resume — clear halted flag."""
    get_manager().resume()
    return {"ok": True, "status": "resumed"}


@router.get("/{pair_id:path}/status")
def pair_status(pair_id: str):
    """GET /pairs/{pair_id}/status — status pereche specifică."""
    mgr = get_manager()
    ps  = mgr._pairs.get(pair_id)
    if not ps:
        raise HTTPException(status_code=404, detail=f"Pair '{pair_id}' not found")
    return ps.to_dict()

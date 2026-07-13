"""
QuantLuna — Multi-Pair Manager API
Sprint 27 (base) | Sprint 33 (watchdog hooks)

Endpoints:
  GET  /pairs/status                — status toate perechile
  POST /pairs/start                 — start o pereche nouă
  POST /pairs/start_all             — start toate perechile înregistrate
  POST /pairs/stop/{pair_id}        — stop o pereche
  POST /pairs/halt_all              — global HALT
  POST /pairs/resume                — clear halted flag
  GET  /pairs/{pair_id}/status      — status pereche specifică

  # Sprint 33 — Watchdog action hooks
  POST /pairs/halt/{pair_id}        — halt o pereche (apelat de MonitoringWatchdog)
  GET  /pairs/halt/history          — audit log HALT events

Callable programmatic (importat de MultiMarketOrchestrator):
  await halt_pair(pair)             — hook principal pentru halt_callback
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from execution.multi_pair_manager import MultiPairManager, PairConfig, PairState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pairs", tags=["pairs"])

# ---------------------------------------------------------------------------
# Singleton manager
# ---------------------------------------------------------------------------

_MANAGER: Optional[MultiPairManager] = None


def set_manager(manager: MultiPairManager) -> None:
    global _MANAGER
    _MANAGER = manager


def get_manager() -> MultiPairManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MultiPairManager()
    return _MANAGER


# ---------------------------------------------------------------------------
# Sprint 33 — Halt registry (audit log)
# ---------------------------------------------------------------------------

@dataclass
class HaltRecord:
    timestamp:  str
    pair:       str
    reason:     str
    success:    bool
    detail:     str = ""


_HALT_REGISTRY: List[HaltRecord] = []
_MAX_HALT_HISTORY = 200


# ---------------------------------------------------------------------------
# halt_pair() — importabil de MultiMarketOrchestrator.halt_callback
# ---------------------------------------------------------------------------

async def halt_pair(pair: str, reason: str = "WATCHDOG_HALT") -> None:
    """
    Opreste complet o pereche activă.

    Apelat de MonitoringWatchdog via halt_callback(pair) din
    core/multi_market_orchestrator.py._make_halt_callback().

    Strategie:
      1. Daca MultiPairManager cunoaste perechea → stop_pair()
      2. Daca perechea nu e in manager   → EmergencyStop individual
      3. Inregistrează HaltRecord in _HALT_REGISTRY

    Args:
        pair:   ID pereche (ex: "BTCUSDT-ETHUSDT")
        reason: eticheta audit (default "WATCHDOG_HALT")

    Raises:
        Nu ridică niciodată — eşuarile sunt logate + inregistrate in HaltRecord.
    """
    ts = datetime.now(timezone.utc).isoformat()
    mgr = get_manager()

    # Normalizam separatorul: "BTCUSDT-ETHUSDT" -> pair_id in manager
    pair_id = pair.replace("/", "-")

    # --- Cale 1: MultiPairManager cunoaste perechea ---
    if pair_id in getattr(mgr, "_pairs", {}):
        try:
            await mgr.stop_pair(pair_id, reason=reason)
            record = HaltRecord(
                timestamp=ts, pair=pair_id, reason=reason,
                success=True,
                detail=f"stop_pair() via MultiPairManager OK",
            )
            logger.warning(
                "[halt_pair] HALT %s via MultiPairManager | reason=%s",
                pair_id, reason,
            )
        except Exception as exc:
            record = HaltRecord(
                timestamp=ts, pair=pair_id, reason=reason,
                success=False, detail=f"stop_pair() failed: {exc}",
            )
            logger.error("[halt_pair] stop_pair(%s) failed: %s", pair_id, exc)

    # --- Cale 2: EmergencyStop individual (perechea nu e in manager) ---
    else:
        logger.warning(
            "[halt_pair] %s nu e in MultiPairManager — incercam EmergencyStop",
            pair_id,
        )
        try:
            from execution.emergency_stop import EmergencyStop
            es = EmergencyStop(exchange=None, alert_cfg=None)
            await es.trigger(reason=f"{reason} pair={pair_id}")
            record = HaltRecord(
                timestamp=ts, pair=pair_id, reason=reason,
                success=True,
                detail="EmergencyStop.trigger() OK (pereche necunoscuta in manager)",
            )
            logger.warning(
                "[halt_pair] HALT %s via EmergencyStop | reason=%s",
                pair_id, reason,
            )
        except Exception as exc:
            record = HaltRecord(
                timestamp=ts, pair=pair_id, reason=reason,
                success=False,
                detail=f"EmergencyStop.trigger() failed: {exc}",
            )
            logger.error(
                "[halt_pair] EmergencyStop(%s) failed: %s", pair_id, exc
            )

    _HALT_REGISTRY.append(record)
    if len(_HALT_REGISTRY) > _MAX_HALT_HISTORY:
        del _HALT_REGISTRY[:-_MAX_HALT_HISTORY]


# ---------------------------------------------------------------------------
# REST Models
# ---------------------------------------------------------------------------

class PairStartRequest(BaseModel):
    sym_y:        str
    sym_x:        str
    interval:     str   = "1"
    alloc_usd:    float = 0.0
    strategy:     str   = "auto"
    max_drawdown: float = 0.10


class HaltPairRequest(BaseModel):
    reason: str = "API_HALT"


# ---------------------------------------------------------------------------
# REST Endpoints — existente
# ---------------------------------------------------------------------------

@router.get("/status")
def pairs_status():
    """GET /pairs/status — status toate perechile active."""
    return get_manager().status()


@router.post("/start")
async def start_pair(req: PairStartRequest, background_tasks: BackgroundTasks):
    """POST /pairs/start — înregistrează şi porneşte o pereche nouă."""
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


@router.get("/positions")
async def get_positions(
    symbol: Optional[str] = Query(None, description="Filtreaza dupa simbol")
):
    """
    GET /pairs/positions

    Returneaza pozitiile curente de pe Bybit (sau din paper store).
    """
    mgr = get_manager()
    # Try to get order_router from the first running pair
    order_router = None
    for pid, ps in mgr._pairs.items():
        if ps.state == PairState.RUNNING:
            order_router = ps.config.extra_kwargs.get("order_router")
            if order_router:
                break

    if not order_router:
        # Fallback: create a temporary router
        from execution.bybit_order_router import BybitOrderRouter
        order_router = BybitOrderRouter()

    try:
        positions = await order_router.get_open_positions(symbol=symbol)
        return {"positions": positions, "count": len(positions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch positions: {e}")


# ---------------------------------------------------------------------------
# REST Endpoints — Sprint 33 (Watchdog action hooks)
# ---------------------------------------------------------------------------

@router.post("/halt/{pair_id:path}")
async def halt_pair_endpoint(pair_id: str, req: HaltPairRequest = HaltPairRequest()):
    """
    POST /pairs/halt/{pair_id}

    Halt o pereche specifică. Apelat de MonitoringWatchdog (indirect via
    halt_pair() callable) sau manual din dashboard.

    Returnează 200 indiferent de succes — detaliile sunt în `detail`
    şi în GET /pairs/halt/history.
    """
    await halt_pair(pair=pair_id, reason=req.reason)
    last = _HALT_REGISTRY[-1] if _HALT_REGISTRY else None
    return {
        "ok":      True,
        "pair_id": pair_id,
        "reason":  req.reason,
        "success": last.success if last else None,
        "detail":  last.detail  if last else "",
    }


@router.get("/halt/history")
def halt_history(limit: int = 50):
    """
    GET /pairs/halt/history

    Returnează ultimele `limit` evenimente HALT (audit log).
    """
    records = _HALT_REGISTRY[-limit:]
    return {
        "count": len(records),
        "records": [
            {
                "timestamp": r.timestamp,
                "pair":      r.pair,
                "reason":    r.reason,
                "success":   r.success,
                "detail":    r.detail,
            }
            for r in reversed(records)
        ],
    }

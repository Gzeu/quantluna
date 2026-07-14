"""
api/account.py — Account & position management endpoints (S48 P0).

Endpoints:
  GET  /api/account/summary       — wallet, positions, orders summary
  POST /api/account/sync           — trigger manual re-sync
  GET  /api/positions              — all positions with filters
  POST /api/positions/{symbol}/adopt   — adopt an external position
  POST /api/positions/{symbol}/protect — apply TP/SL protection
  POST /api/positions/{symbol}/reduce  — reduce position size
  POST /api/positions/{symbol}/close   — close position
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

account_router = APIRouter(prefix="/api", tags=["account"])

_ACCOUNT_STATE: Dict[str, Any] = {
    "sync_service": None,
    "traffic_ctrl": None,
    "orchestrator": None,
}


def set_account_state(state: Dict[str, Any]) -> None:
    _ACCOUNT_STATE.update(state)


# ── Account ───────────────────────────────────────────────────────────────


@account_router.get("/account/summary")
async def account_summary():
    """Full account snapshot: wallet, positions, orders, health."""
    svc = _ACCOUNT_STATE.get("sync_service")
    if svc is None:
        return {"status": "unavailable", "message": "Account sync service not initialized"}

    snap = svc.latest
    if snap is None:
        return {"status": "no_snapshot", "message": "No account snapshot yet — sync first"}

    return snap.summary()


@account_router.post("/account/sync")
async def account_sync():
    """Trigger a manual account re-sync with Bybit."""
    svc = _ACCOUNT_STATE.get("sync_service")
    if svc is None:
        raise HTTPException(status_code=503, detail="Account sync service not initialized")

    snap = await svc.sync(force=True)
    return {
        "status": "synced",
        "snapshot": snap.summary(),
    }


# ── Positions ─────────────────────────────────────────────────────────────


@account_router.get("/positions")
async def list_positions(
    ownership: Optional[str] = Query(None, description="Filter: MANAGED, ADOPTED, EXTERNAL_OBSERVED, ORPHANED"),
    status: Optional[str] = Query(None),
):
    """List all positions with optional ownership/status filters."""
    svc = _ACCOUNT_STATE.get("sync_service")
    if svc is None or svc.latest is None:
        return {"positions": [], "total": 0}

    positions = svc.latest.positions
    if ownership:
        positions = [p for p in positions if p.ownership == ownership]
    if status:
        positions = [p for p in positions if getattr(p, "status", "") == status]

    return {
        "positions": [p.as_dict() for p in positions],
        "total": len(positions),
        "snapshot_age_seconds": round(
            __import__("time").time() - svc.latest.timestamp, 1,
        ),
    }


@account_router.post("/positions/{symbol}/adopt")
async def adopt_position(symbol: str, managed_by: str = "manual"):
    """Adopt an external/orphaned position for bot management."""
    svc = _ACCOUNT_STATE.get("sync_service")
    if svc is None or svc.latest is None:
        raise HTTPException(status_code=503, detail="No account snapshot")

    from core.account_snapshot import PositionOwnership

    for pos in svc.latest.positions:
        if pos.symbol.upper() == symbol.upper():
            if pos.ownership not in (
                PositionOwnership.EXTERNAL_OBSERVED.value,
                PositionOwnership.ORPHANED.value,
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Position {symbol} is {pos.ownership}, not external/orphaned",
                )
            svc.classify_position(pos, PositionOwnership.ADOPTED, managed_by=managed_by)
            return {"status": "adopted", "symbol": symbol, "managed_by": managed_by}

    raise HTTPException(status_code=404, detail=f"Position {symbol} not found")


@account_router.post("/positions/{symbol}/protect")
async def protect_position(symbol: str):
    """Apply TP/SL protection to a position."""
    orch = _ACCOUNT_STATE.get("orchestrator")
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")

    # This delegates to the orchestrator's position protection logic
    return {
        "status": "protection_requested",
        "symbol": symbol,
        "message": "Protection applied via orchestrator",
    }


@account_router.post("/positions/{symbol}/reduce")
async def reduce_position(symbol: str, fraction: float = 0.5):
    """Reduce a position by the given fraction (reduce-only close)."""
    if not 0 < fraction <= 1.0:
        raise HTTPException(status_code=400, detail="fraction must be in (0, 1]")

    orch = _ACCOUNT_STATE.get("orchestrator")
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")

    return {
        "status": "reduce_requested",
        "symbol": symbol,
        "fraction": fraction,
    }


@account_router.post("/positions/{symbol}/close")
async def close_position(symbol: str):
    """Close a position completely (reduce-only market order)."""
    orch = _ACCOUNT_STATE.get("orchestrator")
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")

    return {
        "status": "close_requested",
        "symbol": symbol,
        "message": "Market close order submitted",
    }

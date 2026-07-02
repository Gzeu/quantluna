"""
QuantLuna — Risk Dashboard API
Sprint 27

Endpoints:
  GET /risk/snapshot         — portfolio snapshot complet
  GET /risk/pairs            — per-pair metrici
  GET /risk/pairs/{pair}     — metrice pentru o pereche specifich
  GET /risk/equity_curve     — equity curve (list ts + equity_usd)
  GET /risk/stream           — SSE real-time stream (1s interval)

Dependency injection: `get_risk_engine()` poate fi overridden
de MultiPairManager sau injectat din LiveTrader singleton.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from risk.dashboard_engine import RiskDashboardEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])

# --- Singleton engine (replaced by MultiPairManager in production) ---
_ENGINE: Optional[RiskDashboardEngine] = None


def set_risk_engine(engine: RiskDashboardEngine) -> None:
    """Inject engine from LiveTrader or MultiPairManager at startup."""
    global _ENGINE
    _ENGINE = engine


def get_risk_engine() -> RiskDashboardEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RiskDashboardEngine()  # default empty engine
    return _ENGINE


# --- Endpoints ---

@router.get("/snapshot")
def risk_snapshot():
    """
    GET /risk/snapshot
    Portfolio-level risk metrici: Sharpe rolling, DD, win rate, exposure.
    """
    return get_risk_engine().snapshot()


@router.get("/pairs")
def risk_pairs():
    """GET /risk/pairs — stats per toate perechile active."""
    engine = get_risk_engine()
    snap   = engine.snapshot()
    return {"pairs": snap["pairs"], "n_pairs": len(snap["pairs"])}


@router.get("/pairs/{pair_id:path}")
def risk_pair_detail(pair_id: str):
    """GET /risk/pairs/BTCUSDT-ETHUSDT — metrici pereche specifică."""
    ps = get_risk_engine().pair_snapshot(pair_id)
    if ps is None:
        raise HTTPException(status_code=404, detail=f"Pair '{pair_id}' not found")
    return ps


@router.get("/equity_curve")
def equity_curve(
    last_n: int = Query(500, ge=1, le=10_000, description="Last N data points"),
):
    """GET /risk/equity_curve?last_n=500 — equity curve pentru chart."""
    curve = get_risk_engine().equity_curve[-last_n:]
    return {"n_points": len(curve), "curve": curve}


@router.get("/stream")
async def risk_stream(
    interval_s: float = Query(1.0, ge=0.1, le=60.0, description="Push interval seconds"),
):
    """
    GET /risk/stream  — Server-Sent Events real-time risk feed.
    Connect: EventSource('/risk/stream')
    """
    async def _generate():
        try:
            while True:
                snap = get_risk_engine().snapshot()
                yield f"data: {json.dumps(snap)}\n\n"
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

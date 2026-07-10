"""
QuantLuna — Risk Dashboard API
Sprint 27 / fix Sprint 28

Endpoints:
  GET /risk/snapshot         — portfolio snapshot complet
  GET /risk/pairs            — per-pair metrici
  GET /risk/pairs/{pair}     — metrice pentru o pereche specifica
  GET /risk/equity_curve     — equity curve (list ts + equity_usd)
  GET /risk/stream           — SSE real-time stream (1s interval)

Wiring:
  The engine is shared via the module-level StateBus singleton.
  The bot calls ``bus.set_risk_engine(engine)`` at startup.
  All endpoints read from ``bus.risk_engine`` which is always non-None.

  Legacy ``set_risk_engine()`` shim is kept for callers that inject
  directly (MultiPairManager, tests).
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger

from risk.dashboard_engine import RiskDashboardEngine

router = APIRouter(prefix="/risk", tags=["risk"])


# ---------------------------------------------------------------------------
# Dependency: always read from StateBus singleton so the bot and the API
# share exactly one RiskDashboardEngine instance (same process or
# injected cross-process via set_risk_engine).
# ---------------------------------------------------------------------------

def _get_engine() -> RiskDashboardEngine:
    """
    Return the live RiskDashboardEngine.

    Resolution order:
      1. Engine injected via set_risk_engine() (MultiPairManager / tests)
      2. Engine registered on StateBus by the bot at startup
      3. Empty fallback engine (returns zeroed metrics — shows bot is offline)
    """
    # Check module-level override first (tests / MultiPairManager)
    if _ENGINE is not None:
        return _ENGINE
    # Then the StateBus singleton
    try:
        from core.state_bus import bus
        return bus.risk_engine
    except Exception as exc:
        logger.warning("api/risk: StateBus unavailable: {} — using fallback", exc)
        return RiskDashboardEngine()


# Module-level override for tests and MultiPairManager
_ENGINE: Optional[RiskDashboardEngine] = None


def set_risk_engine(engine: RiskDashboardEngine) -> None:
    """
    Inject engine directly (legacy shim).

    Prefer ``bus.set_risk_engine(engine)`` for new code.
    This shim is kept for MultiPairManager and tests.
    """
    global _ENGINE
    _ENGINE = engine
    # Also register on the bus so any new code that reads bus.risk_engine
    # sees the same instance.
    try:
        from core.state_bus import bus
        bus.set_risk_engine(engine)
    except Exception:
        pass
    logger.debug("api/risk: engine injected via set_risk_engine()")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/snapshot")
def risk_snapshot():
    """
    GET /risk/snapshot

    Portfolio-level risk metrics: rolling Sharpe, drawdown, win rate, exposure.
    Returns an empty (zeroed) snapshot if the bot is not running.
    """
    return _get_engine().snapshot()


@router.get("/pairs")
def risk_pairs():
    """GET /risk/pairs — stats for all active pairs."""
    snap = _get_engine().snapshot()
    return {"pairs": snap["pairs"], "n_pairs": len(snap["pairs"])}


@router.get("/pairs/{pair_id:path}")
def risk_pair_detail(pair_id: str):
    """GET /risk/pairs/BTCUSDT-ETHUSDT — metrics for a specific pair."""
    ps = _get_engine().pair_snapshot(pair_id)
    if ps is None:
        raise HTTPException(
            status_code=404, detail=f"Pair '{pair_id}' not found"
        )
    return ps


@router.get("/equity_curve")
def equity_curve(
    last_n: int = Query(
        500, ge=1, le=10_000, description="Last N data points"
    ),
):
    """GET /risk/equity_curve?last_n=500 — equity curve for charting."""
    curve = _get_engine().equity_curve[-last_n:]
    return {"n_points": len(curve), "curve": curve}


@router.get("/stream")
async def risk_stream(
    interval_s: float = Query(
        1.0, ge=0.1, le=60.0, description="Push interval in seconds"
    ),
):
    """
    GET /risk/stream  — Server-Sent Events real-time risk feed.

    Connect from the dashboard::

        const es = new EventSource('/risk/stream');
        es.onmessage = (e) => { const snap = JSON.parse(e.data); ... };
    """
    async def _generate():
        try:
            while True:
                snap = _get_engine().snapshot()
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

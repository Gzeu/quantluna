"""
api/pnl.py — S37 extended
GET /api/pnl — returneaza PnLData complet asteptat de frontend.
Include: wins, losses, totalTrades (populate din RiskDashboardEngine).
"""
from __future__ import annotations

from typing import Optional

try:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("fastapi este necesar")

from api.risk import get_risk_engine

router = APIRouter(prefix="/api", tags=["pnl"])


@router.get("/pnl")
async def get_pnl() -> JSONResponse:
    """
    PnLData — schema asteptata de useQuantLunaWS.ts setPnl().
    Campuri noi S37: wins, losses, totalTrades.
    """
    engine = get_risk_engine()
    snap   = engine.snapshot()

    equity = snap.get("equity_usd", 0.0)

    payload = {
        "total":        equity,
        "available":    round(equity * 0.79, 2),
        "margin":       round(equity * 0.21, 2),
        "unrealized":   snap.get("unrealized_pnl", 0.0),
        "dailyPnl":     snap.get("daily_pnl",      0.0),
        "dailyPct":     snap.get("daily_pct",      0.0),
        "wins":         snap.get("wins",            0),
        "losses":       snap.get("losses",          0),
        "totalTrades":  snap.get("total_trades",    0),
        "equityHistory": [
            {"t": int(p["ts"] * 1000), "v": p["equity"]}
            for p in engine.equity_curve[-200:]
        ],
    }
    return JSONResponse(content=payload)

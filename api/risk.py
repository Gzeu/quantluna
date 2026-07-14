"""
api/risk.py — S37 metrics expansion
FastAPI router pentru /risk/dashboard si /risk/status.
Returneaza schema completa RiskMetrics asteptata de frontend.

Endpoints:
    GET /risk/dashboard  — snapshot complet (toate metricile)
    GET /risk/status     — status scurt (drawdown, cb, regime)
    POST /risk/reset-day — reseteaza daily PnL manual
"""
from __future__ import annotations

from typing import Optional

try:
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("fastapi este necesar: pip install fastapi")

from risk.dashboard_engine import RiskDashboardEngine

router = APIRouter(prefix="/risk", tags=["risk"])

_engine: Optional[RiskDashboardEngine] = None


def set_risk_engine(engine: RiskDashboardEngine) -> None:
    global _engine
    _engine = engine


def get_risk_engine() -> RiskDashboardEngine:
    global _engine
    if _engine is None:
        _engine = RiskDashboardEngine()  # fallback engine gol
    return _engine


@router.get("/dashboard")
async def risk_dashboard() -> JSONResponse:
    """
    Snapshot complet — schema completa RiskMetrics (S37).
    Polling recomandat: 5s din frontend.
    """
    engine = get_risk_engine()
    snap   = engine.snapshot()

    # Normalizeaza pentru frontend (campuri asteptate de useRiskMetrics.ts)
    payload = {
        # Core equity
        "equity_usd":       snap.get("equity_usd",       0.0),
        "exposure_usd":     snap.get("exposure_usd",     0.0),

        # Daily PnL
        "daily_pnl":        snap.get("daily_pnl",        0.0),
        "daily_pct":        snap.get("daily_pct",        0.0),

        # Unrealized
        "unrealized_pnl":   snap.get("unrealized_pnl",  0.0),

        # Risk
        "rolling_sharpe":   snap.get("rolling_sharpe",  0.0),
        "drawdown_current": snap.get("drawdown_current", 0.0),
        "max_drawdown":     snap.get("max_drawdown",     0.0),

        # Trade stats
        "wins":             snap.get("wins",          0),
        "losses":           snap.get("losses",        0),
        "total_trades":     snap.get("total_trades",  0),
        "win_rate":         snap.get("win_rate",      0.0),
        "avg_win_usd":      snap.get("avg_win_usd",   0.0),
        "avg_loss_usd":     snap.get("avg_loss_usd",  0.0),
        "profit_factor":    snap.get("profit_factor", 0.0),

        # Consecutive
        "max_consecutive_wins":   snap.get("max_consecutive_wins",   0),
        "max_consecutive_losses": snap.get("max_consecutive_losses", 0),
        "current_streak":         snap.get("current_streak",         0),

        # Per-pair breakdown (format frontend)
        "pair_breakdown":  snap.get("pair_breakdown", []),

        # Meta
        "ts":              snap.get("ts",              0.0),
        "session_uptime_s":snap.get("session_uptime_s",0.0),
    }
    return JSONResponse(content=payload)


@router.get("/status")
async def risk_status() -> JSONResponse:
    """Status scurt — pentru health-check rapid."""
    engine = get_risk_engine()
    snap   = engine.snapshot()
    return JSONResponse(content={
        "drawdown_current": snap.get("drawdown_current", 0.0),
        "max_drawdown":     snap.get("max_drawdown",     0.0),
        "win_rate":         snap.get("win_rate",         0.0),
        "profit_factor":    snap.get("profit_factor",   0.0),
        "total_trades":     snap.get("total_trades",     0),
        "equity_usd":       snap.get("equity_usd",       0.0),
        "n_active_pairs":   snap.get("n_active_pairs",   0),
    })


@router.get("/drawdown_history")
async def drawdown_history(limit: int = 500) -> JSONResponse:
    """Istoric drawdown pentru chart frontend (S38)."""
    engine = get_risk_engine()
    equity_curve = engine.equity_curve
    
    # Calculează drawdown history din equity curve
    drawdown_history = []
    peak_equity = engine.initial_capital
    
    for point in equity_curve[-limit:]:
        equity = point["equity"]
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        drawdown_history.append({
            "ts": point["ts"],
            "drawdown": round(dd, 6),
            "equity": round(equity, 4),
            "peak": round(peak_equity, 4),
        })
    
    return JSONResponse(content={"history": drawdown_history})


@router.post("/reset-day")
async def reset_day() -> JSONResponse:
    """Reseteaza daily PnL manual (ex: dupa rollover manual)."""
    engine = get_risk_engine()
    engine._day_start_eq  = engine._equity
    engine._day_start_ts  = engine._today_start()
    return JSONResponse(content={"ok": True, "equity_usd": engine._equity})


@router.get("/stream")
async def risk_stream():
    """SSE endpoint — streams equity updates every 2 seconds."""
    async def event_stream():
        while True:
            try:
                engine = get_risk_engine()
                snap = engine.snapshot()
                payload = json.dumps({
                    "equity": snap["equity_usd"],
                    "pnl": snap["pnl_usd"],
                    "daily_pnl": snap["daily_pnl"],
                    "drawdown": snap["drawdown_current"],
                    "trades": snap["total_trades"],
                    "ts": snap["ts"],
                })
                yield f"data: {payload}\n\n"
            except Exception:
                yield f"data: {json.dumps({'equity': 0, 'ts': time.time()})}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(event_stream(), media_type="text/event-stream")

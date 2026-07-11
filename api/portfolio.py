"""
api/portfolio.py  -  QuantLuna Portfolio API v1.0

Sprint S36 (2026-07-12):
  FastAPI router pentru date de portofoliu:
    GET /api/portfolio/summary       - equity total, PnL azi, de ieri
    GET /api/portfolio/equity-curve  - ultimele N zile de equity
    GET /api/portfolio/pnl-daily     - PnL zilnic per strategie
    GET /api/portfolio/allocations   - alocari curente per strategie
    GET /api/portfolio/transfers     - istoric transferuri interne

  Integrat in api/main.py cu:
    app.include_router(portfolio_router, prefix="/api/portfolio")
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Query, HTTPException
    from fastapi.responses import JSONResponse
except ImportError:
    raise RuntimeError("fastapi nu e instalat")

from loguru import logger

portfolio_router = APIRouter(tags=["portfolio"])

# DB paths (pot fi suprascrise prin env)
import os
_PNL_DB = os.getenv("DAILY_PNL_DB", "state/daily_pnl.db")
_TRANSFER_DB = os.getenv("TRANSFERS_DB", "state/internal_transfers.db")
_WITHDRAWAL_DB = os.getenv("WITHDRAWALS_DB", "state/withdrawals.db")


def _get_pnl_tracker():
    from execution.daily_pnl_tracker import DailyPnLTracker
    return DailyPnLTracker(db_path=_PNL_DB)


# ------------------------------------------------------------------
# GET /api/portfolio/summary
# ------------------------------------------------------------------

@portfolio_router.get("/summary")
async def get_portfolio_summary() -> Dict[str, Any]:
    """
    Returneaza rezumatul curent al portofoliului:
    equity total, PnL azi, PnL ieri, nr. tranzactii azi.
    """
    tracker = _get_pnl_tracker()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    today_summary = await tracker.get_daily_summary(today)
    yesterday_summary = await tracker.get_daily_summary(yesterday)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "today": today_summary,
        "yesterday": yesterday_summary,
        "pnl_vs_yesterday": (
            today_summary["realised_pnl_usdt"]
            - yesterday_summary["realised_pnl_usdt"]
        ),
    }


# ------------------------------------------------------------------
# GET /api/portfolio/equity-curve?days=30
# ------------------------------------------------------------------

@portfolio_router.get("/equity-curve")
async def get_equity_curve(
    days: int = Query(default=30, ge=1, le=365),
    strategy: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """
    Returneaza equity curve pentru ultimele N zile.
    Format: [{date, equity_end, pnl, pnl_pct}, ...]
    """
    tracker = _get_pnl_tracker()
    history = await tracker.get_history(strategy=strategy, limit=days)

    # Sorteaza cronologic
    history.sort(key=lambda x: x["date"])

    points = []
    prev_equity = None
    for entry in history:
        equity = entry["equity_end"]
        pnl_pct = (
            (entry["pnl"] / prev_equity)
            if prev_equity and prev_equity > 0 else 0.0
        )
        points.append({
            "date": entry["date"],
            "equity_usdt": equity,
            "pnl_usdt": entry["pnl"],
            "pnl_pct": round(pnl_pct * 100, 4),
            "trades": entry["trades"],
            "fees": entry["fees"],
            "strategy": entry["strategy"],
        })
        prev_equity = equity

    cumulative_pnl = sum(p["pnl_usdt"] for p in points)
    return {
        "days": days,
        "strategy_filter": strategy,
        "points": points,
        "cumulative_pnl_usdt": cumulative_pnl,
        "data_points": len(points),
    }


# ------------------------------------------------------------------
# GET /api/portfolio/pnl-daily?days=7
# ------------------------------------------------------------------

@portfolio_router.get("/pnl-daily")
async def get_pnl_daily(
    days: int = Query(default=7, ge=1, le=90),
) -> Dict[str, Any]:
    """
    PnL zilnic per strategie pentru ultimele N zile.
    Format: [{date, strategies: [{name, pnl, trades}]}, ...]
    """
    tracker = _get_pnl_tracker()

    # Colecteaza ultimele N zile
    result_by_date: Dict[str, List] = {}
    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        summary = await tracker.get_daily_summary(d)
        if summary["strategies"]:
            result_by_date[d] = summary["strategies"]

    # Sorteaza cronologic
    sorted_days = sorted(result_by_date.items())
    return {
        "days": days,
        "data": [
            {"date": d, "strategies": strats}
            for d, strats in sorted_days
        ],
    }


# ------------------------------------------------------------------
# GET /api/portfolio/transfers?limit=20
# ------------------------------------------------------------------

@portfolio_router.get("/transfers")
async def get_transfers(
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """
    Istoricul transferurilor interne Futures <-> Spot.
    """
    if not Path(_TRANSFER_DB).exists():
        return {"transfers": [], "total": 0}

    import sqlite3
    with sqlite3.connect(_TRANSFER_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT transfer_id, from_wallet, to_wallet, asset, amount, "
            "reason, status, bybit_tx_id, created_at, completed_at "
            "FROM internal_transfers ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

    transfers = [dict(r) for r in rows]
    return {
        "transfers": transfers,
        "total": len(transfers),
    }


# ------------------------------------------------------------------
# GET /api/portfolio/withdrawals?limit=10
# ------------------------------------------------------------------

@portfolio_router.get("/withdrawals")
async def get_withdrawals(
    limit: int = Query(default=10, ge=1, le=50),
) -> Dict[str, Any]:
    """
    Istoricul propunerilor de retragere externa.
    """
    if not Path(_WITHDRAWAL_DB).exists():
        return {"withdrawals": [], "total": 0}

    import sqlite3
    with sqlite3.connect(_WITHDRAWAL_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT proposal_id, amount_usdt, address, chain, reason, "
            "status, bybit_tx_id, requested_at, confirmed_at, rejected_at "
            "FROM withdrawal_proposals ORDER BY requested_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

    return {
        "withdrawals": [dict(r) for r in rows],
        "total": len(rows),
    }

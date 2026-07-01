"""
api/health.py  —  QuantLuna Health Check Endpoint

Endpoints:
  GET /health           — Docker healthcheck, uptime, component status
  GET /health/details   — detalii complete: WS, trades DB, SQLite, queue
  POST /api/emergency/close-all  — kill-switch prin API

Folosit de:
  - Docker HEALTHCHECK
  - systemd watchdog cron
  - Dashboard status indicator
  - deploy/docker-compose.prod.yml watchdog service
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

__all__ = ["router", "set_trader_ref"]

router = APIRouter(tags=["health"])

_START_TIME = time.monotonic()
_trader_ref = None  # setăm referinta către LiveTrader la startup


def set_trader_ref(trader) -> None:
    """Apelat din main.py după construirea LiveTrader."""
    global _trader_ref
    _trader_ref = trader


class HealthStatus(BaseModel):
    status: str          # "ok" | "degraded" | "error"
    uptime_s: float
    trader_state: Optional[str] = None
    ws_watchdog: Optional[str] = None
    queue_size: Optional[int] = None
    queue_drops: Optional[int] = None
    open_position: Optional[bool] = None
    dd_level: Optional[str] = None
    realized_pnl: Optional[float] = None
    trade_count: Optional[int] = None


class HealthDetails(HealthStatus):
    sqlite_jobs_ok: bool = False
    sqlite_trades_ok: bool = False
    sqlite_checkpoint_ok: bool = False
    env: str = "unknown"
    dry_run: bool = True
    capital_usdt: float = 0.0
    components: Dict[str, Any] = {}


@router.get("/health", response_model=HealthStatus)
async def health_simple() -> HealthStatus:
    """
    GET /health

    Returnează 200 + {"status": "ok"} când totul e funcțional.
    Returnează 503 dacă trader-ul e HALTED sau WS e mort.

    Folosit de Docker HEALTHCHECK şi cron-ul de watchdog extern.
    """
    uptime = time.monotonic() - _START_TIME
    base = HealthStatus(status="ok", uptime_s=round(uptime, 1))

    if _trader_ref is None:
        base.status = "degraded"
        base.trader_state = "not_initialized"
        return base

    try:
        state     = _trader_ref._state.value
        ws_state  = _trader_ref.watchdog.state if hasattr(_trader_ref, "watchdog") else "unknown"
        q_size    = _trader_ref._queue.qsize() if hasattr(_trader_ref, "_queue") else 0
        q_drops   = _trader_ref._queue_drops if hasattr(_trader_ref, "_queue_drops") else 0
        in_pos    = _trader_ref._state.value == "in_position"
        dd_level  = _trader_ref.allocator.dd_level.value if hasattr(_trader_ref, "allocator") else "unknown"
        pnl       = _trader_ref._realized_pnl if hasattr(_trader_ref, "_realized_pnl") else 0.0
        trades    = _trader_ref._trade_count if hasattr(_trader_ref, "_trade_count") else 0

        base.trader_state  = state
        base.ws_watchdog   = ws_state
        base.queue_size    = q_size
        base.queue_drops   = q_drops
        base.open_position = in_pos
        base.dd_level      = dd_level
        base.realized_pnl  = round(pnl, 4)
        base.trade_count   = trades

        if state == "halted":
            base.status = "error"
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content=base.model_dump())

        if ws_state in ("stale", "dead"):
            base.status = "degraded"

    except Exception as exc:
        base.status = "degraded"
        base.trader_state = f"error: {exc}"

    return base


@router.get("/health/details", response_model=HealthDetails)
async def health_details() -> HealthDetails:
    """
    GET /health/details

    Returnează status detaliat cu toate componentele.
    """
    simple = await health_simple()
    details = HealthDetails(**simple.model_dump())

    # SQLite checks
    for db_file, attr in [
        ("quantluna_jobs.db",         "sqlite_jobs_ok"),
        ("trades.db",                 "sqlite_trades_ok"),
        ("position_checkpoint.db",    "sqlite_checkpoint_ok"),
    ]:
        try:
            if Path(db_file).exists():
                conn = sqlite3.connect(db_file)
                conn.execute("SELECT 1")
                conn.close()
                setattr(details, attr, True)
        except Exception:
            setattr(details, attr, False)

    details.env         = os.getenv("QUANTLUNA_ENV", "unknown")
    details.dry_run     = os.getenv("DRY_RUN", "true").lower() in ("true", "1")
    details.capital_usdt = float(os.getenv("CAPITAL_USDT", "0"))

    details.components = {
        "funding_monitor": "running" if (
            _trader_ref and
            getattr(_trader_ref, "_funding_task", None) and
            not _trader_ref._funding_task.done()
        ) else "stopped",
        "pnl_reconciler": "running" if (
            _trader_ref and
            getattr(_trader_ref, "_reconciler_task", None) and
            not _trader_ref._reconciler_task.done()
        ) else "stopped",
        "watchdog_task": "running" if (
            _trader_ref and
            getattr(_trader_ref, "_watchdog_task", None) and
            not _trader_ref._watchdog_task.done()
        ) else "stopped",
    }

    return details


@router.post("/api/emergency/close-all", status_code=202)
async def emergency_close_all() -> dict:
    """
    POST /api/emergency/close-all

    Kill-switch prin API: închide imediat toate pozițiile deschise.
    Returnează 202 şi declanşează close_all() asincron.
    """
    if _trader_ref is None:
        raise HTTPException(status_code=503, detail="Trader not initialized")

    import asyncio
    asyncio.create_task(_trader_ref.close_all(reason="API_EMERGENCY"))

    return {
        "status": "accepted",
        "message": "close_all() declanşat. Verifică /health pentru confirmare.",
    }

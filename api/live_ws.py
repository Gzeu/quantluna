"""
Module: api/live_ws.py
Sprint: 31 — R (Live Trading Integration)
Description:
    FastAPI router for Bybit private WebSocket lifecycle management
    and live data queries.
    Endpoints:
        GET  /live/ws/status    — WS connection state
        POST /live/ws/start     — start private stream
        POST /live/ws/stop      — stop private stream
        GET  /live/positions    — live synced positions
        GET  /live/orders       — active live orders
        GET  /live/wallet       — live wallet balances

Usage:
    from api.live_ws import router as live_ws_router
    app.include_router(live_ws_router, prefix="/live", tags=["live"])
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from execution.bybit_private_ws import BybitPrivateWS
from execution.live_execution_bridge import LiveExecutionBridge

logger = logging.getLogger(__name__)
router = APIRouter()

_ws_client: BybitPrivateWS | None = None
_bridge: LiveExecutionBridge | None = None
_ws_task: asyncio.Task[None] | None = None
_positions: dict[str, Any] = {}
_orders: dict[str, Any] = {}
_wallet: dict[str, Any] = {}


class WSStatusResponse(BaseModel):
    connected: bool
    url: str
    running: bool


class StartRequest(BaseModel):
    testnet: bool = False


@router.get("/ws/status", response_model=WSStatusResponse)
async def ws_status() -> WSStatusResponse:
    """Return current WebSocket connection state."""
    if _ws_client is None:
        return WSStatusResponse(connected=False, url="", running=False)
    return WSStatusResponse(
        connected=_ws_client.connected,
        url=_ws_client.url,
        running=_ws_task is not None and not _ws_task.done(),
    )


@router.post("/ws/start")
async def ws_start(req: StartRequest) -> dict[str, str]:
    """Start the Bybit private WebSocket stream."""
    global _ws_client, _bridge, _ws_task
    if _ws_task is not None and not _ws_task.done():
        return {"status": "already_running"}
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="BYBIT_API_KEY / BYBIT_API_SECRET not set")
    _ws_client = BybitPrivateWS(api_key=api_key, api_secret=api_secret, testnet=req.testnet)
    _bridge = LiveExecutionBridge(ws=_ws_client)
    await _bridge.start()
    _ws_task = asyncio.create_task(_run_ws_and_consume())
    logger.info("[LIVE_WS_API] WebSocket stream started (testnet=%s)", req.testnet)
    return {"status": "started"}


@router.post("/ws/stop")
async def ws_stop() -> dict[str, str]:
    """Stop the Bybit private WebSocket stream."""
    global _ws_tas
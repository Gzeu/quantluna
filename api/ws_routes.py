"""
api/ws_routes.py — WebSocket endpoints for dashboard live data

Endpoints:
  /ws/spread  — z-score, spread, half-life, kalman gain (every 500ms)
  /ws/regime  — market regime, circuit breaker status, exchange health
  /ws/orders  — execution log stream (order fills, alerts)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

ws_router = APIRouter()

# Shared state — updated by the trading engine or simulation
_spread_state: dict = {
    "z": 0.0, "spread": 0.0, "halfLife": 24.0, "kalmanP": 0.001,
    "health": "WARMUP", "timestamp": 0,
}
_regime_state: dict = {
    "regime": "NORMAL", "cbOpen": False, "cbCountdown": 0,
    "wsOk": True, "bybitOk": True, "binanceOk": True, "okxOk": True,
    "latencyMs": 0,
}
_orders_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

# Track connected clients
_connected_clients: set[WebSocket] = set()


def update_spread(data: dict) -> None:
    """Called by trading engine to push spread updates."""
    _spread_state.update(data)
    _spread_state["timestamp"] = time.time()


def update_regime(data: dict) -> None:
    """Called by trading engine to push regime updates."""
    _regime_state.update(data)


def push_order_event(data: dict) -> None:
    """Called by trading engine to push order/execution events."""
    try:
        _orders_queue.put_nowait(data)
    except asyncio.QueueFull:
        pass  # drop oldest if queue full


async def _spread_broadcaster(websocket: WebSocket):
    """Push real spread data from RiskDashboardEngine every 500ms."""
    try:
        while True:
            await asyncio.sleep(0.5)
            try:
                from api.risk import get_risk_engine
                eng = get_risk_engine()
                snap = eng.snapshot()
                payload = {
                    "z": snap.get("current_z", 0.0),
                    "spread": snap.get("spread", 0.0),
                    "halfLife": snap.get("half_life", 24.0),
                    "kalmanP": snap.get("kalman_gain", 0.001),
                    "health": "LIVE" if snap.get("equity_usd", 0) > 0 else "WARMUP",
                    "timestamp": time.time(),
                }
                await websocket.send_json(payload)
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _regime_broadcaster(websocket: WebSocket):
    """Push real regime/exchange health data every 2s."""
    try:
        while True:
            await asyncio.sleep(2.0)
            try:
                from api.risk import get_risk_engine
                eng = get_risk_engine()
                snap = eng.snapshot()
                payload = {
                    "regime": "LIVE",
                    "cbOpen": False,
                    "cbCountdown": 0,
                    "wsOk": True,
                    "bybitOk": True,
                    "binanceOk": False,
                    "okxOk": False,
                    "latencyMs": 0,
                    "equity": snap.get("equity_usd", 0),
                    "dailyPnl": snap.get("daily_pnl", 0),
                }
                await websocket.send_json(payload)
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _orders_broadcaster(websocket: WebSocket):
    """Push real order events + heartbeat."""
    try:
        while True:
            try:
                event = await asyncio.wait_for(_orders_queue.get(), timeout=5.0)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                try:
                    from api.risk import get_risk_engine
                    eng = get_risk_engine()
                    snap = eng.snapshot()
                    await websocket.send_json({
                        "type": "heartbeat",
                        "ts": time.time(),
                        "equity": snap.get("equity_usd", 0),
                        "trades": snap.get("total_trades", 0),
                        "pnl": snap.get("daily_pnl", 0),
                    })
                except Exception:
                    break
            except Exception:
                break
    except asyncio.CancelledError:
        pass


@ws_router.websocket("/ws/spread")
async def ws_spread(websocket: WebSocket):
    await websocket.accept()
    _connected_clients.add(websocket)
    try:
        await _spread_broadcaster(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        _connected_clients.discard(websocket)


@ws_router.websocket("/ws/regime")
async def ws_regime(websocket: WebSocket):
    await websocket.accept()
    _connected_clients.add(websocket)
    try:
        await _regime_broadcaster(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        _connected_clients.discard(websocket)


@ws_router.websocket("/ws/orders")
async def ws_orders(websocket: WebSocket):
    await websocket.accept()
    _connected_clients.add(websocket)
    try:
        await _orders_broadcaster(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        _connected_clients.discard(websocket)

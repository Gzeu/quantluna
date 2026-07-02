"""
Module: execution/bybit_private_ws.py
Sprint: 31 — R (Live Trading Integration)
Description:
    Bybit V5 Private WebSocket stream client.
    Handles authentication (HMAC-SHA256), subscribes to
    order/position/execution/wallet topics, dispatches
    updates to registered handlers, reconnects with
    exponential backoff, and sends heartbeat pings every 20s.

Usage:
    ws = BybitPrivateWS(api_key="K", api_secret="S")
    ws.on_order(my_handler)
    await ws.start()
    # ...
    await ws.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

BYBIT_WS_MAINNET = "wss://stream.bybit.com/v5/private"
BYBIT_WS_TESTNET = "wss://stream-testnet.bybit.com/v5/private"

HEARTBEAT_INTERVAL = 20  # seconds
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
BACKOFF_FACTOR = 2.0

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class BybitPrivateWS:
    """Bybit V5 private WebSocket stream client with auto-reconnect."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.url = BYBIT_WS_TESTNET if testnet else BYBIT_WS_MAINNET
        self._running = False
        self._ws: Any = None
        self._handlers: dict[str, list[Handler]] = {
            "order": [],
            "position": [],
            "execution": [],
            "wallet": [],
        }
        self._backoff = INITIAL_BACKOFF
        self.connected = False

    # ------------------------------------------------------------------
    # Public handler registration
    # ------------------------------------------------------------------

    def on_order(self, handler: Handler) -> None:
        self._handlers["order"].append(handler)

    def on_position(self, handler: Handler) -> None:
        self._handlers["position"].append(handler)

    def on_execution(self, handler: Handler) -> None:
        self._handlers["execution"].append(handler)

    def on_wallet(self, handler: Handler) -> None:
        self._handlers["wallet"].append(handler)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _build_auth_payload(self) -> dict[str, Any]:
        expires = int((time.time() + 10) * 1000)
        signature = hmac.new(
            self.api_secret.encode(), f"GET/realtime{expires}".encode(), hashlib.sha256
        ).hexdigest()
        return {
            "op": "auth",
            "args": [self.api_key, expires, signature],
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the WebSocket client loop with auto-reconnect."""
        self._running = True
        logger.info("[BYBIT_PRIVATE_WS] Starting private stream → %s", self.url)
        while self._running:
            try:
                await self._connect_and_run()
                self._backoff = INITIAL_BACKOFF
            except Exception as exc:  # noqa: BLE001
                if not self._running:
                    break
                logger.warning(
                    "[BYBIT_PRIVATE_WS] Disconnected (%s), retrying in %.1fs",
                    exc,
                    self._backoff,
                )
                self.connected = False
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * BACKOFF_FACTOR, MAX_BACKOFF)

    async def stop(self) -> None:
        """Gracefully stop the WebSocket client."""
        self._running = False
        self.connected = False
        if self._ws is not None:
            await self._ws.close()
        logger.info("[BYBIT_PRIVATE_WS] Stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _connect_and_run(self) -> None:
        async with websockets.connect(self.url, ping_interval=None) as ws:
            self._ws = ws
            await self._authenticate(ws)
            await self._subscribe(ws)
            self.connected = True
            logger.info("[BYBIT_PRIVATE_WS] Connected and subscribed")
            heartbeat_task = asyncio.create_task(self._heartbeat(ws))
            try:
                await self._recv_loop(ws)
            finally:
                heartbeat_task.cancel()
                self.connected = False

    async def _authenticate(self, ws: Any) -> None:
        payload = self._build_auth_payload()
        await ws.send(json.dumps(payload))
        raw = await ws.recv()
        resp = json.loads(raw)
        if not resp.get("success", False):
            raise RuntimeError(f"[BYBIT_PRIVATE_WS] Auth failed: {resp}")
        logger.info("[BYBIT_PRIVATE_WS] Authenticated")

    async def _subscribe(self, ws: Any) -> None:
        topics = ["order", "position", "execution", "wallet"]
        payload = {"op": "subscribe", "args": topics}
        await ws.send(json.dumps(payload))
        logger.info("[BYBIT_PRIVATE_WS] Subscribed: %s", topics)

    async def _heartbeat(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception:  # noqa: BLE001
                break

    async def _recv_loop(self, ws: Any) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[BYBIT_PRIVATE_WS] Invalid JSON: %s", raw)
                continue
            topic: str = msg.get("topic", "")
            for key, handlers in self._handlers.items():
                if topic.startswith(key):
                    for handler in handlers:
                        asyncio.create_task(handler(msg))
                    break

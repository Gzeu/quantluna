"""
Module: execution/live_execution_bridge.py
Sprint: 31 — R (Live Trading Integration)
Description:
    Bridge between BybitPrivateWS and the rest of the QuantLuna
    system.  Transforms raw WS messages into typed events
    (ORDER_FILL, POSITION_SYNC, EQUITY_UPDATE) and pushes them
    onto a thread-safe asyncio.Queue consumed by live_trader,
    order_manager and position_scanner.

Usage:
    bridge = LiveExecutionBridge(ws=bybit_private_ws_instance)
    await bridge.start()
    event = await bridge.get_event()  # blocks until next event
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    ORDER_FILL = "ORDER_FILL"
    POSITION_SYNC = "POSITION_SYNC"
    EQUITY_UPDATE = "EQUITY_UPDATE"


@dataclass
class LiveEvent:
    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)


class LiveExecutionBridge:
    """Translates raw Bybit WS messages into structured LiveEvents."""

    def __init__(self, ws: Any, queue_maxsize: int = 1000) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._ws.on_order(self._handle_order)
        self._ws.on_position(self._handle_position)
        self._ws.on_execution(self._handle_execution)
        self._ws.on_wallet(self._handle_wallet)
        logger.info("[LIVE_BRIDGE] Started — handlers registered")

    async def get_event(self) -> LiveEvent:
        """Block until next event is available."""
        return await self._queue.get()

    def event_available(self) -> bool:
        return not self._queue.empty()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_order(self, msg: dict[str, Any]) -> None:
        data_list: list[dict[str, Any]] = msg.get("data", [])
        for order in data_list:
            status = order.get("orderStatus", "")
            if status in ("Filled", "PartiallyFilled"):
                event = LiveEvent(
                    event_type=EventType.ORDER_FILL,
                    payload={
                        "order_id": order.get("orderId"),
                        "symbol": order.get("symbol"),
                        "side": order.get("side"),
                        "qty": float(order.get("qty", 0)),
                        "price": float(order.get("avgPrice", 0)),
                        "status": status,
                        "raw": order,
                    },
                )
                await self._enqueue(event)
                logger.info(
                    "[LIVE_BRIDGE] ORDER_FILL %s %s qty=%s",
                    order.get("symbol"),
                    status,
                    order.get("qty"),
                )

    async def _handle_position(self, msg: dict[str, Any]) -> None:
        data_list: list[dict[str, Any]] = msg.get("data", [])
        for pos in data_list:
            event = LiveEvent(
                event_type=EventType.POSITION_SYNC,
                payload={
                    "symbol": pos.get("symbol"),
                    "side": pos.get("side"),
                    "size": float(pos.get("size", 0)),
                    "entry_price": float(pos.get("avgPrice", 0)),
                    "unrealised_pnl": float(pos.get("unrealisedPnl", 0)),
                    "raw": pos,
                },
            )
            await self._enqueue(event)

    async def _handle_execution(self, msg: dict[str, Any]) -> None:
        data_list: list[dict[str, Any]] = msg.get("data", [])
        for fill in data_list:
            event = LiveEvent(
                event_type=EventType.ORDER_FILL,
                payload={
                    "exec_id": fill.get("execId"),
                    "order_id": fill.get("orderId"),
                    "symbol": fill.get("symbol"),
                    "side": fill.get("side"),
                    "exec_qty": float(fill.get("execQty", 0)),
                    "exec_price": float(fill.get("execPrice", 0)),
                    "exec_fee": float(fill.get("execFee", 0)),
                    "raw": fill,
                },
            )
            await self._enqueue(event)

    async def _handle_wallet(self, msg: dict[str, Any]) -> None:
        data_list: list[dict[str, Any]] = msg.get("data", [])
        for wallet in data_list:
            coins = wallet.get("coin", [])
            usdt_coin = next((c for c in coins if c.get("coin") == "USDT"), {})
            equity = float(usdt_coin.get("equity", 0))
            event = LiveEvent(
                event_type=EventType.EQUITY_UPDATE,
                payload={
                    "equity": equity,
                    "wallet_balance": float(usdt_coin.get("walletBalance", 0)),
                    "available_balance": float(usdt_coin.get("availableToWithdraw", 0)),
                    "raw": wallet,
                },
            )
            await self._enqueue(event)
            logger.info("[LIVE_BRIDGE] EQUITY_UPDATE equity=%.4f", equity)

    async def _enqueue(self, event: LiveEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("[LIVE_BRIDGE] Event queue full, dropping %s", event.event_type)

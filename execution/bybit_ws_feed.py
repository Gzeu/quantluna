"""
QuantLuna — BybitWsFeed
Sprint 27

WebSocket feed via pybit v5 — kline + orderbook subscriptions.
Drop-in replacement pentru BinanceWsFeed din perspectiva LiveTrader.

Features:
  - kline.{interval}.{symbol} subscription — on_bar callback
  - orderbook.1.{symbol} subscription — on_orderbook callback
  - Auto-reconnect la disconnect (max 5 încercări, backoff exp)
  - Watchdog: alert dacă nu s-a primit niciun mesaj în > stale_s secunde
  - Suport testnet

Env vars:
  BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET, BYBIT_CATEGORY

Usage:
    from execution.bybit_ws_feed import BybitWsFeed

    def on_bar(msg: dict):
        close = float(msg["data"][0]["close"])
        ...

    feed = BybitWsFeed(symbol="BTCUSDT", interval="1", on_bar=on_bar)
    await feed.start()   # non-blocking, runs in background task
    await feed.stop()
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_MAX_RECONNECT   = 5
_RECONNECT_BASE  = 1.0   # seconds
_WATCHDOG_STALE  = 30.0  # seconds without message = stale
_WATCHDOG_DEAD   = 60.0  # seconds without message = dead


class BybitWsFeed:
    """
    Async WebSocket feed pentru Bybit v5.
    Rulează în background asyncio task.
    """

    def __init__(
        self,
        symbol:        str,
        interval:      str = "1",         # "1","3","5","15","60","D"
        category:      str = "",
        on_bar:        Optional[Callable] = None,
        on_orderbook:  Optional[Callable] = None,
        testnet:       bool = False,
        api_key:       str  = "",
        api_secret:    str  = "",
    ) -> None:
        self.symbol       = symbol
        self.interval     = interval
        self.category     = category or os.getenv("BYBIT_CATEGORY", "linear")
        self.on_bar       = on_bar
        self.on_orderbook = on_orderbook
        self.testnet      = testnet or os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        self.api_key      = api_key      or os.getenv("BYBIT_API_KEY",    "")
        self.api_secret   = api_secret   or os.getenv("BYBIT_API_SECRET", "")

        self._task:       Optional[asyncio.Task] = None
        self._running:    bool  = False
        self._last_msg_ts: float = 0.0

    async def start(self) -> None:
        """Start background WebSocket listener."""
        self._running = True
        self._task    = asyncio.create_task(self._run_loop())
        logger.info(f"BybitWsFeed started: {self.symbol} {self.interval} (testnet={self.testnet})")

    async def stop(self) -> None:
        """Stop feed gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"BybitWsFeed stopped: {self.symbol}")

    @property
    def last_msg_age_s(self) -> float:
        if self._last_msg_ts == 0:
            return float("inf")
        return time.time() - self._last_msg_ts

    @property
    def is_stale(self) -> bool:
        return self.last_msg_age_s > _WATCHDOG_STALE

    @property
    def is_dead(self) -> bool:
        return self.last_msg_age_s > _WATCHDOG_DEAD

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Main reconnect loop."""
        reconnect_count = 0
        delay = _RECONNECT_BASE
        while self._running and reconnect_count <= _MAX_RECONNECT:
            try:
                await self._connect_and_listen()
                delay = _RECONNECT_BASE  # reset on clean disconnect
                reconnect_count = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                reconnect_count += 1
                logger.warning(
                    f"BybitWsFeed {self.symbol}: error (attempt {reconnect_count}/{_MAX_RECONNECT}): {e}"
                )
                if reconnect_count > _MAX_RECONNECT:
                    logger.error(f"BybitWsFeed {self.symbol}: max reconnects reached, stopping")
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _connect_and_listen(self) -> None:
        """Connect to Bybit WebSocket and process messages."""
        import threading
        from pybit.unified_trading import WebSocket

        loop     = asyncio.get_event_loop()
        msg_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        def _handle_message(msg: dict) -> None:
            self._last_msg_ts = time.time()
            try:
                loop.call_soon_threadsafe(msg_queue.put_nowait, msg)
            except Exception:
                pass

        ws = WebSocket(
            testnet=self.testnet,
            channel_type=self.category,
        )

        # Subscribe
        topic_kline     = f"kline.{self.interval}.{self.symbol}"
        topic_orderbook = f"orderbook.1.{self.symbol}"

        if self.on_bar:
            ws.kline_stream(
                interval=int(self.interval) if self.interval.isdigit() else 1,
                symbol=self.symbol,
                callback=_handle_message,
            )
        if self.on_orderbook:
            ws.orderbook_stream(
                depth=1,
                symbol=self.symbol,
                callback=_handle_message,
            )

        logger.info(f"BybitWsFeed {self.symbol}: connected, listening...")

        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(msg_queue.get(), timeout=5.0)
                    topic = msg.get("topic", "")
                    if "kline" in topic and self.on_bar:
                        await asyncio.get_event_loop().run_in_executor(None, self.on_bar, msg)
                    elif "orderbook" in topic and self.on_orderbook:
                        await asyncio.get_event_loop().run_in_executor(None, self.on_orderbook, msg)
                except asyncio.TimeoutError:
                    pass  # Watchdog check happens via is_stale/is_dead
        finally:
            try:
                ws.exit()
            except Exception:
                pass

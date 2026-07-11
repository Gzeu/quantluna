"""
QuantLuna — BybitWsFeed
Sprint 27 + July 2026 + S20-fix

WebSocket feed via pybit v5 — kline + orderbook subscriptions.

Fix S20:
  - get_bar(): metodă async care returnează BarData (așteptat de _run_loop)
  - stream_bars() yield BarData în loc de dict brut
  - _normalize_bar_dict(): extrage close price din Bybit V5 format
  - _BarData namedtuple: price_y, price_x, timestamp, symbol_y, symbol_x
    (pentru BybitWsFeed single-symbol: price_y = price_x = close)

Improvements (July 2026):
  - on_bar / on_orderbook callbacks can now be async coroutines
  - Reconnect uses jitter (±20%) to avoid thundering-herd on mass restart
  - Dead-feed detection: if is_dead fires twice consecutively, force WS reconnect
  - stream_bars() async generator for use in BybitLiveRunner
  - MAX_RECONNECT bumped from 5 to configurable via ws_max_reconnects param
  - Separate queue per subscription (kline vs orderbook) to prevent cross-topic drop

Env vars:
  BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET, BYBIT_CATEGORY

Usage:
    # Callback style (legacy):
    feed = BybitWsFeed(symbol="BTCUSDT", interval="1", on_bar=handle_bar)
    await feed.start()

    # Generator style (preferred in BybitLiveRunner):
    async for bar in feed.stream_bars():
        process(bar)   # bar.price_y, bar.price_x, bar.timestamp

    # get_bar() style (used in _run_loop):
    bar = await feed.get_bar()   # blocks until next bar
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import random
import time
from collections import namedtuple
from typing import AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

_RECONNECT_BASE  = 1.0    # seconds
_RECONNECT_MAX   = 30.0   # seconds cap
_WATCHDOG_STALE  = 30.0   # seconds without message = stale
_WATCHDOG_DEAD   = 60.0   # seconds without message = dead (triggers forced reconnect)
_DEAD_RECONNECT_THRESHOLD = 2  # consecutive dead checks before forced reconnect

# BarData namedtuple — compatibil cu _run_loop din BybitLiveRunner
# price_y = price_x = close pentru single-symbol feed
# Pentru dual-symbol, BybitWsBarsAdapter suprascrie aceste câmpuri
BarData = namedtuple(
    "BarData",
    ["symbol_y", "symbol_x", "price_y", "price_x", "timestamp"],
    defaults=["", "", 0.0, 0.0, 0.0],
)


class BybitWsFeed:
    """
    Async WebSocket feed for Bybit v5.
    Runs in background asyncio task; exposes stream_bars() generator + get_bar().
    """

    def __init__(
        self,
        symbol:          str,
        interval:        str = "1",
        category:        str = "",
        on_bar:          Optional[Callable] = None,
        on_orderbook:    Optional[Callable] = None,
        testnet:         bool = False,
        api_key:         str  = "",
        api_secret:      str  = "",
        ws_max_reconnects: int = 20,
    ) -> None:
        self.symbol           = symbol
        self.interval         = interval
        self.category         = category or os.getenv("BYBIT_CATEGORY", "linear")
        self.on_bar           = on_bar
        self.on_orderbook     = on_orderbook
        self.testnet          = testnet or os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        self.api_key          = api_key      or os.getenv("BYBIT_API_KEY",    "")
        self.api_secret       = api_secret   or os.getenv("BYBIT_API_SECRET", "")
        self.ws_max_reconnects = ws_max_reconnects

        self._task:            Optional[asyncio.Task] = None
        self._running:         bool  = False
        self._last_msg_ts:     float = 0.0
        self._dead_count:      int   = 0

        # Separate queues for kline bars and orderbook updates
        self._bar_queue:   asyncio.Queue = asyncio.Queue(maxsize=500)
        self._ob_queue:    asyncio.Queue = asyncio.Queue(maxsize=500)

    async def start(self) -> None:
        self._running = True
        self._task    = asyncio.create_task(self._run_loop())
        logger.info(
            f"BybitWsFeed started: {self.symbol} interval={self.interval} "
            f"testnet={self.testnet} category={self.category}"
        )

    async def stop(self) -> None:
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

    def is_healthy(self) -> bool:
        """Health check pentru HealthCheck/Watchdog."""
        return self._running and not self.is_stale

    # ------------------------------------------------------------------
    # FIX S20: get_bar() — așteptat de _run_loop din BybitLiveRunner
    # ------------------------------------------------------------------

    async def get_bar(self, timeout: float = 30.0) -> Optional[BarData]:
        """
        Așteaptă următorul bar și returnează un obiect BarData.

        Pentru un feed single-symbol, price_y == price_x == close.
        BybitWsBarsAdapter suprascrie această metodă pentru dual-symbol.

        Returns None la timeout (runner-ul verifică None).
        """
        if not self._running:
            await self.start()
        try:
            raw = await asyncio.wait_for(self._bar_queue.get(), timeout=timeout)
            return self._normalize_to_bardata(raw)
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None

    def _normalize_to_bardata(self, msg: dict) -> BarData:
        """
        Converteste un mesaj Bybit V5 kline dict în BarData.

        Bybit V5 format:
          {"topic": "kline.5.BTCUSDT", "data": [{"close": "...", "confirm": true}]}
        """
        close = 0.0
        ts    = time.time()
        try:
            data = msg.get("data", msg)
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                close = float(data.get("close") or data.get("c") or 0.0)
                ts_raw = data.get("start") or data.get("timestamp") or data.get("ts")
                if ts_raw:
                    ts = float(ts_raw) / 1000.0  # ms → s
        except Exception as exc:
            logger.debug(f"BybitWsFeed._normalize_to_bardata: {exc}")

        return BarData(
            symbol_y  = self.symbol,
            symbol_x  = self.symbol,
            price_y   = close,
            price_x   = close,
            timestamp = ts,
        )

    # ------------------------------------------------------------------
    # Public: async generator (preferred usage in BybitLiveRunner)
    # ------------------------------------------------------------------

    async def stream_bars(self) -> AsyncIterator[BarData]:
        """
        Async generator that yields BarData objects (FIX S20: nu mai dict brut).
        Usage:
            async for bar in feed.stream_bars():
                # bar.price_y, bar.price_x, bar.timestamp
        Blocks until feed is stopped.
        """
        if not self._running:
            await self.start()
        while self._running:
            try:
                raw = await asyncio.wait_for(self._bar_queue.get(), timeout=5.0)
                yield self._normalize_to_bardata(raw)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Main reconnect loop with jitter to avoid thundering herd."""
        reconnect_count = 0
        delay = _RECONNECT_BASE
        while self._running and reconnect_count <= self.ws_max_reconnects:
            try:
                await self._connect_and_listen()
                delay = _RECONNECT_BASE
                reconnect_count = 0
                self._dead_count = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                reconnect_count += 1
                logger.warning(
                    f"BybitWsFeed {self.symbol}: error "
                    f"(attempt {reconnect_count}/{self.ws_max_reconnects}): {e}"
                )
                if reconnect_count > self.ws_max_reconnects:
                    logger.error(
                        f"BybitWsFeed {self.symbol}: max reconnects reached, stopping"
                    )
                    break
                jitter  = delay * 0.2 * (random.random() * 2 - 1)
                wait    = max(0.5, min(delay + jitter, _RECONNECT_MAX))
                logger.debug(f"BybitWsFeed {self.symbol}: reconnect in {wait:.1f}s")
                await asyncio.sleep(wait)
                delay   = min(delay * 2, _RECONNECT_MAX)

    async def _connect_and_listen(self) -> None:
        """Connect to Bybit WebSocket and forward messages to queues."""
        from pybit.unified_trading import WebSocket

        loop = asyncio.get_event_loop()

        def _enqueue(q: asyncio.Queue, msg: dict) -> None:
            self._last_msg_ts = time.time()
            self._dead_count  = 0
            try:
                loop.call_soon_threadsafe(q.put_nowait, msg)
            except asyncio.QueueFull:
                logger.debug(f"BybitWsFeed {self.symbol}: queue full, dropping message")

        ws = WebSocket(
            testnet=self.testnet,
            channel_type=self.category,
        )

        if self.on_bar or True:   # always subscribe bars for stream_bars() / get_bar()
            ws.kline_stream(
                interval=int(self.interval) if self.interval.isdigit() else 1,
                symbol=self.symbol,
                callback=lambda m: _enqueue(self._bar_queue, m),
            )
        if self.on_orderbook:
            ws.orderbook_stream(
                depth=1,
                symbol=self.symbol,
                callback=lambda m: _enqueue(self._ob_queue, m),
            )

        logger.info(f"BybitWsFeed {self.symbol}: WS connected")

        try:
            while self._running:
                # --- process bar queue ---
                while not self._bar_queue.empty():
                    try:
                        msg = self._bar_queue.get_nowait()
                        if self.on_bar:
                            if inspect.iscoroutinefunction(self.on_bar):
                                await self.on_bar(msg)
                            else:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, self.on_bar, msg
                                )
                    except asyncio.QueueEmpty:
                        break

                # --- process orderbook queue ---
                while not self._ob_queue.empty():
                    try:
                        msg = self._ob_queue.get_nowait()
                        if self.on_orderbook:
                            if inspect.iscoroutinefunction(self.on_orderbook):
                                await self.on_orderbook(msg)
                            else:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, self.on_orderbook, msg
                                )
                    except asyncio.QueueEmpty:
                        break

                # --- dead feed detection → force reconnect ---
                if self.is_dead:
                    self._dead_count += 1
                    logger.warning(
                        f"BybitWsFeed {self.symbol}: dead feed detected "
                        f"(age={self.last_msg_age_s:.0f}s, count={self._dead_count})"
                    )
                    if self._dead_count >= _DEAD_RECONNECT_THRESHOLD:
                        logger.error(
                            f"BybitWsFeed {self.symbol}: forcing reconnect after "
                            f"{self._dead_count} dead checks"
                        )
                        break

                await asyncio.sleep(1.0)
        finally:
            try:
                ws.exit()
            except Exception:
                pass

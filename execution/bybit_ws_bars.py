"""
QuantLuna — Bybit WebSocket Bars Adapter (Sprint 21 + S20-fix)

Adaptează BybitWsFeed (kline stream) la un async generator de BarData perechi.

Fix S20:
  - get_bar(): metodă async compatibilă cu _run_loop din BybitLiveRunner
  - stream_bars() yield BarData cu price_y / price_x corecte per simbol
  - is_healthy(): health check pentru WsWatchdog / HealthCheck

BybitWsFeed trimite bare individuale per simbol. Acest adapter:
  1. Subscrie la ambele simboluri simultan
  2. Păstrează ultimul preţ cunoscut pentru fiecare simbol (bar alignment)
  3. La fiecare bar nou de pe orice simbol, emite o BarData sincronizată
  4. Garantează că nu emite BarData dacă unul din simboluri nu a primit date

Usage:
    feed = BybitWsBarsAdapter(BybitWsFeed(...), interval="5")
    # In _run_loop:
    bar = await feed.get_bar()          # bar.price_y, bar.price_x, bar.timestamp
    # Sau generator:
    async for bar in feed.stream_bars("BTCUSDT", "ETHUSDT"):
        await runner._process_bar(bar)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional

from loguru import logger

from execution.bybit_ws_feed import BarData


class BybitWsBarsAdapter:
    """
    Wraps two BybitWsFeed instances and emits synchronized BarData pairs.

    Parameters
    ----------
    ws_feed     : BybitWsFeed instance pentru symbol_y (sau None pentru mock)
    interval    : kline interval string (e.g. "5" for 5-minute bars)
    price_field : OHLCV field to use ("close" sau "open", "high", "low")
    symbol_y    : primul simbol (setat de get_dual_ws_feed)
    symbol_x    : al doilea simbol (setat de get_dual_ws_feed)
    ws_feed_x   : BybitWsFeed pentru symbol_x (setat de get_dual_ws_feed)
    """

    def __init__(
        self,
        ws_feed=None,
        interval: str = "5",
        price_field: str = "close",
        symbol_y: str = "BTCUSDT",
        symbol_x: str = "ETHUSDT",
        ws_feed_x=None,
    ) -> None:
        self._ws_feed     = ws_feed
        self._ws_feed_x   = ws_feed_x
        self._interval    = interval
        self._price_field = price_field
        self._symbol_y    = symbol_y
        self._symbol_x    = symbol_x
        self._prices: Dict[str, float] = {}
        self._last_ts: Dict[str, float] = {}
        self._bar_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._running = False
        self._last_msg_ts: float = 0.0

    # ------------------------------------------------------------------
    # Health check (pentru WsWatchdog / HealthCheck)
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Returnează True dacă feed-ul rulează și a primit date recent."""
        if not self._running:
            return False
        age = time.time() - self._last_msg_ts if self._last_msg_ts > 0 else float("inf")
        return age < 60.0

    # ------------------------------------------------------------------
    # FIX S20: get_bar() — așteptat de _run_loop din BybitLiveRunner
    # ------------------------------------------------------------------

    async def get_bar(self, timeout: float = 30.0) -> Optional[BarData]:
        """
        Așteaptă următorul BarData sincronizat (ambele simboluri).
        Compatibil cu _run_loop: `bar = await ws_feed.get_bar()`.
        Returns None la timeout.
        """
        if not self._running:
            # Pornește background tasks la primul get_bar()
            asyncio.create_task(self._start_background())
            self._running = True

        try:
            updated_symbol = await asyncio.wait_for(self._bar_queue.get(), timeout=timeout)
            if self._symbol_y in self._prices and self._symbol_x in self._prices:
                return BarData(
                    symbol_y  = self._symbol_y,
                    symbol_x  = self._symbol_x,
                    price_y   = self._prices[self._symbol_y],
                    price_x   = self._prices[self._symbol_x],
                    timestamp = time.time(),
                )
            return None
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None

    async def _start_background(self) -> None:
        """Pornește consume tasks pentru ambele simboluri."""
        task_y = asyncio.create_task(
            self._consume_symbol(self._symbol_y, self._ws_feed),
            name=f"ws_{self._symbol_y}"
        )
        task_x = asyncio.create_task(
            self._consume_symbol(self._symbol_x, self._ws_feed_x),
            name=f"ws_{self._symbol_x}"
        )
        await asyncio.gather(task_y, task_x, return_exceptions=True)

    # ------------------------------------------------------------------
    # Main interface: async generator de BarData
    # ------------------------------------------------------------------

    async def stream_bars(
        self,
        symbol_y: str = "",
        symbol_x: str = "",
    ) -> AsyncIterator[BarData]:
        """
        Async generator: yields BarData când ambele simboluri au preţuri fresh.
        """
        sym_y = symbol_y or self._symbol_y
        sym_x = symbol_x or self._symbol_x

        self._running = True
        self._prices  = {}
        self._last_ts = {}

        if self._ws_feed is None:
            async for bar in self._mock_stream(sym_y, sym_x):
                yield bar
            return

        task_y = asyncio.create_task(
            self._consume_symbol(sym_y, self._ws_feed), name=f"ws_{sym_y}"
        )
        task_x = asyncio.create_task(
            self._consume_symbol(sym_x, self._ws_feed_x), name=f"ws_{sym_x}"
        )

        try:
            while self._running:
                try:
                    updated_symbol = await asyncio.wait_for(
                        self._bar_queue.get(), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("BybitWsBarsAdapter: 30s timeout, no bar received")
                    continue

                if sym_y in self._prices and sym_x in self._prices:
                    bar = BarData(
                        symbol_y  = sym_y,
                        symbol_x  = sym_x,
                        price_y   = self._prices[sym_y],
                        price_x   = self._prices[sym_x],
                        timestamp = time.time(),
                    )
                    yield bar
        finally:
            self._running = False
            task_y.cancel()
            task_x.cancel()
            await asyncio.gather(task_y, task_x, return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal: consume a single symbol from ws_feed
    # ------------------------------------------------------------------

    async def _consume_symbol(self, symbol: str, ws_feed) -> None:
        """Subscribe la kline stream și push price updates în queue."""
        if ws_feed is None:
            logger.warning(f"BybitWsBarsAdapter: no ws_feed for {symbol}, skipping")
            return
        try:
            if hasattr(ws_feed, "subscribe_kline"):
                await ws_feed.subscribe_kline(symbol=symbol, interval=self._interval)
            elif hasattr(ws_feed, "subscribe"):
                await ws_feed.subscribe(symbol=symbol, channel="kline", interval=self._interval)
            elif hasattr(ws_feed, "stream_bars"):
                # BybitWsFeed: folosește stream_bars() și extrage price
                async for bar in ws_feed.stream_bars():
                    if not self._running:
                        return
                    price = bar.price_y if hasattr(bar, "price_y") else None
                    if price is not None and price > 0:
                        self._prices[symbol] = price
                        self._last_ts[symbol] = time.time()
                        self._last_msg_ts = time.time()
                        await self._bar_queue.put(symbol)
                return

            if hasattr(ws_feed, "stream"):
                async for msg in ws_feed.stream(symbol=symbol):
                    if not self._running:
                        return
                    price = self._extract_price(msg)
                    if price is not None:
                        self._prices[symbol] = price
                        self._last_ts[symbol] = time.time()
                        self._last_msg_ts = time.time()
                        await self._bar_queue.put(symbol)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error(f"BybitWsBarsAdapter: consume_symbol({symbol}) error: {exc}")

    def _extract_price(self, msg) -> Optional[float]:
        """Extract price from kline message (Bybit V5 format)."""
        try:
            if isinstance(msg, dict):
                data = msg.get("data", msg)
                if isinstance(data, list) and data:
                    data = data[0]
                if isinstance(data, dict):
                    field = self._price_field
                    val = data.get(field) or data.get("c")
                    if val is not None:
                        return float(val)
            elif isinstance(msg, (int, float)):
                return float(msg)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Mock stream pentru testing fără conexiune WS reală
    # ------------------------------------------------------------------

    async def _mock_stream(
        self,
        symbol_y: str,
        symbol_x: str,
    ) -> AsyncIterator[BarData]:
        """Mock stream pentru testing fără conexiune WS reală."""
        import random
        price_y = 60000.0
        price_x = 3000.0
        while self._running:
            price_y *= 1 + (random.random() - 0.5) * 0.001
            price_x *= 1 + (random.random() - 0.5) * 0.001
            yield BarData(
                symbol_y  = symbol_y,
                symbol_x  = symbol_x,
                price_y   = round(price_y, 2),
                price_x   = round(price_x, 2),
                timestamp = time.time(),
            )
            await asyncio.sleep(1.0)

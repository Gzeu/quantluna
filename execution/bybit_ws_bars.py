"""
QuantLuna — Bybit WebSocket Bars Adapter (Sprint 21)

Adaptă BybitWsFeed (kline stream) la un async generator de BarData perechi.

BybitWsFeed trimite bare individuale per simbol. Acest adapter:
  1. Subscrie la ambele simboluri simultan
  2. Păstrează ultimul preţ cunoscut pentru fiecare simbol (bar alignment)
  3. La fiecare bar nou de pe orice simbol, emite o BarData sincronizată
  4. Garantează că nu emite BarData dacă unul din simboluri nu a primit date

Usage:
    feed = BybitWsBarsAdapter(BybitWsFeed(...), interval="5")
    async for bar in feed.stream_bars("BTCUSDT", "ETHUSDT"):
        await runner._process_bar(bar)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional

from loguru import logger

from execution.integration_loop import BarData


class BybitWsBarsAdapter:
    """
    Wraps BybitWsFeed and emits synchronized BarData pairs.

    Parameters
    ----------
    ws_feed     : BybitWsFeed instance (or any object with subscribe / stream)
    interval    : kline interval string (e.g. "5" for 5-minute bars)
    price_field : OHLCV field to use ("close" or "open", "high", "low")
    """

    def __init__(
        self,
        ws_feed=None,
        interval: str = "5",
        price_field: str = "close",
    ) -> None:
        self._ws_feed     = ws_feed
        self._interval    = interval
        self._price_field = price_field
        self._prices: Dict[str, float] = {}
        self._last_ts: Dict[str, float] = {}
        self._bar_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._running = False

    # ------------------------------------------------------------------
    # Main interface: async generator of BarData
    # ------------------------------------------------------------------

    async def stream_bars(
        self,
        symbol_y: str = "BTCUSDT",
        symbol_x: str = "ETHUSDT",
    ) -> AsyncIterator[BarData]:
        """
        Async generator: yields BarData when both symbols have fresh prices.
        Runs until cancelled or ws_feed disconnects.
        """
        self._running = True
        self._prices  = {}
        self._last_ts = {}

        if self._ws_feed is None:
            logger.warning("BybitWsBarsAdapter: no ws_feed, using mock price stream")
            async for bar in self._mock_stream(symbol_y, symbol_x):
                yield bar
            return

        # Start background tasks for each symbol
        task_y = asyncio.create_task(
            self._consume_symbol(symbol_y), name=f"ws_{symbol_y}"
        )
        task_x = asyncio.create_task(
            self._consume_symbol(symbol_x), name=f"ws_{symbol_x}"
        )

        try:
            while self._running:
                # Wait for a bar update
                try:
                    updated_symbol = await asyncio.wait_for(
                        self._bar_queue.get(), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("BybitWsBarsAdapter: 30s timeout, no bar received")
                    continue

                # Both symbols must have prices
                if symbol_y in self._prices and symbol_x in self._prices:
                    yield BarData(
                        symbol_y=symbol_y,
                        symbol_x=symbol_x,
                        price_y=self._prices[symbol_y],
                        price_x=self._prices[symbol_x],
                        timestamp=time.time(),
                    )
        finally:
            self._running = False
            task_y.cancel()
            task_x.cancel()
            await asyncio.gather(task_y, task_x, return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal: consume a single symbol from ws_feed
    # ------------------------------------------------------------------

    async def _consume_symbol(self, symbol: str) -> None:
        """Subscribe to kline stream and push price updates to queue."""
        ws = self._ws_feed
        try:
            if hasattr(ws, "subscribe_kline"):
                await ws.subscribe_kline(symbol=symbol, interval=self._interval)
            elif hasattr(ws, "subscribe"):
                await ws.subscribe(symbol=symbol, channel="kline", interval=self._interval)

            async for msg in ws.stream(symbol=symbol):
                if not self._running:
                    return
                price = self._extract_price(msg)
                if price is not None:
                    self._prices[symbol] = price
                    self._last_ts[symbol] = time.time()
                    await self._bar_queue.put(symbol)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error(f"BybitWsBarsAdapter: consume_symbol({symbol}) error: {exc}")

    def _extract_price(self, msg) -> Optional[float]:
        """Extract price from kline message (Bybit V5 format)."""
        try:
            if isinstance(msg, dict):
                # Bybit V5: {"topic": "kline.5.BTCUSDT", "data": [{"close": "..."}]}
                data = msg.get("data", msg)
                if isinstance(data, list) and data:
                    data = data[0]
                if isinstance(data, dict):
                    field = self._price_field
                    val = data.get(field) or data.get("c")  # 'c' = close in V5
                    if val is not None:
                        return float(val)
            elif isinstance(msg, (int, float)):
                return float(msg)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Mock stream for testing without real WS connection
    # ------------------------------------------------------------------

    async def _mock_stream(
        self,
        symbol_y: str,
        symbol_x: str,
        n_bars: int = 200,
        interval_s: float = 0.0,
    ) -> AsyncIterator[BarData]:
        """Synthetic OU stream for testing."""
        import random
        random.seed(42)
        spread = 0.0
        base_x = 100.0

        for _ in range(n_bars):
            spread += 0.05 * (0 - spread) + random.gauss(0, 1)
            price_x = base_x + random.gauss(0, 0.5)
            price_y = price_x + spread
            yield BarData(
                symbol_y=symbol_y,
                symbol_x=symbol_x,
                price_y=price_y,
                price_x=price_x,
                timestamp=time.time(),
            )
            if interval_s > 0:
                await asyncio.sleep(interval_s)

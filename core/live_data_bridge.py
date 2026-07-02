"""
QuantLuna — Live Data Bridge (Sprint 20)

Conectează sursele de date (historical_fetcher sau WebSocket feed) cu
IntegrationLoop prin generarea de BarData obiecte.

Două moduri:
  1. HISTORICAL  — citeşte OHLCV din historical_fetcher, convert la BarData list
  2. LIVE FEED   — asincron, citeşte pret din market_data_cache bar-cu-bar

Usage (historical backfill):
    bridge = LiveDataBridge(LiveDataBridgeConfig(
        symbol_y="BTCUSDT", symbol_x="ETHUSDT",
        venue="bybit", interval="1h", lookback_bars=500,
    ))
    bars = await bridge.fetch_historical()
    results = await loop.run_synthetic(bars)

Usage (live simulation from cache):
    async for bar in bridge.stream_live(cache=market_data_cache):
        result = await loop._process_bar(bar)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional

from loguru import logger

from execution.integration_loop import BarData


@dataclass
class LiveDataBridgeConfig:
    symbol_y:      str   = "BTCUSDT"
    symbol_x:      str   = "ETHUSDT"
    venue:         str   = "bybit"
    interval:      str   = "1h"           # timeframe string
    lookback_bars: int   = 500            # bars to fetch in historical mode
    price_field:   str   = "close"        # OHLCV field to use as price
    live_poll_s:   float = 1.0            # seconds between polls in live mode
    max_live_bars: int   = 0              # 0 = unlimited


class LiveDataBridge:
    """
    Bridges data sources to IntegrationLoop BarData format.

    Parameters
    ----------
    cfg : LiveDataBridgeConfig
    fetcher : historical fetcher instance (optional, injected for testability)
    """

    def __init__(
        self,
        cfg: Optional[LiveDataBridgeConfig] = None,
        fetcher=None,
    ) -> None:
        self.cfg     = cfg or LiveDataBridgeConfig()
        self._fetcher = fetcher

    # ------------------------------------------------------------------
    # Historical mode
    # ------------------------------------------------------------------

    async def fetch_historical(self) -> List[BarData]:
        """
        Fetch historical OHLCV for both symbols and zip into BarData list.
        Returns bars sorted by timestamp ascending.
        """
        cfg = self.cfg

        if self._fetcher is None:
            logger.warning(
                "LiveDataBridge: no fetcher provided, returning empty list. "
                "Inject a fetcher or use from_dataframes()."
            )
            return []

        try:
            df_y = await self._fetch_symbol(cfg.symbol_y)
            df_x = await self._fetch_symbol(cfg.symbol_x)
        except Exception as exc:
            logger.error(f"LiveDataBridge: fetch failed: {exc}")
            return []

        return self._zip_to_bars(df_y, df_x)

    def from_dataframes(self, df_y, df_x) -> List[BarData]:
        """
        Convert two aligned pandas DataFrames to BarData list.
        DataFrames must have a 'close' (or cfg.price_field) column.
        Index should be datetime or integer.
        """
        return self._zip_to_bars(df_y, df_x)

    # ------------------------------------------------------------------
    # Live streaming mode
    # ------------------------------------------------------------------

    async def stream_live(
        self,
        cache=None,
    ) -> AsyncIterator[BarData]:
        """
        Async generator that yields BarData from a market data cache.
        Polls cache every cfg.live_poll_s seconds.

        Parameters
        ----------
        cache : MarketDataCache or any object with get_latest_price(symbol) -> float
        """
        cfg      = self.cfg
        bar_count = 0

        while True:
            if cfg.max_live_bars > 0 and bar_count >= cfg.max_live_bars:
                logger.info(f"LiveDataBridge: reached max_live_bars={cfg.max_live_bars}")
                return

            price_y: Optional[float] = None
            price_x: Optional[float] = None

            if cache is not None:
                try:
                    price_y = float(cache.get_latest_price(cfg.symbol_y))
                    price_x = float(cache.get_latest_price(cfg.symbol_x))
                except Exception as exc:
                    logger.warning(f"LiveDataBridge: cache read error: {exc}")

            if price_y is not None and price_x is not None:
                yield BarData(
                    symbol_y=cfg.symbol_y,
                    symbol_x=cfg.symbol_x,
                    price_y=price_y,
                    price_x=price_x,
                    timestamp=time.time(),
                )
                bar_count += 1

            await asyncio.sleep(cfg.live_poll_s)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_symbol(self, symbol: str):
        """Fetch OHLCV DataFrame for a single symbol."""
        fetcher = self._fetcher
        cfg     = self.cfg

        # Try async fetch first, fall back to sync
        if hasattr(fetcher, "fetch_async"):
            return await fetcher.fetch_async(
                symbol=symbol, interval=cfg.interval, limit=cfg.lookback_bars
            )
        elif hasattr(fetcher, "fetch"):
            return fetcher.fetch(
                symbol=symbol, interval=cfg.interval, limit=cfg.lookback_bars
            )
        elif hasattr(fetcher, "get_ohlcv"):
            return fetcher.get_ohlcv(symbol=symbol, interval=cfg.interval)
        else:
            raise RuntimeError(
                f"LiveDataBridge: fetcher has no known fetch method. "
                f"Expected: fetch_async, fetch, or get_ohlcv."
            )

    def _zip_to_bars(self, df_y, df_x) -> List[BarData]:
        """Zip two DataFrames to BarData list, aligned by index position."""
        field = self.cfg.price_field
        bars  = []

        try:
            prices_y = list(df_y[field])
            prices_x = list(df_x[field])
        except (KeyError, TypeError) as exc:
            logger.error(f"LiveDataBridge: _zip_to_bars error: {exc} (field='{field}')")
            return []

        n = min(len(prices_y), len(prices_x))
        cfg = self.cfg

        for i in range(n):
            ts = time.time() - (n - i) * 3600.0  # synthetic timestamps
            try:
                ts_raw = df_y.index[i]
                ts = float(ts_raw.timestamp()) if hasattr(ts_raw, "timestamp") else float(ts_raw)
            except Exception:
                pass

            bars.append(BarData(
                symbol_y=cfg.symbol_y,
                symbol_x=cfg.symbol_x,
                price_y=float(prices_y[i]),
                price_x=float(prices_x[i]),
                timestamp=ts,
            ))

        logger.info(f"LiveDataBridge: zipped {n} bars for {cfg.symbol_y}/{cfg.symbol_x}")
        return bars

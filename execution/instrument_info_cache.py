"""
execution/instrument_info_cache.py  -  QuantLuna Instrument Info Cache

Fetches per-symbol lot size rules from Bybit REST:
  /v5/market/instruments-info?category=linear&symbol=BTCUSDT

Exposes:
  InstrumentInfoCache.round_qty(symbol, qty)   -> float
  InstrumentInfoCache.round_price(symbol, price) -> float

Cache TTL: 1h per symbol. Thread-safe (asyncio).
Fallback: 8 decimal places on fetch error (logs warning).

Usage::
    cache = InstrumentInfoCache(testnet=False, category="linear")
    qty   = await cache.round_qty("BTCUSDT", 0.00312)
    price = await cache.round_price("BTCUSDT", 67432.1)
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Optional

from loguru import logger

_CACHE_TTL_S = 3600  # 1 hour
_FALLBACK_DECIMALS = 8
_BYBIT_REST_MAINNET = "https://api.bybit.com"
_BYBIT_REST_TESTNET = "https://api-testnet.bybit.com"


def _step_to_decimals(step: float) -> int:
    """Convert e.g. 0.001 -> 3, 0.1 -> 1, 1.0 -> 0."""
    if step <= 0:
        return _FALLBACK_DECIMALS
    if step >= 1.0:
        return 0
    return max(0, -int(math.floor(math.log10(step))))


class _SymbolInfo:
    __slots__ = ("qty_step", "qty_decimals", "tick_size", "price_decimals", "fetched_at")

    def __init__(self, qty_step: float, tick_size: float) -> None:
        self.qty_step = qty_step
        self.qty_decimals = _step_to_decimals(qty_step)
        self.tick_size = tick_size
        self.price_decimals = _step_to_decimals(tick_size)
        self.fetched_at = time.monotonic()

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self.fetched_at) > _CACHE_TTL_S


class InstrumentInfoCache:
    """
    Per-symbol lot-size and price-filter cache.

    Fetches from Bybit REST on first call per symbol (or when TTL expired).
    Falls back gracefully on network errors.
    """

    def __init__(
        self,
        testnet: bool = False,
        category: str = "linear",
    ) -> None:
        self._base_url = _BYBIT_REST_TESTNET if testnet else _BYBIT_REST_MAINNET
        self._category = category
        self._cache: dict[str, _SymbolInfo] = {}
        self._lock = asyncio.Lock()

    async def _fetch(self, symbol: str) -> Optional[_SymbolInfo]:
        url = (
            f"{self._base_url}/v5/market/instruments-info"
            f"?category={self._category}&symbol={symbol}"
        )
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()

            items = data.get("result", {}).get("list", [])
            if not items:
                logger.warning(
                    "InstrumentInfoCache: niciun instrument gasit pentru %s", symbol
                )
                return None

            info = items[0]
            lot_filter = info.get("lotSizeFilter", {})
            price_filter = info.get("priceFilter", {})

            qty_step  = float(lot_filter.get("qtyStep", "0.001"))
            tick_size = float(price_filter.get("tickSize", "0.01"))

            sym_info = _SymbolInfo(qty_step=qty_step, tick_size=tick_size)
            logger.info(
                "InstrumentInfoCache: %s -> qtyStep=%s tickSize=%s",
                symbol, qty_step, tick_size,
            )
            return sym_info

        except Exception as exc:
            logger.warning(
                "InstrumentInfoCache: fetch failed pentru %s (%s) — fallback %d zecimale",
                symbol, exc, _FALLBACK_DECIMALS,
            )
            return None

    async def get(self, symbol: str) -> Optional[_SymbolInfo]:
        """Return cached SymbolInfo, fetching/refreshing if needed."""
        async with self._lock:
            cached = self._cache.get(symbol)
            if cached is None or cached.is_stale:
                info = await self._fetch(symbol)
                if info is not None:
                    self._cache[symbol] = info
                    return info
                # Keep stale cache if re-fetch fails
                if cached is not None:
                    logger.warning(
                        "InstrumentInfoCache: re-fetch esuat pt %s — pastram cache stale",
                        symbol,
                    )
                    return cached
                return None
            return cached

    async def round_qty(self, symbol: str, qty: float) -> float:
        """
        Round qty to symbol's qtyStep.
        Falls back to _FALLBACK_DECIMALS decimal places on cache miss.
        """
        info = await self.get(symbol)
        if info is None:
            return round(qty, _FALLBACK_DECIMALS)
        # Floor to nearest qtyStep (never round up — avoids over-ordering)
        steps = math.floor(qty / info.qty_step)
        return round(steps * info.qty_step, info.qty_decimals)

    async def round_price(self, symbol: str, price: float) -> float:
        """Round price to symbol's tickSize."""
        info = await self.get(symbol)
        if info is None:
            return round(price, _FALLBACK_DECIMALS)
        steps = round(price / info.tick_size)
        return round(steps * info.tick_size, info.price_decimals)

    def clear(self, symbol: Optional[str] = None) -> None:
        """Evict one symbol or entire cache."""
        if symbol:
            self._cache.pop(symbol, None)
        else:
            self._cache.clear()

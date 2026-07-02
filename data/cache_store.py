"""
QuantLuna — CacheStore
Sprint 26 (initial) + Sprint 28 (unified fetcher factory)

High-level cache store — auto-routes la Bybit sau Binance fetcher
bazat pe EXCHANGE env var (sau explicit).

Usage:
    from data.cache_store import CacheStore
    store = CacheStore()               # EXCHANGE env -> bybit sau binance
    store = CacheStore(exchange="bybit")  # explicit

    y, x = store.fetch_pair_for_optimizer(
        sym_y="BTCUSDT", sym_x="ETHUSDT",
        interval="60", start="2024-01-01", end="2024-12-31",
    )
    # -> pd.Series ready for WalkForwardOptimizer
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from data.fetcher_factory import get_fetcher

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = os.getenv("DATA_CACHE_DIR", "data/cache")


class CacheStore:
    """
    Unified cache store: Bybit sau Binance, acelasi API.
    """

    def __init__(
        self,
        exchange:  Optional[str] = None,
        cache_dir: str   = _DEFAULT_CACHE_DIR,
        ttl_hours: float = 24.0,
    ) -> None:
        self._fetcher  = get_fetcher(exchange=exchange, cache_dir=cache_dir, ttl_hours=ttl_hours)
        self._cache_dir = Path(cache_dir)
        self._exchange  = exchange or os.getenv("EXCHANGE", "bybit")

    def fetch_pair_for_optimizer(
        self,
        sym_y:    str,
        sym_x:    str,
        interval: str  = "60",
        start:    Optional[str] = None,
        end:      Optional[str] = None,
        force_refresh: bool = False,
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Fetch aligned close price series.
        Returns (y_close, x_close) gata pentru WalkForwardOptimizer.run().
        interval: universal alias ("1h","4h","1d") sau exchange-specific ("60","240","D")
        """
        y, x = self._fetcher.fetch_pair(
            sym_y=sym_y, sym_x=sym_x, interval=interval,
            start=start, end=end, force_refresh=force_refresh,
        )
        logger.info(
            f"CacheStore [{self._exchange}]: {sym_y}/{sym_x} {interval} — {len(y)} bare aliniate"
        )
        return y, x

    def fetch(
        self,
        symbol:   str,
        interval: str  = "60",
        start:    Optional[str] = None,
        end:      Optional[str] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Fetch full OHLCV DataFrame for a single symbol."""
        return self._fetcher.fetch(symbol, interval, start, end, force_refresh)

    def list_cache(self) -> List[dict]:
        return self._fetcher.list_cache()

    def stats(self) -> Dict:
        items       = self.list_cache()
        total_bytes = sum(i.get("size_bytes", 0) for i in items)
        total_bars  = sum(i.get("n_bars", 0)     for i in items)
        return {
            "exchange":    self._exchange,
            "n_datasets":  len(items),
            "total_bars":  total_bars,
            "total_mb":    round(total_bytes / 1024 / 1024, 2),
            "cache_dir":   str(self._cache_dir),
        }

    def delete_cache(self, symbol: str, interval: Optional[str] = None) -> int:
        return self._fetcher.delete_cache(symbol, interval)

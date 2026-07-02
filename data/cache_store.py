"""
QuantLuna — CacheStore
Sprint 26

Central registry + helper pentru cache-ul de date istorice.
Wraps BinanceHistoricalFetcher cu:
  - Singleton instance per cache_dir
  - Convenience methods pentru optimizer workflow
  - Cache statistics

Usage:
    from data.cache_store import CacheStore
    store = CacheStore()  # uses DATA_CACHE_DIR env or 'data/cache'

    # Fetch + cache BTCUSDT/ETHUSDT 1h for 2024:
    y, x = store.fetch_pair_for_optimizer(
        sym_y="BTCUSDT", sym_x="ETHUSDT",
        interval="1h", start="2024-01-01", end="2024-12-31",
    )
    from backtest.walk_forward_optimizer import WalkForwardOptimizer
    result = WalkForwardOptimizer().run(y=y, x=x)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from data.historical_fetcher import BinanceHistoricalFetcher

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = os.getenv("DATA_CACHE_DIR", "data/cache")


class CacheStore:
    """
    High-level cache store for QuantLuna data needs.
    """

    def __init__(
        self,
        cache_dir: str = _DEFAULT_CACHE_DIR,
        ttl_hours: float = 24.0,
    ) -> None:
        self._fetcher = BinanceHistoricalFetcher(cache_dir=cache_dir, ttl_hours=ttl_hours)
        self._cache_dir = Path(cache_dir)

    def fetch_pair_for_optimizer(
        self,
        sym_y:    str,
        sym_x:    str,
        interval: str = "1h",
        start:    Optional[str] = None,
        end:      Optional[str] = None,
        force_refresh: bool = False,
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Fetch aligned close price series for sym_y and sym_x.
        Returns (y_close, x_close) ready for WalkForwardOptimizer.run().
        """
        y, x = self._fetcher.fetch_pair(
            sym_y=sym_y, sym_x=sym_x, interval=interval,
            start=start, end=end, force_refresh=force_refresh,
        )
        logger.info(f"CacheStore: {sym_y}/{sym_x} {interval} — {len(y)} aligned bars")
        return y, x

    def fetch(
        self,
        symbol:   str,
        interval: str = "1h",
        start:    Optional[str] = None,
        end:      Optional[str] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Fetch full OHLCV DataFrame for a single symbol."""
        return self._fetcher.fetch(symbol, interval, start, end, force_refresh)

    def list_cache(self) -> List[dict]:
        """List all cached datasets with metadata and file size."""
        return self._fetcher.list_cache()

    def stats(self) -> Dict:
        """Return cache statistics."""
        items = self.list_cache()
        total_bytes = sum(i.get("size_bytes", 0) for i in items)
        total_bars  = sum(i.get("n_bars", 0) for i in items)
        return {
            "n_datasets":   len(items),
            "total_bars":   total_bars,
            "total_mb":     round(total_bytes / 1024 / 1024, 2),
            "cache_dir":    str(self._cache_dir),
        }

    def delete_cache(self, symbol: str, interval: Optional[str] = None) -> int:
        """Delete cached files for symbol. Returns number of files deleted."""
        return self._fetcher.delete_cache(symbol, interval)

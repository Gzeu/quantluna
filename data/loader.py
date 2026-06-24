"""
QuantLuna — DataLoader v2
Async OHLCV fetcher: CCXT + parquet cache + retry + staleness check + multi-symbol alignment.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

try:
    import ccxt.async_support as ccxt_async
except ImportError:  # pragma: no cover
    ccxt_async = None

CACHE_DIR = Path("./data/cache")
_TIMEFRAME_SECONDS: Dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "8h": 28800, "1d": 86400,
}


def _cache_path(exchange_id: str, symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return CACHE_DIR / f"{exchange_id}_{safe}_{timeframe}.parquet"


def _is_stale(path: Path, timeframe: str, stale_multiplier: float = 2.0) -> bool:
    """Cache is stale if file mtime older than stale_multiplier * bar_seconds."""
    bar_seconds = _TIMEFRAME_SECONDS.get(timeframe, 3600)
    return (time.time() - path.stat().st_mtime) > stale_multiplier * bar_seconds


def _ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df["returns"] = df["close"].pct_change()
    df["log_close"] = np.log(df["close"].clip(lower=1e-12))
    return df


class DataLoader:
    """
    Async OHLCV fetcher with local parquet cache and retry logic.

    Parameters
    ----------
    exchange_id    : ccxt exchange id ('binance', 'bybit')
    timeframe      : bar timeframe string ('1h', '4h', '1d', ...)
    use_cache      : serve from parquet cache when fresh
    market_type    : 'future' | 'spot'
    max_retries    : number of fetch retries on transient errors
    retry_delay    : seconds between retries (exponential backoff x attempt)
    stale_multiplier: cache considered stale after N * bar_seconds
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        timeframe: str = "1h",
        use_cache: bool = True,
        market_type: str = "future",
        max_retries: int = 3,
        retry_delay: float = 2.0,
        stale_multiplier: float = 2.0,
    ) -> None:
        self.exchange_id = exchange_id
        self.timeframe = timeframe
        self.use_cache = use_cache
        self.market_type = market_type
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.stale_multiplier = stale_multiplier
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
        """
        Fetch OHLCV for *symbol*.  Returns DataFrame:
          columns: open, high, low, close, volume, returns, log_close
          index  : DatetimeTZDtype UTC
        """
        path = _cache_path(self.exchange_id, symbol, self.timeframe)

        if self.use_cache and path.exists() and not _is_stale(path, self.timeframe, self.stale_multiplier):
            df = pd.read_parquet(path)
            logger.debug(f"Cache hit: {symbol} ({len(df)} bars)")
            return df

        df = await self._fetch_with_retry(symbol, limit)

        if self.use_cache:
            df.to_parquet(path)
            logger.debug(f"Cached -> {path}")

        return df

    async def fetch_multiple(
        self,
        symbols: List[str],
        limit: int = 1000,
        price_col: str = "close",
    ) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
        """
        Fetch *symbols* concurrently.

        Returns
        -------
        prices : DataFrame of aligned *price_col*, forward-filled gaps <= 3 bars
        raw    : dict symbol -> full OHLCV DataFrame
        """
        tasks = [self.fetch(s, limit) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        raw: Dict[str, pd.DataFrame] = {}
        for sym, res in zip(symbols, results):
            if isinstance(res, Exception):
                logger.error(f"Fetch failed {sym}: {res}")
            else:
                raw[sym] = res

        if not raw:
            raise RuntimeError("All symbol fetches failed")

        prices = pd.DataFrame({sym: df[price_col] for sym, df in raw.items()})
        prices = prices.ffill(limit=3).dropna()

        logger.info(f"Aligned prices: {len(prices)} bars x {len(prices.columns)} symbols")
        return prices, raw

    def load_from_parquet(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load cache directly (offline / test mode)."""
        path = _cache_path(self.exchange_id, symbol, self.timeframe)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        logger.debug(f"Loaded from parquet: {symbol} ({len(df)} bars)")
        return df

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_with_retry(self, symbol: str, limit: int) -> pd.DataFrame:
        if ccxt_async is None:
            raise ImportError("ccxt not installed -- pip install ccxt")

        last_exc: Exception = RuntimeError("no attempt made")
        for attempt in range(1, self.max_retries + 1):
            exchange = None
            try:
                exchange_cls = getattr(ccxt_async, self.exchange_id)
                exchange = exchange_cls({"options": {"defaultType": self.market_type}})
                ohlcv = await exchange.fetch_ohlcv(symbol, self.timeframe, limit=limit)
                df = _ohlcv_to_df(ohlcv)
                logger.info(f"Fetched {symbol}: {len(df)} bars ({self.timeframe})")
                return df
            except Exception as exc:
                last_exc = exc
                wait = self.retry_delay * attempt
                logger.warning(
                    f"Fetch attempt {attempt}/{self.max_retries} failed for {symbol}: {exc}. "
                    f"Retry in {wait:.1f}s"
                )
                await asyncio.sleep(wait)
            finally:
                if exchange is not None:
                    try:
                        await exchange.close()
                    except Exception:
                        pass

        raise RuntimeError(
            f"Failed to fetch {symbol} after {self.max_retries} attempts: {last_exc}"
        ) from last_exc

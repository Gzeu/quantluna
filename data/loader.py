"""
QuantLuna — Data Loader

Fetches OHLCV data from exchanges via CCXT.
Supports:
  - Binance futures (mark price)
  - Bybit linear perpetuals
  - Caching to parquet
"""
import os
import asyncio
from pathlib import Path
from typing import List, Optional
import pandas as pd
import numpy as np
from loguru import logger

try:
    import ccxt.async_support as ccxt_async
except ImportError:
    ccxt_async = None


CACHE_DIR = Path("./data/cache")


class DataLoader:
    """
    Async OHLCV fetcher with local parquet cache.

    Parameters
    ----------
    exchange_id : ccxt exchange id
    timeframe   : OHLCV timeframe string (e.g., '1h', '4h', '1d')
    use_cache   : Load from local cache if available
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        timeframe: str = "1h",
        use_cache: bool = True,
        market_type: str = "future",
    ):
        self.exchange_id = exchange_id
        self.timeframe = timeframe
        self.use_cache = use_cache
        self.market_type = market_type
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def fetch(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
        """
        Fetch OHLCV for symbol. Returns DataFrame with OHLCV + returns columns.
        """
        cache_path = CACHE_DIR / f"{self.exchange_id}_{symbol.replace('/', '_')}_{self.timeframe}.parquet"

        if self.use_cache and cache_path.exists():
            df = pd.read_parquet(cache_path)
            logger.debug(f"Cache hit: {symbol} ({len(df)} bars)")
            return df

        if ccxt_async is None:
            raise ImportError("ccxt not installed")

        exchange_class = getattr(ccxt_async, self.exchange_id)
        exchange = exchange_class({"options": {"defaultType": self.market_type}})

        try:
            ohlcv = await exchange.fetch_ohlcv(symbol, self.timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            df["returns"] = df["close"].pct_change()
            df["log_close"] = np.log(df["close"])

            if self.use_cache:
                df.to_parquet(cache_path)
                logger.debug(f"Cached: {symbol} → {cache_path}")

        finally:
            await exchange.close()

        logger.info(f"Fetched {symbol}: {len(df)} bars ({self.timeframe})")
        return df

    async def fetch_multiple(self, symbols: List[str], limit: int = 1000) -> pd.DataFrame:
        """
        Fetch multiple symbols and return aligned close price DataFrame.
        """
        tasks = [self.fetch(s, limit) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        closes = {}
        for sym, res in zip(symbols, results):
            if isinstance(res, Exception):
                logger.error(f"Failed to fetch {sym}: {res}")
            else:
                closes[sym] = res["close"]

        df = pd.DataFrame(closes).dropna()
        logger.info(f"Aligned close prices: {len(df)} bars x {len(df.columns)} symbols")
        return df

"""
data/market_data_cache.py  —  QuantLuna Market Data Cache

Sprint 12 — Local Parquet cache pentru date OHLCV:
  - Download via CCXT (Bybit, Binance, orice exchange suportat)
  - Salvare locală în format Parquet (Apache Arrow, compresie snappy)
  - Încărcare cu merge incremental — actualizează doar barele noi
  - Deduplicare automată și sortare cronologică
  - API simplu: load(), refresh(), exists(), info()
  - Cache directory configurabil (default: ~/.quantluna/cache/)
  - Suportă multiple exchange-uri și timeframes simultan

Structura cache:
    ~/.quantluna/cache/<exchange>/<symbol>/<timeframe>.parquet

Usage:
    from data.market_data_cache import MarketDataCache

    cache = MarketDataCache()  # sau MarketDataCache(cache_dir="./data/cache")

    # Descarcă și cachează (sau încarcă din cache dacă există)
    ohlcv = cache.load("BTCUSDT", "bybit", "1h", days=180)

    # Refresh incremental — descarcă doar barele noi
    ohlcv = cache.refresh("BTCUSDT", "bybit", "1h")

    # Info despre cache
    print(cache.info("BTCUSDT", "bybit", "1h"))
    # {'symbol': 'BTCUSDT', 'exchange': 'bybit', 'timeframe': '1h',
    #  'bars': 4320, 'from': '2025-01-01', 'to': '2025-07-01',
    #  'size_mb': 0.42, 'cached': True}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path.home() / ".quantluna" / "cache"
_OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
_PARQUET_ENGINE = "pyarrow"
_PARQUET_COMPRESSION = "snappy"


class MarketDataCache:
    """
    Local Parquet-based OHLCV cache.

    Thread-safe pentru read; nu thread-safe pentru write concurrent
    pe același simbol (folosiți un singur process de download).
    """

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._root = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info(f"MarketDataCache initialized at {self._root}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        symbol: str,
        exchange: str,
        timeframe: str = "1h",
        days: int = 365,
        refresh_if_stale: bool = True,
        stale_threshold_h: float = 4.0,
    ) -> pd.DataFrame:
        """
        Load OHLCV from cache or download if missing/stale.

        Args:
            symbol:             e.g. 'BTCUSDT' sau 'BTC/USDT'
            exchange:           e.g. 'bybit', 'binance'
            timeframe:          e.g. '1h', '15m', '4h'
            days:               number of days to fetch if downloading
            refresh_if_stale:   auto-refresh când ultimul bar > stale_threshold_h
            stale_threshold_h:  ore după care cache-ul este considerat stale

        Returns:
            pd.DataFrame cu columns: open, high, low, close, volume
            index: DatetimeIndex UTC
        """
        path = self._path(symbol, exchange, timeframe)

        if path.exists():
            df = self._read(path)
            if refresh_if_stale and self._is_stale(df, stale_threshold_h):
                logger.info(f"Cache stale for {symbol} {exchange} {timeframe} — refreshing")
                df = self._download_and_merge(df, symbol, exchange, timeframe)
                self._write(df, path)
            return df

        logger.info(f"Cache miss: {symbol} {exchange} {timeframe} — downloading {days} days")
        df = self._download(symbol, exchange, timeframe, days=days)
        self._write(df, path)
        return df

    def refresh(
        self,
        symbol: str,
        exchange: str,
        timeframe: str = "1h",
    ) -> pd.DataFrame:
        """
        Force incremental refresh — descarcă doar barele noi și merge.
        Dacă nu există cache, descarcă 365 zile.
        """
        path = self._path(symbol, exchange, timeframe)
        if path.exists():
            existing = self._read(path)
            df = self._download_and_merge(existing, symbol, exchange, timeframe)
        else:
            df = self._download(symbol, exchange, timeframe, days=365)
        self._write(df, path)
        logger.info(f"Refreshed {symbol} {exchange} {timeframe}: {len(df)} bars")
        return df

    def exists(self, symbol: str, exchange: str, timeframe: str = "1h") -> bool:
        """Returns True dacă cache-ul există pe disk."""
        return self._path(symbol, exchange, timeframe).exists()

    def info(self, symbol: str, exchange: str, timeframe: str = "1h") -> Dict:
        """Returns metadata despre fișierul cache."""
        path = self._path(symbol, exchange, timeframe)
        if not path.exists():
            return {"symbol": symbol, "exchange": exchange, "timeframe": timeframe, "cached": False}
        df = self._read(path)
        size_mb = path.stat().st_size / (1024 * 1024)
        return {
            "symbol": symbol,
            "exchange": exchange,
            "timeframe": timeframe,
            "bars": len(df),
            "from": str(df.index[0].date()) if len(df) else None,
            "to": str(df.index[-1].date()) if len(df) else None,
            "last_bar": str(df.index[-1]) if len(df) else None,
            "size_mb": round(size_mb, 3),
            "cached": True,
            "path": str(path),
        }

    def list_cached(self) -> List[Dict]:
        """Lists all cached symbols."""
        results = []
        for parquet_file in self._root.rglob("*.parquet"):
            parts = parquet_file.relative_to(self._root).parts
            if len(parts) == 3:
                exchange, symbol, tf_file = parts
                timeframe = tf_file.replace(".parquet", "")
                results.append(self.info(symbol, exchange, timeframe))
        return results

    def clear(self, symbol: str, exchange: str, timeframe: str = "1h") -> None:
        """Delete cache file for a specific symbol."""
        path = self._path(symbol, exchange, timeframe)
        if path.exists():
            path.unlink()
            logger.info(f"Cache cleared: {symbol} {exchange} {timeframe}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path(self, symbol: str, exchange: str, timeframe: str) -> Path:
        sym_clean = symbol.replace("/", "").upper()
        return self._root / exchange.lower() / sym_clean / f"{timeframe}.parquet"

    def _read(self, path: Path) -> pd.DataFrame:
        df = pd.read_parquet(path, engine=_PARQUET_ENGINE)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        elif df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return df.sort_index()

    def _write(self, df: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_parquet(path, engine=_PARQUET_ENGINE, compression=_PARQUET_COMPRESSION)
        logger.debug(f"Cache written: {path} ({len(df)} bars)")

    def _is_stale(self, df: pd.DataFrame, threshold_h: float) -> bool:
        if df.empty:
            return True
        last_bar = df.index[-1]
        now = pd.Timestamp.now(tz="UTC")
        age_h = (now - last_bar).total_seconds() / 3600.0
        return age_h > threshold_h

    def _download(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
        days: int = 365,
        since: Optional[pd.Timestamp] = None,
    ) -> pd.DataFrame:
        """Download OHLCV via CCXT."""
        try:
            import ccxt
        except ImportError:
            raise ImportError("ccxt not installed. Run: pip install ccxt")

        exchange_class = getattr(ccxt, exchange.lower(), None)
        if exchange_class is None:
            raise ValueError(f"Unknown CCXT exchange: {exchange}")

        ex = exchange_class({"enableRateLimit": True})

        sym_ccxt = self._to_ccxt_symbol(symbol)
        since_ms = (
            int(since.timestamp() * 1000)
            if since
            else int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        )

        all_bars: List[List] = []
        limit = 1000
        logger.info(f"Downloading {sym_ccxt} {exchange} {timeframe} since {pd.Timestamp(since_ms, unit='ms', tz='UTC').date()}")

        while True:
            try:
                bars = ex.fetch_ohlcv(sym_ccxt, timeframe=timeframe, since=since_ms, limit=limit)
            except Exception as exc:
                logger.error(f"CCXT fetch error: {exc}")
                break
            if not bars:
                break
            all_bars.extend(bars)
            last_ts = bars[-1][0]
            if len(bars) < limit:
                break
            since_ms = last_ts + 1

        if not all_bars:
            logger.warning(f"No data downloaded for {symbol} {exchange} {timeframe}")
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(all_bars, columns=_OHLCV_COLUMNS)
        df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop(columns=["timestamp"])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        logger.info(f"Downloaded {len(df)} bars for {symbol} {exchange} {timeframe}")
        return df

    def _download_and_merge(
        self,
        existing: pd.DataFrame,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> pd.DataFrame:
        """Incremental download — only bars after last cached bar."""
        if existing.empty:
            return self._download(symbol, exchange, timeframe)
        last_bar = existing.index[-1]
        new_data = self._download(symbol, exchange, timeframe, since=last_bar)
        if new_data.empty:
            return existing
        combined = pd.concat([existing, new_data])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        new_bars = len(combined) - len(existing)
        logger.info(f"Merged {new_bars} new bars for {symbol} {exchange} {timeframe} (total: {len(combined)})")
        return combined

    @staticmethod
    def _to_ccxt_symbol(symbol: str) -> str:
        """Convert 'BTCUSDT' -> 'BTC/USDT:USDT' for perpetual, or 'BTC/USDT' for spot."""
        sym = symbol.upper().replace("-PERP", "").replace("PERP", "")
        if sym.endswith("USDT") and "/" not in sym:
            base = sym[:-4]
            return f"{base}/USDT:USDT"
        return sym

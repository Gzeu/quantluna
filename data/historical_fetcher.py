"""
QuantLuna — BinanceHistoricalFetcher
Sprint 26

Downloadă OHLCV klines de pe Binance REST API cu:
  - Paginare automată pe intervale mari de timp
  - Cache local Parquet (fallback CSV dacă pyarrow lipsește)
  - Auto-refresh la date expirate (configurable staleness TTL)
  - Rate limiting: max 1200 req/min (1 req / 50ms)
  - Progress logging per batch
  - Returnare pd.DataFrame cu coloane standardizate

Coloane output DataFrame:
  open_time, open, high, low, close, volume,
  close_time, quote_volume, n_trades, taker_buy_volume,
  taker_buy_quote_volume

Usage:
    from data.historical_fetcher import BinanceHistoricalFetcher
    fetcher = BinanceHistoricalFetcher(cache_dir="data/cache")
    df = fetcher.fetch(
        symbol="BTCUSDT",
        interval="1h",
        start="2024-01-01",
        end="2024-06-30",
    )
    # DataFrame cu 4344 bare ~

    # For optimizer:
    y = df_btc["close"]
    x = df_eth["close"]
    result = optimizer.run(y=y, x=x)

Env vars:
  BINANCE_BASE_URL   https://api.binance.com (override for testnet)
  DATA_CACHE_DIR     path to cache directory (default: data/cache)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_BINANCE_BASE    = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
_KLINES_ENDPOINT = f"{_BINANCE_BASE}/api/v3/klines"
_RATE_LIMIT_DELAY = 0.055   # 1200 req/min → 1 req/50ms (+ 5ms buffer)
_MAX_PER_REQUEST  = 1000    # Binance max limit per call
_DEFAULT_CACHE_DIR = os.getenv("DATA_CACHE_DIR", "data/cache")

_INTERVAL_MS: dict = {
    "1m":  60_000,       "3m":  180_000,      "5m":   300_000,
    "15m": 900_000,      "30m": 1_800_000,    "1h":  3_600_000,
    "2h":  7_200_000,    "4h":  14_400_000,   "6h":  21_600_000,
    "8h":  28_800_000,   "12h": 43_200_000,   "1d": 86_400_000,
    "3d":  259_200_000,  "1w":  604_800_000,
}

_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "n_trades",
    "taker_buy_volume", "taker_buy_quote_volume", "_ignore",
]
_FLOAT_COLS = ["open", "high", "low", "close", "volume",
               "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]


class BinanceHistoricalFetcher:
    """
    Downloads + caches Binance OHLCV klines.

    Parameters
    ----------
    cache_dir  : directory for local cache (Parquet files)
    ttl_hours  : hours before cached data is considered stale (default 24)
    """

    def __init__(
        self,
        cache_dir: str = _DEFAULT_CACHE_DIR,
        ttl_hours: float = 24.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_hours = ttl_hours
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(
        self,
        symbol:   str,
        interval: str = "1h",
        start:    Optional[str] = None,
        end:      Optional[str] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data. Returns cached data if fresh, otherwise downloads.

        Parameters
        ----------
        symbol        : e.g. "BTCUSDT"
        interval      : Binance interval string ("1h", "4h", "1d", ...)
        start         : ISO date string or None (default: 30 days ago)
        end           : ISO date string or None (default: now)
        force_refresh : ignore cache and re-download
        """
        start_ms, end_ms = self._parse_range(start, end, interval)
        cache_key = self._cache_key(symbol, interval, start_ms, end_ms)
        cache_path = self.cache_dir / f"{cache_key}.parquet"
        meta_path  = self.cache_dir / f"{cache_key}.meta.json"

        if not force_refresh and self._cache_is_fresh(cache_path, meta_path):
            logger.info(f"[Cache HIT] {cache_key}")
            return self._load_cache(cache_path)

        logger.info(f"[Fetch] {symbol} {interval} {start} → {end}")
        df = self._download(symbol, interval, start_ms, end_ms)
        self._save_cache(df, cache_path, meta_path, symbol, interval, start_ms, end_ms)
        return df

    def fetch_pair(
        self,
        sym_y:    str,
        sym_x:    str,
        interval: str = "1h",
        start:    Optional[str] = None,
        end:      Optional[str] = None,
        force_refresh: bool = False,
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Fetch close prices for a pair. Returns (y_close, x_close) aligned by open_time.
        Ready for direct use with WalkForwardOptimizer.
        """
        df_y = self.fetch(sym_y, interval, start, end, force_refresh)
        df_x = self.fetch(sym_x, interval, start, end, force_refresh)
        merged = df_y[["open_time", "close"]].rename(columns={"close": sym_y}).merge(
            df_x[["open_time", "close"]].rename(columns={"close": sym_x}),
            on="open_time", how="inner",
        )
        return merged[sym_y], merged[sym_x]

    def list_cache(self) -> List[dict]:
        """List all cached datasets with metadata."""
        result = []
        for meta_path in sorted(self.cache_dir.glob("*.meta.json")):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                parquet_path = meta_path.with_suffix("").with_suffix(".parquet")
                csv_path     = meta_path.with_suffix("").with_suffix(".csv")
                size_bytes   = (
                    parquet_path.stat().st_size if parquet_path.exists()
                    else csv_path.stat().st_size if csv_path.exists()
                    else 0
                )
                meta["size_bytes"] = size_bytes
                meta["cache_key"]  = meta_path.stem
                result.append(meta)
            except Exception as e:
                logger.warning(f"list_cache: bad meta {meta_path}: {e}")
        return result

    def delete_cache(
        self,
        symbol:   str,
        interval: Optional[str] = None,
    ) -> int:
        """Delete cached files for symbol (optionally filtered by interval). Returns count deleted."""
        prefix = symbol.upper()
        if interval:
            prefix += f"_{interval}"
        deleted = 0
        for path in list(self.cache_dir.glob(f"{prefix}*")):
            path.unlink(missing_ok=True)
            deleted += 1
        return deleted

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _download(
        self,
        symbol:   str,
        interval: str,
        start_ms: int,
        end_ms:   int,
    ) -> pd.DataFrame:
        import requests
        all_bars: List[list] = []
        current_ms = start_ms
        batch = 0

        while current_ms < end_ms:
            params = {
                "symbol":    symbol.upper(),
                "interval":  interval,
                "startTime": current_ms,
                "endTime":   end_ms,
                "limit":     _MAX_PER_REQUEST,
            }
            try:
                resp = requests.get(_KLINES_ENDPOINT, params=params, timeout=15)
                resp.raise_for_status()
                bars = resp.json()
            except Exception as e:
                logger.error(f"Binance klines request failed: {e}")
                raise

            if not bars:
                break

            all_bars.extend(bars)
            batch += 1
            last_close_ms = int(bars[-1][6])  # close_time of last bar
            current_ms = last_close_ms + 1

            logger.info(
                f"  Batch {batch}: {len(bars)} bars | "
                f"total={len(all_bars)} | "
                f"last={datetime.fromtimestamp(bars[-1][0]/1000, tz=timezone.utc).date()}"
            )

            if len(bars) < _MAX_PER_REQUEST:
                break  # last page

            time.sleep(_RATE_LIMIT_DELAY)

        if not all_bars:
            logger.warning(f"No data returned for {symbol} {interval}")
            return self._empty_df()

        df = pd.DataFrame(all_bars, columns=_COLUMNS).drop(columns=["_ignore"])
        df["open_time"]  = pd.to_datetime(df["open_time"],  unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        df["n_trades"]   = df["n_trades"].astype(int)
        for col in _FLOAT_COLS:
            df[col] = df[col].astype(float)
        df = df.sort_values("open_time").reset_index(drop=True)
        logger.info(f"Downloaded {len(df)} bars for {symbol} {interval}")
        return df

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(symbol: str, interval: str, start_ms: int, end_ms: int) -> str:
        return f"{symbol.upper()}_{interval}_{start_ms}_{end_ms}"

    def _cache_is_fresh(self, cache_path: Path, meta_path: Path) -> bool:
        if not cache_path.exists() and not cache_path.with_suffix(".csv").exists():
            return False
        if not meta_path.exists():
            return False
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            fetched_at = meta.get("fetched_at", 0)
            age_hours  = (time.time() - fetched_at) / 3600
            return age_hours < self.ttl_hours
        except Exception:
            return False

    def _save_cache(
        self,
        df:         pd.DataFrame,
        cache_path: Path,
        meta_path:  Path,
        symbol:     str,
        interval:   str,
        start_ms:   int,
        end_ms:     int,
    ) -> None:
        try:
            df.to_parquet(cache_path, index=False)
        except Exception:
            csv_path = cache_path.with_suffix(".csv")
            df.to_csv(csv_path, index=False)
            logger.info(f"Saved CSV fallback: {csv_path}")
        meta = {
            "symbol":     symbol.upper(),
            "interval":   interval,
            "start_ms":   start_ms,
            "end_ms":     end_ms,
            "n_bars":     len(df),
            "fetched_at": time.time(),
            "fetched_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    def _load_cache(self, cache_path: Path) -> pd.DataFrame:
        if cache_path.exists():
            return pd.read_parquet(cache_path)
        csv_path = cache_path.with_suffix(".csv")
        if csv_path.exists():
            return pd.read_csv(csv_path, parse_dates=["open_time", "close_time"])
        raise FileNotFoundError(f"Cache file not found: {cache_path}")

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(columns=[c for c in _COLUMNS if c != "_ignore"])

    @staticmethod
    def _parse_range(
        start: Optional[str],
        end:   Optional[str],
        interval: str,
    ) -> Tuple[int, int]:
        """Convert ISO date strings to millisecond timestamps."""
        tz = timezone.utc
        if end is None:
            end_dt = datetime.now(tz)
        else:
            end_dt = datetime.fromisoformat(end).replace(tzinfo=tz) if "T" not in end \
                     else datetime.fromisoformat(end).astimezone(tz)
            end_dt = end_dt.replace(hour=23, minute=59, second=59) if "T" not in end else end_dt

        if start is None:
            interval_ms = _INTERVAL_MS.get(interval, 3_600_000)
            start_dt = end_dt - timedelta(milliseconds=interval_ms * 720)  # ~30 days of 1h bars
        else:
            start_dt = datetime.fromisoformat(start).replace(tzinfo=tz) if "T" not in start \
                       else datetime.fromisoformat(start).astimezone(tz)

        return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)

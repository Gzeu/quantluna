"""
QuantLuna — BybitHistoricalFetcher
Sprint 28

Descarca OHLCV klines de pe Bybit V5 REST API cu:
  - Endpoint: GET /v5/market/kline
  - Categorii: spot | linear | inverse
  - Paginare automata pe intervale mari (limit=200 per request)
  - Cache local Parquet (fallback CSV)
  - TTL configurable (default 24h)
  - Rate limiting: max 1200 req/min
  - DataFrame schema IDENTIC cu BinanceHistoricalFetcher
    (open_time, open, high, low, close, volume, ...)

Bybit kline raspuns (list per bar):
  [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]

Coloane output DataFrame (compatibil cu WalkForwardOptimizer):
  open_time, open, high, low, close, volume, quote_volume,
  n_trades(=0 Bybit nu ofera), close_time

Env vars:
  BYBIT_BASE_URL    https://api.bybit.com (default)
  BYBIT_CATEGORY    spot | linear | inverse (default: linear)
  BYBIT_TESTNET     true | false
  DATA_CACHE_DIR    path to cache (default: data/cache)

Usage:
    from data.bybit_fetcher import BybitHistoricalFetcher
    fetcher = BybitHistoricalFetcher(category="linear")
    df = fetcher.fetch(
        symbol="BTCUSDT",
        interval="60",         # Bybit: "1","3","5","15","30","60","120","240","D","W"
        start="2024-01-01",
        end="2024-06-30",
    )
    y = df["close"]  # pregătit pentru optimizer
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_BYBIT_BASE     = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
_KLINES_URL     = f"{_BYBIT_BASE}/v5/market/kline"
_TESTNET_BASE   = "https://api-testnet.bybit.com"
_RATE_DELAY     = 0.055          # 1200 req/min safe
_MAX_PER_CALL   = 200            # Bybit max limit per request
_DEFAULT_CACHE  = os.getenv("DATA_CACHE_DIR", "data/cache")

# Bybit interval string → milliseconds
_INTERVAL_MS: Dict[str, int] = {
    "1":   60_000,       "3":   180_000,      "5":    300_000,
    "15":  900_000,      "30":  1_800_000,    "60":  3_600_000,
    "120": 7_200_000,   "240": 14_400_000,   "360": 21_600_000,
    "720": 43_200_000,  "D":   86_400_000,   "W":  604_800_000,
    "M":  2_592_000_000,
}

# Bybit ↔ Binance interval aliases (pentru CacheStore compatibil)
_BYBIT_ALIASES: Dict[str, str] = {
    "1h": "60", "4h": "240", "1d": "D", "1w": "W",
    "15m": "15", "30m": "30", "5m": "5", "3m": "3", "1m": "1",
}

_FLOAT_COLS = ["open", "high", "low", "close", "volume", "quote_volume"]


class BybitHistoricalFetcher:
    """
    Descarca + cacheaza OHLCV klines de pe Bybit V5.
    API compatibil cu BinanceHistoricalFetcher.
    """

    def __init__(
        self,
        cache_dir: str   = _DEFAULT_CACHE,
        ttl_hours: float = 24.0,
        category:  str   = "",
        testnet:   bool  = False,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_hours = ttl_hours
        self.category  = category or os.getenv("BYBIT_CATEGORY", "linear")
        self.testnet   = testnet  or os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        self._base_url = _TESTNET_BASE if self.testnet else _BYBIT_BASE
        self._klines_url = f"{self._base_url}/v5/market/kline"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API (compatibil cu BinanceHistoricalFetcher)
    # ------------------------------------------------------------------

    def fetch(
        self,
        symbol:        str,
        interval:      str  = "60",
        start:         Optional[str] = None,
        end:           Optional[str] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV. Interval: Bybit format ("60","240","D") sau alias ("1h","4h","1d").
        Returneaza DataFrame cu coloane: open_time, open, high, low, close, volume, ...
        """
        interval   = _BYBIT_ALIASES.get(interval, interval)
        start_ms, end_ms = self._parse_range(start, end, interval)
        cache_key  = self._cache_key(symbol, interval, start_ms, end_ms)
        cache_path = self.cache_dir / f"{cache_key}.parquet"
        meta_path  = self.cache_dir / f"{cache_key}.meta.json"

        if not force_refresh and self._cache_is_fresh(cache_path, meta_path):
            logger.info(f"[Bybit Cache HIT] {cache_key}")
            return self._load_cache(cache_path)

        logger.info(f"[Bybit Fetch] {symbol} {interval} {start} -> {end} (cat={self.category})")
        df = self._download(symbol, interval, start_ms, end_ms)
        self._save_cache(df, cache_path, meta_path, symbol, interval, start_ms, end_ms)
        return df

    def fetch_pair(
        self,
        sym_y:    str,
        sym_x:    str,
        interval: str  = "60",
        start:    Optional[str] = None,
        end:      Optional[str] = None,
        force_refresh: bool = False,
    ) -> Tuple[pd.Series, pd.Series]:
        """Fetch aligned close prices for a pair. Returns (y_close, x_close)."""
        df_y = self.fetch(sym_y, interval, start, end, force_refresh)
        df_x = self.fetch(sym_x, interval, start, end, force_refresh)
        merged = df_y[["open_time", "close"]].rename(columns={"close": sym_y}).merge(
            df_x[["open_time", "close"]].rename(columns={"close": sym_x}),
            on="open_time", how="inner",
        )
        return merged[sym_y], merged[sym_x]

    def list_cache(self) -> List[dict]:
        result = []
        for meta_path in sorted(self.cache_dir.glob("*.meta.json")):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                pq  = meta_path.with_suffix("").with_suffix(".parquet")
                csv = meta_path.with_suffix("").with_suffix(".csv")
                meta["size_bytes"] = pq.stat().st_size if pq.exists() \
                    else csv.stat().st_size if csv.exists() else 0
                meta["cache_key"]  = meta_path.stem
                result.append(meta)
            except Exception as e:
                logger.warning(f"list_cache meta error {meta_path}: {e}")
        return result

    def delete_cache(self, symbol: str, interval: Optional[str] = None) -> int:
        prefix = f"BYBIT_{symbol.upper()}"
        if interval:
            prefix += f"_{_BYBIT_ALIASES.get(interval, interval)}"
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
        # Bybit klines are returned newest-first; we paginate backwards
        # by setting end = last open_time - 1ms each batch
        current_end_ms = end_ms
        batch = 0

        while True:
            params = {
                "category": self.category,
                "symbol":   symbol.upper(),
                "interval": interval,
                "start":    start_ms,
                "end":      current_end_ms,
                "limit":    _MAX_PER_CALL,
            }
            try:
                resp = requests.get(self._klines_url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"Bybit klines request failed: {e}")
                raise

            ret_code = data.get("retCode", -1)
            if ret_code != 0:
                raise RuntimeError(f"Bybit API error {ret_code}: {data.get('retMsg', '')}")

            bars = data.get("result", {}).get("list", [])
            if not bars:
                break

            # Bybit returns: [startTime, open, high, low, close, volume, turnover]
            # Newest first -> reverse for chronological order
            all_bars.extend(reversed(bars))
            batch += 1

            oldest_ts = int(bars[-1][0])  # oldest bar in this batch (last item after reverse)
            logger.info(
                f"  Bybit batch {batch}: {len(bars)} bars | "
                f"total={len(all_bars)} | "
                f"oldest={datetime.fromtimestamp(oldest_ts/1000, tz=timezone.utc).date()}"
            )

            if oldest_ts <= start_ms:
                break
            if len(bars) < _MAX_PER_CALL:
                break

            current_end_ms = oldest_ts - 1
            time.sleep(_RATE_DELAY)

        if not all_bars:
            logger.warning(f"No Bybit data for {symbol} {interval}")
            return self._empty_df()

        df = pd.DataFrame(all_bars, columns=[
            "open_time", "open", "high", "low", "close", "volume", "quote_volume"
        ])
        df["open_time"]   = pd.to_datetime(df["open_time"].astype(int), unit="ms", utc=True)
        df["close_time"]  = df["open_time"] + pd.Timedelta(milliseconds=_INTERVAL_MS.get(interval, 3_600_000) - 1)
        df["n_trades"]    = 0  # Bybit kline endpoint nu ofera n_trades
        for col in _FLOAT_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        # Filter to requested range
        start_dt = pd.Timestamp(start_ms, unit="ms", tz="UTC")
        end_dt   = pd.Timestamp(end_ms,   unit="ms", tz="UTC")
        df = df[(df["open_time"] >= start_dt) & (df["open_time"] <= end_dt)].reset_index(drop=True)
        logger.info(f"Bybit: downloaded {len(df)} bars for {symbol} {interval}")
        return df

    # ------------------------------------------------------------------
    # Cache helpers (identice cu BinanceHistoricalFetcher)
    # ------------------------------------------------------------------

    def _cache_key(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> str:
        return f"BYBIT_{symbol.upper()}_{interval}_{start_ms}_{end_ms}"

    def _cache_is_fresh(self, cache_path: Path, meta_path: Path) -> bool:
        if not cache_path.exists() and not cache_path.with_suffix(".csv").exists():
            return False
        if not meta_path.exists():
            return False
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            age_h = (time.time() - meta.get("fetched_at", 0)) / 3600
            return age_h < self.ttl_hours
        except Exception:
            return False

    def _save_cache(self, df, cache_path, meta_path, symbol, interval, start_ms, end_ms):
        try:
            df.to_parquet(cache_path, index=False)
        except Exception:
            df.to_csv(cache_path.with_suffix(".csv"), index=False)
        with open(meta_path, "w") as f:
            json.dump({
                "exchange":     "bybit",
                "category":     self.category,
                "symbol":       symbol.upper(),
                "interval":     interval,
                "start_ms":     start_ms,
                "end_ms":       end_ms,
                "n_bars":       len(df),
                "fetched_at":   time.time(),
                "fetched_at_iso": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

    def _load_cache(self, cache_path: Path) -> pd.DataFrame:
        if cache_path.exists():
            return pd.read_parquet(cache_path)
        csv = cache_path.with_suffix(".csv")
        if csv.exists():
            return pd.read_csv(csv, parse_dates=["open_time", "close_time"])
        raise FileNotFoundError(f"Cache not found: {cache_path}")

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "quote_volume", "close_time", "n_trades"
        ])

    @staticmethod
    def _parse_range(start, end, interval) -> Tuple[int, int]:
        tz = timezone.utc
        end_dt = datetime.now(tz) if end is None else (
            datetime.fromisoformat(end).replace(tzinfo=tz)
            if "T" not in end else datetime.fromisoformat(end).astimezone(tz)
        )
        if end is not None and "T" not in end:
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
        if start is None:
            iv_ms  = _INTERVAL_MS.get(interval, 3_600_000)
            start_dt = end_dt - timedelta(milliseconds=iv_ms * 720)
        else:
            start_dt = datetime.fromisoformat(start).replace(tzinfo=tz) if "T" not in start \
                       else datetime.fromisoformat(start).astimezone(tz)
        return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)

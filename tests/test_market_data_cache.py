"""
tests/test_market_data_cache.py  —  MarketDataCache unit tests

All CCXT network calls are mocked — no real download.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


def _make_bars(n: int = 20, start_ts_ms: int = None) -> list:
    if start_ts_ms is None:
        start_ts_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return [
        [start_ts_ms + i * 3_600_000, 100 + i, 101 + i, 99 + i, 100.5 + i, 500.0]
        for i in range(n)
    ]


class TestMarketDataCache:
    def test_cache_miss_downloads_and_writes(self, tmp_cache_dir, mock_ccxt_exchange):
        from data.market_data_cache import MarketDataCache

        with patch("data.market_data_cache.ccxt") as mock_ccxt:
            mock_ccxt.bybit.return_value = mock_ccxt_exchange
            mock_ccxt_exchange.fetch_ohlcv.side_effect = [_make_bars(20), []]

            cache = MarketDataCache(cache_dir=tmp_cache_dir)
            df = cache.load("BTCUSDT", "bybit", "1h", days=30)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 20
        assert isinstance(df.index, pd.DatetimeIndex)
        assert cache.exists("BTCUSDT", "bybit", "1h")

    def test_cache_hit_loads_from_disk(self, tmp_cache_dir, mock_ccxt_exchange):
        from data.market_data_cache import MarketDataCache

        with patch("data.market_data_cache.ccxt") as mock_ccxt:
            mock_ccxt.bybit.return_value = mock_ccxt_exchange
            mock_ccxt_exchange.fetch_ohlcv.side_effect = [_make_bars(20), []]

            cache = MarketDataCache(cache_dir=tmp_cache_dir)
            df1 = cache.load("BTCUSDT", "bybit", "1h", days=30, refresh_if_stale=False)
            # Second load — should read from disk without calling ccxt again
            mock_ccxt_exchange.fetch_ohlcv.reset_mock()
            df2 = cache.load("BTCUSDT", "bybit", "1h", days=30, refresh_if_stale=False)

        assert len(df2) == len(df1)
        mock_ccxt_exchange.fetch_ohlcv.assert_not_called()

    def test_info_returns_metadata(self, tmp_cache_dir, mock_ccxt_exchange):
        from data.market_data_cache import MarketDataCache

        with patch("data.market_data_cache.ccxt") as mock_ccxt:
            mock_ccxt.bybit.return_value = mock_ccxt_exchange
            mock_ccxt_exchange.fetch_ohlcv.side_effect = [_make_bars(10), []]

            cache = MarketDataCache(cache_dir=tmp_cache_dir)
            cache.load("BTCUSDT", "bybit", "1h", days=7, refresh_if_stale=False)

        info = cache.info("BTCUSDT", "bybit", "1h")
        assert info["cached"] is True
        assert info["bars"] == 10
        assert info["size_mb"] > 0

    def test_info_not_cached(self, tmp_cache_dir):
        from data.market_data_cache import MarketDataCache
        cache = MarketDataCache(cache_dir=tmp_cache_dir)
        info = cache.info("XYZUSDT", "bybit", "1h")
        assert info["cached"] is False

    def test_clear_removes_file(self, tmp_cache_dir, mock_ccxt_exchange):
        from data.market_data_cache import MarketDataCache

        with patch("data.market_data_cache.ccxt") as mock_ccxt:
            mock_ccxt.bybit.return_value = mock_ccxt_exchange
            mock_ccxt_exchange.fetch_ohlcv.side_effect = [_make_bars(5), []]

            cache = MarketDataCache(cache_dir=tmp_cache_dir)
            cache.load("BTCUSDT", "bybit", "1h", refresh_if_stale=False)

        assert cache.exists("BTCUSDT", "bybit", "1h")
        cache.clear("BTCUSDT", "bybit", "1h")
        assert not cache.exists("BTCUSDT", "bybit", "1h")

    def test_deduplication_on_write(self, tmp_cache_dir):
        from data.market_data_cache import MarketDataCache
        cache = MarketDataCache(cache_dir=tmp_cache_dir)
        bars = _make_bars(10)
        # Duplicate bars
        bars_dup = bars + bars
        ts_ms = [b[0] for b in bars_dup]
        idx = pd.to_datetime(ts_ms, unit="ms", utc=True)
        df = pd.DataFrame(
            [[b[1], b[2], b[3], b[4], b[5]] for b in bars_dup],
            index=idx,
            columns=["open", "high", "low", "close", "volume"]
        )
        path = cache._path("BTCUSDT", "bybit", "1h")
        cache._write(df, path)
        df_read = cache._read(path)
        assert len(df_read) == 10  # deduped

    def test_symbol_normalization(self, tmp_cache_dir):
        from data.market_data_cache import MarketDataCache
        cache = MarketDataCache(cache_dir=tmp_cache_dir)
        path1 = cache._path("BTCUSDT", "bybit", "1h")
        path2 = cache._path("btcusdt", "bybit", "1h")  # lowercase
        assert path1 == path2

"""
Tests for data/loader.py and data/funding.py -- Sprint 2
All tests use synthetic data / mocks; no live exchange calls.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from data.loader import DataLoader, _cache_path, _is_stale, _ohlcv_to_df, CACHE_DIR
from data.funding import FundingRateFetcher, annualized_funding_cost


# --- Helpers ------------------------------------------------------------------

def _make_ohlcv(n: int = 200, seed: int = 0) -> list:
    rng = np.random.default_rng(seed)
    ts_start = 1_700_000_000_000
    prices = 30_000 + np.cumsum(rng.normal(0, 50, n))
    rows = []
    for i in range(n):
        p = float(prices[i])
        rows.append([ts_start + i * 3_600_000, p * 0.999, p * 1.001, p * 0.998, p,
                     float(rng.integers(100, 500))])
    return rows


def _make_funding_history(n: int = 50) -> list:
    ts_start = 1_700_000_000_000
    return [
        {"timestamp": ts_start + i * 28_800_000, "fundingRate": 0.0001 + i * 0.000005}
        for i in range(n)
    ]


# --- _ohlcv_to_df -------------------------------------------------------------

class TestOhlcvToDf:
    def test_columns_present(self):
        df = _ohlcv_to_df(_make_ohlcv(10))
        assert set(["open", "high", "low", "close", "volume", "returns", "log_close"]).issubset(df.columns)

    def test_index_is_utc_datetime(self):
        df = _ohlcv_to_df(_make_ohlcv(10))
        assert hasattr(df.index, "tz")
        assert str(df.index.tz) == "UTC"

    def test_log_close_positive(self):
        df = _ohlcv_to_df(_make_ohlcv(20))
        assert (df["log_close"] > 0).all()

    def test_sorted_ascending(self):
        raw = _make_ohlcv(20)
        raw_shuffled = raw[::-1]
        df = _ohlcv_to_df(raw_shuffled)
        assert df.index.is_monotonic_increasing


# --- _is_stale ----------------------------------------------------------------

class TestIsStale:
    def test_fresh_file_not_stale(self, tmp_path):
        p = tmp_path / "test.parquet"
        p.write_text("x")
        assert not _is_stale(p, "1h", stale_multiplier=2.0)

    def test_old_file_is_stale(self, tmp_path, monkeypatch):
        p = tmp_path / "test.parquet"
        p.write_text("x")
        monkeypatch.setattr(time, "time", lambda: p.stat().st_mtime + 7_201)
        assert _is_stale(p, "1h", stale_multiplier=2.0)


# --- DataLoader ---------------------------------------------------------------

class TestDataLoader:
    @pytest.mark.asyncio
    async def test_fetch_calls_exchange_and_caches(self, tmp_path):
        import data.loader
        data.loader.CACHE_DIR = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv = AsyncMock(return_value=_make_ohlcv(100))
        mock_exchange.close = AsyncMock()
        mock_cls = MagicMock(return_value=mock_exchange)
        mock_ccxt = MagicMock()
        mock_ccxt.binance = mock_cls

        with patch("data.loader.ccxt_async", mock_ccxt):
            loader = DataLoader(exchange_id="binance", timeframe="1h", use_cache=True)
            df = await loader.fetch("BTC/USDT", limit=100)

        assert len(df) == 100
        assert "close" in df.columns
        cache = tmp_path / "binance_BTC_USDT_1h.parquet"
        assert cache.exists()

    @pytest.mark.asyncio
    async def test_fetch_uses_cache_on_second_call(self, tmp_path):
        import data.loader
        data.loader.CACHE_DIR = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)

        df_cache = _ohlcv_to_df(_make_ohlcv(80))
        cache_path = tmp_path / "binance_BTC_USDT_1h.parquet"
        df_cache.to_parquet(cache_path)

        call_count = 0

        async def fake_fetch(*a, **kw):
            nonlocal call_count
            call_count += 1
            return _make_ohlcv(80)

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv = fake_fetch
        mock_exchange.close = AsyncMock()
        mock_cls = MagicMock(return_value=mock_exchange)
        mock_ccxt = MagicMock()
        mock_ccxt.binance = mock_cls

        with patch("data.loader.ccxt_async", mock_ccxt):
            loader = DataLoader(exchange_id="binance", timeframe="1h", use_cache=True, stale_multiplier=1e9)
            await loader.fetch("BTC/USDT", limit=80)

        assert call_count == 0, "Should serve from cache"

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self, tmp_path):
        import data.loader
        data.loader.CACHE_DIR = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)

        call_count = 0

        async def flaky_fetch(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary network failure")
            return _make_ohlcv(50)

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv = flaky_fetch
        mock_exchange.close = AsyncMock()
        mock_cls = MagicMock(return_value=mock_exchange)
        mock_ccxt = MagicMock()
        mock_ccxt.binance = mock_cls

        with patch("data.loader.ccxt_async", mock_ccxt):
            loader = DataLoader(max_retries=3, retry_delay=0.0, use_cache=False)
            df = await loader.fetch("ETH/USDT", limit=50)

        assert call_count == 3
        assert len(df) == 50

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, tmp_path):
        import data.loader
        data.loader.CACHE_DIR = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv = AsyncMock(side_effect=ConnectionError("down"))
        mock_exchange.close = AsyncMock()
        mock_cls = MagicMock(return_value=mock_exchange)
        mock_ccxt = MagicMock()
        mock_ccxt.binance = mock_cls

        with patch("data.loader.ccxt_async", mock_ccxt):
            loader = DataLoader(max_retries=2, retry_delay=0.0, use_cache=False)
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                await loader.fetch("SOL/USDT")

    @pytest.mark.asyncio
    async def test_fetch_multiple_aligned(self, tmp_path):
        import data.loader
        data.loader.CACHE_DIR = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)

        async def mock_fetch(symbol, limit=1000):
            return _ohlcv_to_df(_make_ohlcv(100, seed=hash(symbol) % 100))

        loader = DataLoader(use_cache=False)
        with patch.object(loader, "fetch", side_effect=mock_fetch):
            prices, raw = await loader.fetch_multiple(["BTC/USDT", "ETH/USDT"], limit=100)

        assert "BTC/USDT" in prices.columns
        assert "ETH/USDT" in prices.columns
        assert not prices.isnull().any().any()

    def test_load_from_parquet_returns_none_if_missing(self, tmp_path):
        import data.loader
        data.loader.CACHE_DIR = tmp_path
        loader = DataLoader()
        result = loader.load_from_parquet("NONEXISTENT/USDT")
        assert result is None

    def test_load_from_parquet_reads_existing(self, tmp_path):
        import data.loader
        data.loader.CACHE_DIR = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        df = _ohlcv_to_df(_make_ohlcv(30))
        path = tmp_path / "binance_BTC_USDT_1h.parquet"
        df.to_parquet(path)
        loader = DataLoader()
        result = loader.load_from_parquet("BTC/USDT")
        assert result is not None
        assert len(result) == 30


# --- FundingRateFetcher -------------------------------------------------------

class TestFundingRateFetcher:
    def test_annualized_funding_cost_math(self):
        rate = 0.0001
        annual = annualized_funding_cost(rate, leverage=1.0)
        expected = rate * (365 * 24 / 8)
        assert abs(annual - expected) < 1e-10

    def test_annualized_scales_with_leverage(self):
        r = 0.0001
        assert abs(annualized_funding_cost(r, 2.0) - 2 * annualized_funding_cost(r, 1.0)) < 1e-12

    @pytest.mark.asyncio
    async def test_fetch_current_returns_float(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_funding_rate = AsyncMock(return_value={"fundingRate": 0.0001})
        mock_exchange.close = AsyncMock()
        mock_ccxt = MagicMock()
        mock_ccxt.binance = MagicMock(return_value=mock_exchange)

        with patch("data.funding.ccxt_async", mock_ccxt):
            fetcher = FundingRateFetcher("binance")
            rate = await fetcher.fetch_current("BTC/USDT:USDT")

        assert isinstance(rate, float)
        assert abs(rate - 0.0001) < 1e-10

    @pytest.mark.asyncio
    async def test_fetch_current_returns_zero_on_error(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_funding_rate = AsyncMock(side_effect=Exception("API down"))
        mock_exchange.close = AsyncMock()
        mock_ccxt = MagicMock()
        mock_ccxt.binance = MagicMock(return_value=mock_exchange)

        with patch("data.funding.ccxt_async", mock_ccxt):
            fetcher = FundingRateFetcher("binance")
            rate = await fetcher.fetch_current("BTC/USDT:USDT")

        assert rate == 0.0

    @pytest.mark.asyncio
    async def test_fetch_history_returns_dataframe(self, tmp_path):
        import data.funding
        data.funding.CACHE_DIR = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)

        mock_exchange = AsyncMock()
        mock_exchange.fetch_funding_rate_history = AsyncMock(return_value=_make_funding_history(30))
        mock_exchange.close = AsyncMock()
        mock_ccxt = MagicMock()
        mock_ccxt.binance = MagicMock(return_value=mock_exchange)

        with patch("data.funding.ccxt_async", mock_ccxt):
            fetcher = FundingRateFetcher("binance")
            df = await fetcher.fetch_history("BTC/USDT:USDT", use_cache=False)

        assert "fundingRate" in df.columns
        assert "annualized_cost" in df.columns
        assert len(df) == 30

    def test_compute_drag_scales_correctly(self):
        rates = pd.Series([0.0001] * 10)
        fetcher = FundingRateFetcher()
        drag = fetcher.compute_drag(rates, leverage=2.0, freq_hours=1.0)
        expected = 0.0001 * 2.0 * (1.0 / 8.0)
        assert abs(drag.iloc[0] - expected) < 1e-12

    def test_should_reduce_size_true(self):
        rates = pd.Series([0.0001] * 5)
        fetcher = FundingRateFetcher()
        assert fetcher.should_reduce_size(rates, threshold_annual=0.05)

    def test_should_reduce_size_false_low_rate(self):
        rates = pd.Series([0.000001] * 5)
        fetcher = FundingRateFetcher()
        assert not fetcher.should_reduce_size(rates, threshold_annual=0.05)

    def test_should_reduce_size_empty_series(self):
        fetcher = FundingRateFetcher()
        assert not fetcher.should_reduce_size(pd.Series([], dtype=float))

"""
tests/test_rate_limiter.py  —  RateLimiter async unit tests
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from execution.rate_limiter import RateLimiter, RateLimiterConfig, _TokenBucket


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_first_acquire_no_wait(self):
        bucket = _TokenBucket(rate=100.0, burst=100.0)
        wait = await bucket.acquire()
        assert wait == 0.0

    @pytest.mark.asyncio
    async def test_acquire_exhausts_tokens(self):
        bucket = _TokenBucket(rate=1.0, burst=2.0)
        await bucket.acquire()  # token 1
        await bucket.acquire()  # token 2
        # Third acquire should wait ~1s — we just verify it doesn't crash
        start = time.monotonic()
        await asyncio.wait_for(bucket.acquire(), timeout=3.0)
        elapsed = time.monotonic() - start
        assert elapsed > 0.5  # should have waited


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_bybit_limits_configured(self):
        limiter = RateLimiter(RateLimiterConfig(exchange="bybit"))
        assert "order" in limiter._buckets
        assert "market" in limiter._buckets
        assert "query" in limiter._buckets

    @pytest.mark.asyncio
    async def test_acquire_order_no_crash(self):
        limiter = RateLimiter(RateLimiterConfig(exchange="bybit"))
        await limiter.acquire_order()  # should complete immediately (burst)

    @pytest.mark.asyncio
    async def test_acquire_market_no_crash(self):
        limiter = RateLimiter(RateLimiterConfig(exchange="bybit"))
        await limiter.acquire_market()

    @pytest.mark.asyncio
    async def test_unknown_endpoint_falls_back_to_order(self):
        limiter = RateLimiter(RateLimiterConfig(exchange="bybit"))
        await limiter.acquire("nonexistent_endpoint")  # must not raise

    @pytest.mark.asyncio
    async def test_binance_limits_different_from_bybit(self):
        bybit = RateLimiter(RateLimiterConfig(exchange="bybit"))
        binance = RateLimiter(RateLimiterConfig(exchange="binance"))
        # market rps differs: bybit=50, binance=20
        assert bybit._buckets["market"]._rate != binance._buckets["market"]._rate

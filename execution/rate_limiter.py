"""
execution/rate_limiter.py  —  QuantLuna API Rate Limiter

Sprint 12 — Token bucket rate limiter async pentru CCXT API calls:
  - Previne HTTP 429 (Too Many Requests) pe Bybit / Binance
  - Token bucket algorithm: burst + sustained rate
  - Per-endpoint limiting (order, query, market data)
  - Async-first — await acquire() înainte de orice API call
  - Bybit limits: 10 req/s orders, 50 req/s market data
  - Binance limits: 10 req/s orders, 20 req/s market data

Usage:
    from execution.rate_limiter import RateLimiter, RateLimiterConfig

    limiter = RateLimiter(RateLimiterConfig(exchange="bybit"))

    # Înainte de orice order call:
    await limiter.acquire("order")
    await exchange.create_order(...)

    # Înainte de market data:
    await limiter.acquire("market")
    await exchange.fetch_ohlcv(...)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger(__name__)

# Default limits per exchange (requests/second)
_LIMITS: Dict[str, Dict[str, float]] = {
    "bybit":   {"order": 10.0, "query": 20.0, "market": 50.0},
    "binance": {"order": 10.0, "query": 20.0, "market": 20.0},
}
_DEFAULT_LIMITS = {"order": 5.0, "query": 10.0, "market": 20.0}


@dataclass
class RateLimiterConfig:
    exchange: str = "bybit"
    # Override limits (req/s). None = use defaults per exchange.
    order_rps:  float = 0.0
    query_rps:  float = 0.0
    market_rps: float = 0.0
    # Burst multiplier — max tokens in bucket = rate * burst_factor
    burst_factor: float = 2.0
    # Warning log jei wait > this threshold
    warn_wait_s: float = 0.5


class _TokenBucket:
    """Async token bucket rate limiter."""

    def __init__(self, rate: float, burst: float) -> None:
        self._rate = rate       # tokens per second
        self._burst = burst     # max tokens
        self._tokens = burst
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait until a token is available. Returns wait time in seconds."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0

            wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)
            self._tokens = 0.0
            self._last_refill = time.monotonic()
            return wait


class RateLimiter:
    """
    Multi-endpoint async rate limiter.

    Fiecare endpoint type (order, query, market) are propriul bucket.
    """

    def __init__(self, config: RateLimiterConfig) -> None:
        self.cfg = config
        defaults = _LIMITS.get(config.exchange.lower(), _DEFAULT_LIMITS)

        order_rps  = config.order_rps  or defaults.get("order", 5.0)
        query_rps  = config.query_rps  or defaults.get("query", 10.0)
        market_rps = config.market_rps or defaults.get("market", 20.0)

        self._buckets: Dict[str, _TokenBucket] = {
            "order":  _TokenBucket(order_rps,  order_rps  * config.burst_factor),
            "query":  _TokenBucket(query_rps,  query_rps  * config.burst_factor),
            "market": _TokenBucket(market_rps, market_rps * config.burst_factor),
        }
        logger.info(
            f"RateLimiter({config.exchange}): order={order_rps}rps "
            f"query={query_rps}rps market={market_rps}rps "
            f"burst={config.burst_factor}x"
        )

    async def acquire(self, endpoint: str = "order") -> None:
        """
        Async acquire pentru endpoint specificat.
        Blochează dacă rate limit-ul este atins.

        Args:
            endpoint: 'order' | 'query' | 'market'
        """
        bucket = self._buckets.get(endpoint, self._buckets["order"])
        wait = await bucket.acquire()
        if wait > self.cfg.warn_wait_s:
            logger.warning(
                f"RateLimiter: waited {wait:.3f}s for {endpoint} endpoint "
                f"({self.cfg.exchange}) — consider reducing request frequency"
            )

    async def acquire_order(self) -> None:
        """Shortcut pentru order endpoint."""
        await self.acquire("order")

    async def acquire_market(self) -> None:
        """Shortcut pentru market data endpoint."""
        await self.acquire("market")

    async def acquire_query(self) -> None:
        """Shortcut pentru account query endpoint."""
        await self.acquire("query")

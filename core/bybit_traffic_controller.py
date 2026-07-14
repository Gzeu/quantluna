"""
core/bybit_traffic_controller.py — Centralized Bybit traffic management (P0 S48).

Single entry point for ALL Bybit REST calls. Provides:
  - Rate limiting (configurable requests/minute)
  - Circuit breaker (429, 10006, network/auth errors)
  - Request deduplication (singleflight)
  - TTL cache for slow-changing data
  - Exponential backoff with jitter
  - Structured logging with correlation IDs
  - Observable state for diagnostics

ALL Bybit REST calls MUST go through this module. Direct exchange calls
bypassing this controller are a P0 violation.

Usage::

    ctrl = BybitTrafficController()

    # Rate-limited, cached, auto-retried call:
    data = await ctrl.request(
        group=EndpointGroup.ACCOUNT_SYNC,
        endpoint="/v5/account/wallet-balance",
        params={"accountType": "UNIFIED"},
        ttl=30.0,  # cache for 30s
    )
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoint Groups (priority-ordered)
# ═══════════════════════════════════════════════════════════════════════════════


class EndpointGroup(str, Enum):
    CRITICAL_POSITION_PROTECTION = "critical_position_protection"  # TP/SL, close
    ACCOUNT_SYNC = "account_sync"                  # wallet, positions, orders
    EXECUTION = "execution"                         # place/cancel orders
    MARKET_DATA = "market_data"                     # OHLCV, ticker, orderbook
    UI_DIAGNOSTICS = "ui_diagnostics"              # dashboard queries


# Priority → max concurrent requests (lower index = higher priority)
GROUP_PRIORITY = {
    EndpointGroup.CRITICAL_POSITION_PROTECTION: 0,
    EndpointGroup.ACCOUNT_SYNC: 1,
    EndpointGroup.EXECUTION: 2,
    EndpointGroup.MARKET_DATA: 3,
    EndpointGroup.UI_DIAGNOSTICS: 4,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Connection state
# ═══════════════════════════════════════════════════════════════════════════════


class ConnectionState(str, Enum):
    CONNECTING = "CONNECTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    THROTTLED = "THROTTLED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    BLOCKED = "BLOCKED"


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TrafficConfig:
    max_rest_rpm: int = int(os.getenv("BYBIT_MAX_REST_RPM", "120"))
    max_concurrent: int = int(os.getenv("BYBIT_MAX_CONCURRENT_REQUESTS", "10"))
    account_sync_interval: float = float(os.getenv("BYBIT_ACCOUNT_SYNC_SECONDS", "30"))
    ui_cache_ttl: float = float(os.getenv("BYBIT_UI_CACHE_TTL_SECONDS", "10"))
    ws_reconnect_min: float = float(os.getenv("BYBIT_WS_RECONNECT_MIN_SECONDS", "1.0"))
    ws_reconnect_max: float = float(os.getenv("BYBIT_WS_RECONNECT_MAX_SECONDS", "30.0"))
    ws_max_reconnects: int = int(os.getenv("BYBIT_WS_MAX_RECONNECTS", "20"))
    circuit_breaker_enabled: bool = os.getenv("BYBIT_CIRCUIT_BREAKER_ENABLED", "true").lower() == "true"
    single_instance_enabled: bool = os.getenv("SINGLE_INSTANCE_ENABLED", "true").lower() == "true"
    entries_enabled: bool = os.getenv("ENTRIES_ENABLED", "false").lower() == "true"
    sync_only: bool = os.getenv("SYNC_ONLY", "true").lower() == "true"

    # Circuit breaker thresholds
    cb_429_threshold: int = 5          # open after N 429s in window
    cb_error_threshold: int = 10       # open after N errors in window
    cb_window_seconds: float = 60.0    # sliding window
    cb_recovery_seconds: float = 30.0  # time before half-open attempt


# ═══════════════════════════════════════════════════════════════════════════════
# Traffic stats
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TrafficStats:
    requests_total: Dict[str, int] = field(default_factory=lambda: {
        g.value: 0 for g in EndpointGroup
    })
    requests_429: int = 0
    requests_10006: int = 0
    requests_timeout: int = 0
    requests_auth_error: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    circuit_open_count: int = 0
    ws_reconnects: int = 0
    ws_state: str = ConnectionState.CONNECTING.value
    rest_state: str = ConnectionState.CONNECTING.value

    @property
    def rpm(self) -> float:
        total = sum(self.requests_total.values())
        return total / max(self._elapsed_minutes(), 1) * 1.0

    @property
    def cache_hit_ratio(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    def _elapsed_minutes(self) -> float:
        return 1.0

    def as_dict(self) -> dict:
        return {
            "requests_total": dict(self.requests_total),
            "requests_429": self.requests_429,
            "requests_10006": self.requests_10006,
            "requests_timeout": self.requests_timeout,
            "requests_auth_error": self.requests_auth_error,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_ratio": round(self.cache_hit_ratio, 3),
            "circuit_open_count": self.circuit_open_count,
            "ws_reconnects": self.ws_reconnects,
            "ws_state": self.ws_state,
            "rest_state": self.rest_state,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Rate Limiter (token bucket)
# ═══════════════════════════════════════════════════════════════════════════════


class TokenBucket:
    """Token bucket rate limiter — thread-safe for asyncio."""

    def __init__(self, rate: float, burst: int = 5) -> None:
        self._rate = rate          # tokens per second
        self._burst = burst        # max tokens
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    async def wait_and_acquire(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.acquire():
                return True
            await asyncio.sleep(0.1)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════════


class CircuitBreaker:
    """Circuit breaker with half-open probing."""

    def __init__(self, cfg: TrafficConfig) -> None:
        self._cfg = cfg
        self._open = False
        self._half_open = False
        self._error_times: List[float] = []  # timestamps
        self._opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def state(self) -> str:
        if self._open:
            return "OPEN"
        if self._half_open:
            return "HALF_OPEN"
        return "CLOSED"

    def record_error(self, status_code: int = 0) -> None:
        now = time.monotonic()
        self._error_times.append(now)
        # Prune old errors
        cutoff = now - self._cfg.cb_window_seconds
        self._error_times = [t for t in self._error_times if t > cutoff]

        errors = len(self._error_times)
        if not self._open and not self._half_open:
            if status_code == 429 and errors >= self._cfg.cb_429_threshold:
                self._open = True
                self._opened_at = now
                logger.warning("CircuitBreaker: OPEN (429 threshold: {} in {}s)", errors, self._cfg.cb_window_seconds)
            elif errors >= self._cfg.cb_error_threshold:
                self._open = True
                self._opened_at = now
                logger.warning("CircuitBreaker: OPEN (error threshold: {} in {}s)", errors, self._cfg.cb_window_seconds)

    def try_half_open(self) -> bool:
        """Return True if we should attempt a probe request."""
        if not self._open:
            return True
        if self._half_open:
            return False  # already probing
        if time.monotonic() - self._opened_at >= self._cfg.cb_recovery_seconds:
            self._half_open = True
            logger.info("CircuitBreaker: HALF_OPEN — probing")
            return True
        return False

    def on_success(self) -> None:
        if self._half_open or self._open:
            logger.info("CircuitBreaker: CLOSED — probe succeeded")
        self._open = False
        self._half_open = False
        self._error_times.clear()

    def force_open(self, reason: str = "") -> None:
        self._open = True
        self._half_open = False
        logger.error("CircuitBreaker: FORCED OPEN — {}", reason)


# ═══════════════════════════════════════════════════════════════════════════════
# TTL Cache
# ═══════════════════════════════════════════════════════════════════════════════


class TTLCache:
    """Simple TTL cache for API responses."""

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires, value = entry
            if time.monotonic() > expires:
                del self._cache[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl: float) -> None:
        async with self._lock:
            self._cache[key] = (time.monotonic() + ttl, value)

    async def invalidate(self, prefix: str = "") -> None:
        async with self._lock:
            if prefix:
                self._cache = {k: v for k, v in self._cache.items() if not k.startswith(prefix)}
            else:
                self._cache.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# Request Coordinator
# ═══════════════════════════════════════════════════════════════════════════════


class BybitTrafficController:
    """
    SINGLE entry point for ALL Bybit REST calls.

    Usage:
        ctrl = BybitTrafficController()
        data = await ctrl.request(
            group=EndpointGroup.ACCOUNT_SYNC,
            endpoint="/v5/account/wallet-balance",
            executor=my_async_callable,  # the actual HTTP call
            ttl=30.0,
        )
    """

    def __init__(self, cfg: Optional[TrafficConfig] = None) -> None:
        self._cfg = cfg or TrafficConfig()
        self._bucket = TokenBucket(self._cfg.max_rest_rpm / 60.0, burst=10)
        self._circuit_breaker = CircuitBreaker(self._cfg)
        self._cache = TTLCache()
        self._stats = TrafficStats()
        self._semaphore = asyncio.Semaphore(self._cfg.max_concurrent)
        self._inflight: Dict[str, asyncio.Event] = {}  # singleflight dedup
        self._inflight_results: Dict[str, Any] = {}
        self._non_critical_paused = False
        self._started_at = time.monotonic()

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def stats(self) -> TrafficStats:
        return self._stats

    @property
    def circuit_open(self) -> bool:
        return self._circuit_breaker.is_open

    @property
    def non_critical_paused(self) -> bool:
        return self._non_critical_paused

    async def request(
        self,
        group: EndpointGroup,
        endpoint: str,
        executor: Callable = None,
        params: dict = None,
        ttl: float = 0.0,
        priority: int = 0,
        cache_key: str = "",
    ) -> Any:
        """
        Execute a rate-limited, circuit-breaker-protected, cached Bybit REST call.

        Parameters
        ----------
        group : EndpointGroup
            Traffic classification group.
        endpoint : str
            Bybit REST endpoint path (for logging).
        executor : callable
            Async callable that does the actual HTTP request.
        params : dict
            Parameters for the request (used for cache key if not provided).
        ttl : float
            Cache TTL in seconds. 0 = no caching.
        cache_key : str
            Explicit cache key. Auto-generated from endpoint+params if empty.

        Returns
        -------
        The result from executor(), or None if blocked/rejected.
        """
        corr_id = str(uuid.uuid4())[:8]

        # 0. Non-critical pause
        if self._non_critical_paused and group in (
            EndpointGroup.MARKET_DATA, EndpointGroup.UI_DIAGNOSTICS,
        ):
            logger.debug("[{}] BLOCKED: non-critical paused", corr_id)
            return None

        # 1. Cache check
        ck = cache_key or self._cache_key(endpoint, params)
        if ttl > 0 and ck:
            cached = await self._cache.get(ck)
            if cached is not None:
                self._stats.cache_hits += 1
                logger.debug("[{}] CACHE HIT {} (group={})", corr_id, endpoint, group.value)
                return cached
            self._stats.cache_misses += 1

        # 2. Circuit breaker
        if self._cfg.circuit_breaker_enabled and self._circuit_breaker.is_open:
            if not self._circuit_breaker.try_half_open():
                logger.warning("[{}] CIRCUIT OPEN — blocking {} ({})", corr_id, endpoint, group.value)
                return None

        # 3. Singleflight dedup
        if ck and ck in self._inflight:
            logger.debug("[{}] SINGLEFLIGHT wait: {}", corr_id, endpoint)
            await self._inflight[ck].wait()
            return self._inflight_results.get(ck)

        if ck:
            self._inflight[ck] = asyncio.Event()

        # 4. Rate limit
        if not await self._bucket.wait_and_acquire(timeout=5.0):
            logger.warning("[{}] RATE LIMITED: {} ({})", corr_id, endpoint, group.value)
            self._cleanup_inflight(ck)
            return None

        # 5. Concurrency limit
        async with self._semaphore:
            self._stats.requests_total[group.value] += 1

            try:
                result = await self._execute_with_retry(executor, endpoint, group, corr_id)
            except Exception:
                result = None
            finally:
                self._cleanup_inflight(ck)

        # 6. Cache result
        if result is not None and ttl > 0 and ck:
            await self._cache.set(ck, result, ttl)

        if ck:
            self._inflight_results[ck] = result

        return result

    async def pause_non_critical(self) -> None:
        self._non_critical_paused = True
        logger.info("TrafficController: non-critical traffic PAUSED")

    async def resume_non_critical(self) -> None:
        self._non_critical_paused = False
        logger.info("TrafficController: non-critical traffic RESUMED")

    async def invalidate_cache(self, prefix: str = "") -> None:
        await self._cache.invalidate(prefix)

    def snapshot(self) -> dict:
        return {
            "config": {
                "max_rest_rpm": self._cfg.max_rest_rpm,
                "max_concurrent": self._cfg.max_concurrent,
                "circuit_breaker_enabled": self._cfg.circuit_breaker_enabled,
                "entries_enabled": self._cfg.entries_enabled,
                "sync_only": self._cfg.sync_only,
            },
            "stats": self._stats.as_dict(),
            "circuit_breaker": self._circuit_breaker.state,
            "non_critical_paused": self._non_critical_paused,
            "uptime_seconds": round(time.monotonic() - self._started_at, 1),
        }

    # ── Internal ────────────────────────────────────────────────────────────

    async def _execute_with_retry(
        self, executor: Callable, endpoint: str, group: EndpointGroup, corr_id: str,
    ) -> Any:
        """Execute with exponential backoff + jitter."""
        if executor is None:
            return None

        max_retries = 3 if group == EndpointGroup.CRITICAL_POSITION_PROTECTION else 1
        base_delay = 1.0

        for attempt in range(max_retries + 1):
            t0 = time.monotonic()
            try:
                result = await executor()
                elapsed = (time.monotonic() - t0) * 1000
                logger.debug(
                    "[{}] OK {} {} attempt={} latency={:.0f}ms",
                    corr_id, endpoint, group.value, attempt + 1, elapsed,
                )
                self._circuit_breaker.on_success()
                return result
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                status = getattr(exc, "status_code", getattr(exc, "code", 0))
                if status == 429:
                    self._stats.requests_429 += 1
                    self._circuit_breaker.record_error(429)
                elif status == 10006:
                    self._stats.requests_10006 += 1
                    self._circuit_breaker.record_error(10006)
                else:
                    self._circuit_breaker.record_error()

                if attempt >= max_retries:
                    logger.error(
                        "[{}] FAIL {} {} attempt={}/{}, status={} latency={:.0f}ms: {}",
                        corr_id, endpoint, group.value, attempt + 1, max_retries + 1,
                        status, elapsed, exc,
                    )
                    return None

                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "[{}] RETRY {} {} attempt={} delay={:.1f}s status={}: {}",
                    corr_id, endpoint, group.value, attempt + 1, delay, status, exc,
                )
                await asyncio.sleep(delay)

        return None

    def _cache_key(self, endpoint: str, params: dict = None) -> str:
        raw = endpoint + json.dumps(params or {}, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _cleanup_inflight(self, ck: str) -> None:
        if ck and ck in self._inflight:
            self._inflight[ck].set()
            self._inflight.pop(ck, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton instance
# ═══════════════════════════════════════════════════════════════════════════════

_traffic_controller: Optional[BybitTrafficController] = None


def get_traffic_controller() -> BybitTrafficController:
    """Return the global singleton traffic controller."""
    global _traffic_controller
    if _traffic_controller is None:
        _traffic_controller = BybitTrafficController()
    return _traffic_controller


def set_traffic_controller(ctrl: BybitTrafficController) -> None:
    global _traffic_controller
    _traffic_controller = ctrl

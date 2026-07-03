"""
execution/order_throttle.py — per-symbol order throttle with burst protection.

Prevents accidental over-trading caused by signal noise or bugs.
Uses a token-bucket algorithm: N orders per window, with an optional
global burst cap across all symbols.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict


@dataclass
class ThrottleConfig:
    max_orders_per_window: int = 5
    window_seconds: float = 60.0
    global_burst_cap: int = 20


class OrderThrottle:
    """
    Token-bucket throttle for order submission.

    Usage::

        throttle = OrderThrottle(ThrottleConfig(max_orders_per_window=5))

        if throttle.allow("BTCUSDT"):
            router.place_order(...)
        else:
            logger.warning("Order throttled for BTCUSDT")
    """

    def __init__(self, config: ThrottleConfig | None = None) -> None:
        self._cfg = config or ThrottleConfig()
        self._lock = Lock()
        self._buckets: Dict[str, list[float]] = {}
        self._global_times: list[float] = []

    def allow(self, symbol: str) -> bool:
        with self._lock:
            now = time.monotonic()
            window = self._cfg.window_seconds

            # Prune global bucket
            self._global_times = [t for t in self._global_times if now - t <= window]
            if len(self._global_times) >= self._cfg.global_burst_cap:
                return False

            # Prune per-symbol bucket
            bucket = self._buckets.setdefault(symbol, [])
            self._buckets[symbol] = [t for t in bucket if now - t <= window]

            if len(self._buckets[symbol]) >= self._cfg.max_orders_per_window:
                return False

            # Allow and record
            self._buckets[symbol].append(now)
            self._global_times.append(now)
            return True

    def remaining(self, symbol: str) -> int:
        """Tokens remaining for symbol in current window."""
        with self._lock:
            now = time.monotonic()
            window = self._cfg.window_seconds
            bucket = self._buckets.get(symbol, [])
            active = sum(1 for t in bucket if now - t <= window)
            return max(0, self._cfg.max_orders_per_window - active)

    def reset(self, symbol: str | None = None) -> None:
        """Reset throttle for a symbol or all symbols."""
        with self._lock:
            if symbol:
                self._buckets.pop(symbol, None)
            else:
                self._buckets.clear()
                self._global_times.clear()

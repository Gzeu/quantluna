"""
risk/circuit_breaker.py — Risk-layer circuit breaker (P&L / drawdown aware).

This module contains TWO circuit breaker implementations with distinct roles:

  RiskCircuitBreaker  (defined here)
    - Sync, P&L-aware breaker used by the risk layer.
    - Monitors: consecutive losses, drawdown %, execution error count.
    - State machine: CLOSED → OPEN → HALF_OPEN (time-based cooldown).
    - Use this when reacting to trading outcomes (wins/losses/errors).

  CircuitBreaker  (from execution.circuit_breaker — re-exported here)
    - Async context-manager, used for WebSocket / CCXT connection protection.
    - Monitors: consecutive connection/API failures.
    - Use this when wrapping async I/O calls.

Quick-reference:
    from risk.circuit_breaker import RiskCircuitBreaker   # P&L breaker
    from risk.circuit_breaker import CircuitBreaker        # WS/async breaker (re-export)
    from execution.circuit_breaker import CircuitBreaker   # same, direct import
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from execution.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState  # noqa: F401


class BreakerState(Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


@dataclass
class BreakerEvent:
    reason: str
    ts: float = field(default_factory=time.time)


class RiskCircuitBreaker:
    """
    Sync circuit breaker for the risk layer.

    Monitors P&L outcomes and drawdown to halt new order placement
    during adverse streaks. Operates independently of the async
    execution.CircuitBreaker (which guards WS/API connectivity).

    Usage::

        cb = RiskCircuitBreaker(max_consecutive_losses=3, cooldown_seconds=300)
        cb.record_loss(50.0)
        if not cb.allow():
            logger.warning("RiskCircuitBreaker OPEN: %s", cb.last_reason)
    """

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        max_drawdown_pct: float = 0.05,
        max_errors: int = 5,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self._max_losses = max_consecutive_losses
        self._max_dd = max_drawdown_pct
        self._max_errors = max_errors
        self._cooldown = cooldown_seconds

        self._state = BreakerState.CLOSED
        self._consecutive_losses = 0
        self._error_count = 0
        self._open_at: Optional[float] = None
        self._events: List[BreakerEvent] = []
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0
        self.last_reason: str = ""

    @property
    def state(self) -> BreakerState:
        self._maybe_transition()
        return self._state

    def allow(self) -> bool:
        return self.state != BreakerState.OPEN

    def record_win(self, pnl: float) -> None:
        self._consecutive_losses = 0
        self._current_equity += pnl
        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity

    def record_loss(self, pnl: float) -> None:
        """pnl can be positive or negative — treated as an absolute loss."""
        loss = abs(pnl)
        self._current_equity -= loss
        self._consecutive_losses += 1

        if self._consecutive_losses >= self._max_losses:
            self._trip(f"{self._consecutive_losses} consecutive losses")
            return

        if self._peak_equity > 0:
            dd = (self._peak_equity - self._current_equity) / self._peak_equity
            if dd >= self._max_dd:
                self._trip(f"drawdown {dd*100:.1f}% >= {self._max_dd*100:.1f}%")

    def record_error(self) -> None:
        self._error_count += 1
        if self._error_count >= self._max_errors:
            self._trip(f"{self._error_count} execution errors")

    def reset(self) -> None:
        self._state = BreakerState.CLOSED
        self._consecutive_losses = 0
        self._error_count = 0
        self._open_at = None
        self.last_reason = ""

    def set_equity(self, equity: float) -> None:
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    def _trip(self, reason: str) -> None:
        if self._state == BreakerState.OPEN:
            return
        self._state = BreakerState.OPEN
        self._open_at = time.time()
        self.last_reason = reason
        self._events.append(BreakerEvent(reason=reason))

    def _maybe_transition(self) -> None:
        if self._state == BreakerState.OPEN and self._open_at is not None:
            elapsed = time.time() - self._open_at
            if elapsed >= self._cooldown:
                self._state = BreakerState.HALF_OPEN

    @property
    def events(self) -> List[BreakerEvent]:
        return list(self._events)

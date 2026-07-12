"""
risk/circuit_breaker.py — Risk-layer circuit breaker (P&L / drawdown aware).

This module contains TWO circuit breaker implementations with distinct roles:

  RiskCircuitBreaker  (defined here)
    - Sync, P&L-aware breaker used by the risk layer.
    - Monitors: consecutive losses, drawdown %, execution error count.
    - State machine: CLOSED -> OPEN -> HALF_OPEN (time-based cooldown).
    - Use this when reacting to trading outcomes (wins/losses/errors).

  CircuitBreaker  (from execution.circuit_breaker — re-exported here)
    - Async context-manager, used for WebSocket / CCXT connection protection.
    - Monitors: consecutive connection/API failures.
    - Use this when wrapping async I/O calls.

Quick-reference:
    from risk.circuit_breaker import RiskCircuitBreaker   # P&L breaker
    from risk.circuit_breaker import CircuitBreaker        # WS/async breaker (re-export)
    from execution.circuit_breaker import CircuitBreaker   # same, direct import

Changes (code review 2026-07-12):
  - Patch 1: removed early `return` in record_loss() so the drawdown
    check always executes even when consecutive-loss threshold is hit.
  - Patch 2: record_win() now transitions HALF_OPEN -> CLOSED on a
    confirmed win, preventing the breaker from sticking in HALF_OPEN.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

# Re-export the canonical async CircuitBreaker so callers that previously
# imported it from risk.circuit_breaker keep working without changes.
from execution.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState  # noqa: F401


class BreakerState(Enum):
    CLOSED    = "closed"       # normal, trading allowed
    OPEN      = "open"         # blocked
    HALF_OPEN = "half_open"    # test after cooldown


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
        """
        Record a winning trade.

        Patch 2: transitions HALF_OPEN -> CLOSED on a confirmed win so
        the breaker does not stay stuck in HALF_OPEN indefinitely.
        """
        self._consecutive_losses = 0
        self._current_equity += pnl
        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity
        # Transition HALF_OPEN -> CLOSED on confirmed win
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self.last_reason = ""

    def record_loss(self, pnl: float) -> None:
        """
        Record a losing trade. pnl can be positive or negative — treated as
        an absolute loss.

        Patch 1: removed early `return` after the consecutive-loss trip so
        that the drawdown check always executes regardless of which threshold
        was hit first. Previously, hitting max_consecutive_losses would skip
        the drawdown check entirely.
        """
        loss = abs(pnl)
        self._current_equity -= loss
        self._consecutive_losses += 1

        if self._consecutive_losses >= self._max_losses:
            self._trip(f"{self._consecutive_losses} consecutive losses")
            # NOTE: no early return — fall through to drawdown check too

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


# ---------------------------------------------------------------------------
# Backward-compatibility alias: code that imported CircuitBreaker from here
# previously got the sync P&L breaker. New code should use RiskCircuitBreaker
# explicitly. This alias will be removed in a future release.
# ---------------------------------------------------------------------------
# NOTE: CircuitBreaker is now the ASYNC version re-exported from execution/.
# If you need the sync P&L breaker, use RiskCircuitBreaker.

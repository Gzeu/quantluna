"""
QuantLuna — Circuit Breaker (Sprint 17)

Automatically halts trading after configurable loss/drawdown/error thresholds.
When tripped, no new entries are allowed until the cooldown expires or
manual reset is performed.

Trip conditions (configurable, any single trigger is sufficient):
  1. Consecutive losing trades ≥ max_consecutive_losses
  2. Realised PnL drop ≥ max_drawdown_pct within a rolling window
  3. Order error rate ≥ max_error_rate within error_window_trades
  4. Manual trip (e.g. operator kill-switch)

Usage:
    cb = CircuitBreaker(CircuitBreakerConfig(max_consecutive_losses=5))
    cb.record_trade(pnl=-50.0)
    if not cb.is_open:   # is_open = trading allowed
        signal_generator.block_entries()
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, List, Optional

from loguru import logger


class TripReason(str, Enum):
    CONSECUTIVE_LOSSES = "consecutive_losses"
    DRAWDOWN           = "drawdown"
    ERROR_RATE         = "error_rate"
    MANUAL             = "manual"


@dataclass
class TripEvent:
    reason:     TripReason
    tripped_at: float = field(default_factory=time.time)
    detail:     str = ""
    auto_reset_at: Optional[float] = None


@dataclass
class CircuitBreakerConfig:
    # --- Consecutive loss trip ---
    max_consecutive_losses: int = 5

    # --- Rolling drawdown trip ---
    # Max cumulative PnL drop (as fraction, e.g. -0.10 = -10%) in window
    max_drawdown_pct: float = -0.10
    # Window size in number of trades for drawdown calculation
    drawdown_window: int = 20
    # Starting capital reference for pct calculation (0 = disabled)
    capital_reference: float = 0.0

    # --- Error rate trip ---
    # Max fraction of errored orders in last N submissions
    max_error_rate: float = 0.5
    error_window_trades: int = 10

    # --- Cooldown ---
    # Seconds before auto-reset after a trip (0 = manual reset only)
    cooldown_seconds: float = 3600.0  # 1 hour default

    # --- Notifications ---
    # Whether to log a critical alert on trip
    alert_on_trip: bool = True


class CircuitBreaker:
    """
    Monitors trading health and halts new entries when thresholds are breached.

    Parameters
    ----------
    cfg : CircuitBreakerConfig
    """

    def __init__(self, cfg: Optional[CircuitBreakerConfig] = None) -> None:
        self.cfg = cfg or CircuitBreakerConfig()

        self._tripped:            bool = False
        self._trip_event:         Optional[TripEvent] = None
        self._trip_history:       List[TripEvent] = []

        # Consecutive loss counter
        self._consecutive_losses: int = 0

        # Rolling PnL window
        self._pnl_window: Deque[float] = deque(maxlen=self.cfg.drawdown_window)

        # Error rate window: True = error, False = success
        self._error_window: Deque[bool] = deque(maxlen=self.cfg.error_window_trades)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """
        True = circuit closed = trading ALLOWED.
        False = circuit open (tripped) = trading HALTED.
        """
        self._check_auto_reset()
        return not self._tripped

    @property
    def is_tripped(self) -> bool:
        """Inverse of is_open, for explicit check."""
        return not self.is_open

    def record_trade(self, pnl: float) -> None:
        """
        Record a completed trade PnL and update trip counters.

        Parameters
        ----------
        pnl : realised PnL in quote currency (positive = win, negative = loss)
        """
        self._pnl_window.append(pnl)

        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        self._evaluate()

    def record_order_result(self, success: bool) -> None:
        """
        Record an order submission result for error-rate tracking.

        Parameters
        ----------
        success : True if order submitted/filled OK, False if errored
        """
        self._error_window.append(not success)  # True = error
        self._evaluate()

    def trip_manual(self, detail: str = "operator kill-switch") -> None:
        """Manually trip the circuit breaker."""
        self._trip(TripReason.MANUAL, detail)

    def reset(self) -> None:
        """Manually reset (close) the circuit breaker."""
        if self._tripped:
            logger.warning("CircuitBreaker: RESET — trading RESUMED")
        self._tripped        = False
        self._trip_event     = None
        self._consecutive_losses = 0

    def full_reset(self) -> None:
        """Hard reset — clears all counters and history."""
        self.reset()
        self._pnl_window.clear()
        self._error_window.clear()
        self._trip_history.clear()

    def status(self) -> dict:
        """Return current state as dict for dashboard / logging."""
        self._check_auto_reset()
        return {
            "is_open":             self.is_open,
            "tripped":             self._tripped,
            "trip_reason":         self._trip_event.reason.value if self._trip_event else None,
            "trip_detail":         self._trip_event.detail if self._trip_event else None,
            "consecutive_losses":  self._consecutive_losses,
            "rolling_pnl":         round(sum(self._pnl_window), 4),
            "error_rate":          self._current_error_rate(),
            "cooldown_remaining_s": self._cooldown_remaining(),
            "trip_count":          len(self._trip_history),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evaluate(self) -> None:
        """Check all trip conditions. Trip on first match."""
        if self._tripped:
            return

        cfg = self.cfg

        # 1. Consecutive losses
        if self._consecutive_losses >= cfg.max_consecutive_losses:
            self._trip(
                TripReason.CONSECUTIVE_LOSSES,
                f"{self._consecutive_losses} consecutive losses",
            )
            return

        # 2. Rolling drawdown
        if (
            len(self._pnl_window) >= 2
            and cfg.capital_reference > 0
        ):
            cumulative = sum(self._pnl_window)
            dd_pct = cumulative / cfg.capital_reference
            if dd_pct <= cfg.max_drawdown_pct:
                self._trip(
                    TripReason.DRAWDOWN,
                    f"rolling PnL={cumulative:.2f} ({dd_pct:.2%}) <= {cfg.max_drawdown_pct:.2%}",
                )
                return

        # 3. Error rate
        err_rate = self._current_error_rate()
        if (
            len(self._error_window) >= max(3, self.cfg.error_window_trades // 2)
            and err_rate >= cfg.max_error_rate
        ):
            self._trip(
                TripReason.ERROR_RATE,
                f"error_rate={err_rate:.2%} >= {cfg.max_error_rate:.2%}",
            )

    def _trip(self, reason: TripReason, detail: str = "") -> None:
        self._tripped = True
        auto_reset_at = (
            time.time() + self.cfg.cooldown_seconds
            if self.cfg.cooldown_seconds > 0
            else None
        )
        event = TripEvent(reason=reason, detail=detail, auto_reset_at=auto_reset_at)
        self._trip_event = event
        self._trip_history.append(event)

        if self.cfg.alert_on_trip:
            logger.critical(
                f"CircuitBreaker TRIPPED: reason={reason.value} detail={detail} "
                f"cooldown={self.cfg.cooldown_seconds:.0f}s"
            )

    def _check_auto_reset(self) -> None:
        if (
            self._tripped
            and self._trip_event is not None
            and self._trip_event.auto_reset_at is not None
            and time.time() >= self._trip_event.auto_reset_at
        ):
            logger.warning("CircuitBreaker: auto-reset after cooldown — trading RESUMED")
            self._tripped    = False
            self._trip_event = None
            self._consecutive_losses = 0

    def _current_error_rate(self) -> float:
        if not self._error_window:
            return 0.0
        return sum(1 for e in self._error_window if e) / len(self._error_window)

    def _cooldown_remaining(self) -> float:
        if (
            self._tripped
            and self._trip_event is not None
            and self._trip_event.auto_reset_at is not None
        ):
            return max(0.0, self._trip_event.auto_reset_at - time.time())
        return 0.0

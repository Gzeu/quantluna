"""
execution/runner_context.py  —  RunnerContext

Extracted from bybit_live_runner.py (Sprint 28 SRP refactor).
Holds shared runtime state that flows between runner phases.

Usage::

    from execution.runner_context import RunnerContext
    ctx = RunnerContext()
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.spread_monitor import SpreadMonitor
from execution.circuit_breaker import CircuitBreaker
from execution.order_manager import OrderManager
from execution.ws_watchdog import WsWatchdog
from notifications.notifier_bus import NotifierBus


@dataclass
class RunnerContext:
    """Shared context populated during BybitLiveRunner startup phases."""

    should_halt: bool = False
    halt_reason: str  = ""

    order_router:    Optional[Any]            = None
    ws_feed:         Optional[Any]            = None
    spread_monitor:  Optional[SpreadMonitor]  = None
    circuit_breaker: Optional[CircuitBreaker] = None
    order_manager:   Optional[OrderManager]   = None
    watchdog:        Optional[WsWatchdog]     = None
    notifier_bus:    Optional[NotifierBus]    = None

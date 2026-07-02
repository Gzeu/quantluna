"""
Module: strategy/base_strategy.py
Sprint: 31 — T (Multi-Strategy Engine)
Description:
    Abstract base class (ABC) for all QuantLuna strategies plus
    StrategyState enum.  Every concrete strategy must implement:
        generate_signal() -> SignalResult
        on_fill(fill_event)
        on_position_update(position_event)
        get_metrics() -> StrategyMetrics

Usage:
    class MyStrategy(BaseStrategy):
        async def generate_signal(self, data) -> SignalResult: ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StrategyState(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT = "EXIT"
    FLAT = "FLAT"


@dataclass
class SignalResult:
    direction: SignalDirection
    symbol: str
    strength: float = 1.0  # 0..1 normalised
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyMetrics:
    strategy_id: str
    state: StrategyState
    sharpe: float = 0.0
    sortino: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    allocated_capital: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract base for all QuantLuna trading strategies."""

    def __init__(self, strategy_id: str, params: dict[str, Any] | None = None) -> None:
        self.strategy_id = strategy_id
        self.params: dict[str, Any] = params or {}
        self.state = StrategyState.ACTIVE
        self._pnl_history: list[float] = []
        self._trades: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def generate_signal(self, data: dict[str, Any]) -> SignalResult:
        """Generate trading signal from market data snapshot."""
        ...

    @abstractmethod
    async def on_fill(self, fill_event: dict[str, Any]) -> None:
        """Process an ORDER_FILL event."""
        ...

    @abstractmethod
    async def on_position_update(self, position_event: dict[str, Any]) -> None:
        """Process a POSITION_SYNC event."""
        ...

    @abstractmethod
    def get_metrics(self) -> StrategyMetrics:
        """Return current performance metrics."""
        ...

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def pause(self) -> None:
        self.state = StrategyState.PAUSED

    def resume(self) -> None:
        self.state = StrategyState.ACTIVE

    def stop(self) -> None:
        self.state = StrategyState.STOPPED

    def is_active(self) -> bool:
        return self.state == StrategyState.ACTIVE

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_trade(self, pnl: float, metadata: dict[str, Any] | None = None) -> None:
        self._pnl_history.append(pnl)
        self._trades.append({"pnl": pnl, **(metadata or {})})

    def _compute_sharpe(self, risk_free: float = 0.0, window: int = 30) -> float:
        import statistics
        data = self._pnl_history[-window:]
        if len(data) < 2:
            return 0.0
        mean = statistics.mean(data) - risk_free
        std = statistics.stdev(data)
        return (mean / std * (252 ** 0.5)) if std > 0 else 0.0

"""
strategy/base_strategy.py  —  Base classes for the QuantLuna strategy layer.

Contains two ABCs serving different subsystems:

  PairsBaseStrategy
      For pairs trading strategies (kalman_pairs_trading, bb_mean_reversion, …)
      Uses synchronous generate_live() / generate_batch() interface.
      Signal types: Signal (IntEnum), TradeSignal, MarketContext.
      Previously defined in strategy/base.py (Sprint 19).

  BaseStrategy
      For the async multi-strategy engine (multi_strategy_engine.py).
      Uses async generate_signal() / on_fill() / on_position_update() interface.
      Signal types: SignalResult, SignalDirection, StrategyState, StrategyMetrics.
      Original definition from strategy/base_strategy.py (Sprint 31).

strategy/base.py is now a deprecated shim that re-exports PairsBaseStrategy
as BaseStrategy for backward compatibility.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, Optional

import pandas as pd


# ===========================================================================
# Pairs-trading signal types  (previously strategy/base.py — Sprint 19)
# ===========================================================================

class Signal(IntEnum):
    LONG_SPREAD  =  1
    SHORT_SPREAD = -1
    EXIT         =  0
    PARTIAL_EXIT =  2


@dataclass
class TradeSignal:
    """Unified signal output for all pairs strategies."""
    signal: Signal
    confidence: float
    reason: str
    strategy_name: str
    zscore: float = 0.0
    beta: float = 1.0
    alpha: float = 0.0
    spread: float = 0.0
    regime_multiplier: float = 1.0
    half_life_hours: Optional[float] = None
    timestamp: Optional[pd.Timestamp] = None
    meta: Dict = field(default_factory=dict)

    def as_dict(self) -> Dict:
        return {
            "signal":            self.signal.name,
            "confidence":        round(self.confidence, 4),
            "reason":            self.reason,
            "strategy_name":     self.strategy_name,
            "zscore":            round(self.zscore, 4),
            "beta":              round(self.beta, 6),
            "alpha":             round(self.alpha, 6),
            "spread":            round(self.spread, 6),
            "regime_multiplier": round(self.regime_multiplier, 4),
            "half_life_hours":   round(self.half_life_hours, 2) if self.half_life_hours else None,
            "timestamp":         str(self.timestamp) if self.timestamp else None,
            "meta":              self.meta,
        }


@dataclass
class MarketContext:
    """
    Snapshot of market conditions passed to PairsBaseStrategy.score().
    AutoStrategySelector populates this per bar.
    """
    zscore: float = 0.0
    half_life_hours: float = 24.0
    vol_rank: float = 0.5
    regime: str = "ranging"           # "ranging" | "trending" | "breakout" | "unknown"
    funding_annual: float = 0.0
    coint_pvalue: float = 0.05
    spread_autocorr: float = 0.0
    recent_win_rate: float = 0.5
    n_bars_since_entry: int = 0
    is_warm: bool = True

    def to_dict(self) -> Dict:
        return {
            "zscore":             round(self.zscore, 4),
            "half_life_hours":    round(self.half_life_hours, 2),
            "vol_rank":           round(self.vol_rank, 4),
            "regime":             self.regime,
            "funding_annual":     round(self.funding_annual, 6),
            "coint_pvalue":       round(self.coint_pvalue, 6),
            "spread_autocorr":    round(self.spread_autocorr, 4),
            "recent_win_rate":    round(self.recent_win_rate, 4),
            "n_bars_since_entry": self.n_bars_since_entry,
            "is_warm":            self.is_warm,
        }


class PairsBaseStrategy(ABC):
    """
    Abstract base for all pairs-trading strategies.

    Subclasses must implement:
      - name           : str property
      - generate_live  : single-bar online signal
      - generate_batch : vectorised backtest signal
      - score          : suitability score [0, 1] given MarketContext
      - reset          : clear all internal state
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def version(self) -> str:
        return "1.0"

    def describe(self) -> Dict:
        return {"name": self.name, "version": self.version}

    @abstractmethod
    def generate_live(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
        coint_valid: bool = True,
    ) -> TradeSignal: ...

    @abstractmethod
    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        coint_valid_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame: ...

    @abstractmethod
    def score(self, context: "MarketContext") -> float: ...

    @abstractmethod
    def reset(self) -> None: ...

    def _make_exit(self, reason: str, ts: Optional[pd.Timestamp] = None) -> TradeSignal:
        return TradeSignal(
            signal=Signal.EXIT, confidence=0.0,
            reason=reason, strategy_name=self.name, timestamp=ts,
        )


# ===========================================================================
# Multi-strategy engine types  (original strategy/base_strategy.py — Sprint 31)
# ===========================================================================

class StrategyState(str, Enum):
    ACTIVE  = "ACTIVE"
    PAUSED  = "PAUSED"
    STOPPED = "STOPPED"


class SignalDirection(str, Enum):
    LONG  = "LONG"
    SHORT = "SHORT"
    EXIT  = "EXIT"
    FLAT  = "FLAT"


@dataclass
class SignalResult:
    direction: SignalDirection
    symbol: str
    strength: float = 1.0
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
    """Abstract base for all async multi-strategy engine strategies."""

    def __init__(self, strategy_id: str, params: dict[str, Any] | None = None) -> None:
        self.strategy_id = strategy_id
        self.params: dict[str, Any] = params or {}
        self.state = StrategyState.ACTIVE
        self._pnl_history: list[float] = []
        self._trades: list[dict[str, Any]] = []

    @abstractmethod
    async def generate_signal(self, data: dict[str, Any]) -> SignalResult: ...

    @abstractmethod
    async def on_fill(self, fill_event: dict[str, Any]) -> None: ...

    @abstractmethod
    async def on_position_update(self, position_event: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_metrics(self) -> StrategyMetrics: ...

    def pause(self)     -> None: self.state = StrategyState.PAUSED
    def resume(self)    -> None: self.state = StrategyState.ACTIVE
    def stop(self)      -> None: self.state = StrategyState.STOPPED
    def is_active(self) -> bool: return self.state == StrategyState.ACTIVE

    def _record_trade(self, pnl: float, metadata: dict[str, Any] | None = None) -> None:
        self._pnl_history.append(pnl)
        self._trades.append({"pnl": pnl, **(metadata or {})})

    def _compute_sharpe(self, risk_free: float = 0.0, window: int = 30) -> float:
        import statistics
        data = self._pnl_history[-window:]
        if len(data) < 2:
            return 0.0
        mean = statistics.mean(data) - risk_free
        std  = statistics.stdev(data)
        return (mean / std * (252 ** 0.5)) if std > 0 else 0.0

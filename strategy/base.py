"""
QuantLuna — BaseStrategy ABC
Sprint 19

Toate strategiile de trading implementeaza aceasta interfata.
Permite backtest/engine.py si live_trader.py sa ruleze orice strategie
fara modificari, inclusiv compararea side-by-side prin /compare endpoint.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional

import pandas as pd


class Signal(IntEnum):
    LONG_SPREAD  =  1
    SHORT_SPREAD = -1
    EXIT         =  0
    PARTIAL_EXIT =  2


@dataclass
class TradeSignal:
    """Unified signal output for all strategies."""
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


class BaseStrategy(ABC):
    """
    Abstract base for all QuantLuna trading strategies.

    Subclasses must implement:
      - name           : str property
      - generate_live  : single-bar online signal
      - generate_batch : vectorised backtest signal
      - score          : suitability score [0,1] given market context
      - reset          : clear all internal state
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

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
    ) -> TradeSignal:
        ...

    @abstractmethod
    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        coint_valid_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        ...

    @abstractmethod
    def score(self, context: "MarketContext") -> float:
        """
        Suitability score [0, 1] for current market context.
        Used by AutoStrategySelector to choose the best strategy.
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        ...

    def _make_exit(self, reason: str, ts: Optional[pd.Timestamp] = None) -> TradeSignal:
        return TradeSignal(
            signal=Signal.EXIT, confidence=0.0,
            reason=reason, strategy_name=self.name, timestamp=ts,
        )


@dataclass
class MarketContext:
    """
    Snapshot of market conditions passed to BaseStrategy.score().
    AutoStrategySelector populates this per bar.
    """
    zscore: float = 0.0
    half_life_hours: float = 24.0
    vol_rank: float = 0.5
    regime: str = "ranging"           # "ranging" | "trending" | "breakout" | "unknown"
    funding_annual: float = 0.0
    coint_pvalue: float = 0.05
    spread_autocorr: float = 0.0      # lag-1 autocorrelation of spread
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

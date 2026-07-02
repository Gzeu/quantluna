"""
QuantLuna — Volatility Regime Classifier (Sprint 16)

Classifies current market volatility into LOW / NORMAL / HIGH / EXTREME regimes
based on rolling ATR percentile rank and spread volatility.

Used by SignalGenerator and PositionSizer to adapt behaviour:
  - LOW vol     → more aggressive entries, larger size multiplier
  - NORMAL vol  → standard parameters
  - HIGH vol    → tighter thresholds, reduced size
  - EXTREME vol → no new entries, reduce open positions

Usage:
    from core.volatility_regime import VolatilityRegime, VolRegimeConfig, RegimeLabel

    vr = VolatilityRegime(VolRegimeConfig())
    vr.update(spread_return=0.003)
    label = vr.current_regime  # RegimeLabel.NORMAL
    mult  = vr.size_multiplier  # 1.0
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Optional

import numpy as np
from loguru import logger


class RegimeLabel(str, Enum):
    LOW     = "low"
    NORMAL  = "normal"
    HIGH    = "high"
    EXTREME = "extreme"


@dataclass
class VolRegimeConfig:
    """Configuration for volatility regime detection."""

    # Rolling window for percentile calculation (bars)
    lookback: int = 100

    # Percentile thresholds for regime boundaries
    low_pct: float = 25.0       # Below this → LOW
    high_pct: float = 75.0      # Above this → HIGH
    extreme_pct: float = 95.0   # Above this → EXTREME

    # Size multipliers per regime
    size_mult_low: float = 1.25
    size_mult_normal: float = 1.0
    size_mult_high: float = 0.6
    size_mult_extreme: float = 0.0   # Block new entries

    # Entry blocking: block new entries when regime is at or above this level
    block_entry_regime: RegimeLabel = RegimeLabel.EXTREME

    # Smoothing: require N consecutive bars in new regime before switching
    hysteresis_bars: int = 3


class VolatilityRegime:
    """
    Online volatility regime classifier using rolling percentile of spread returns.

    Parameters
    ----------
    cfg : VolRegimeConfig
    """

    def __init__(self, cfg: Optional[VolRegimeConfig] = None) -> None:
        self.cfg = cfg or VolRegimeConfig()
        self._vol_buffer: Deque[float] = deque(maxlen=self.cfg.lookback)
        self._current_regime: RegimeLabel = RegimeLabel.NORMAL
        self._candidate_regime: RegimeLabel = RegimeLabel.NORMAL
        self._candidate_count: int = 0
        self._current_percentile: float = 50.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, spread_return: float) -> RegimeLabel:
        """
        Update with a new spread return observation.

        Parameters
        ----------
        spread_return : absolute return of spread at current bar
                        (e.g. abs((spread_t - spread_{t-1}) / spread_{t-1}))

        Returns
        -------
        RegimeLabel : current regime after update
        """
        self._vol_buffer.append(abs(spread_return))

        if len(self._vol_buffer) < 10:
            return self._current_regime

        buf = np.array(self._vol_buffer)
        current_vol = buf[-1]
        pct = float(np.mean(buf[:-1] <= current_vol) * 100.0)
        self._current_percentile = pct

        candidate = self._classify(pct)
        self._apply_hysteresis(candidate)

        return self._current_regime

    def update_from_prices(
        self,
        price_y: float,
        price_x: float,
        beta: float,
        prev_spread: Optional[float] = None,
    ) -> RegimeLabel:
        """
        Convenience: compute spread return from prices and update.

        Parameters
        ----------
        price_y, price_x : asset prices
        beta             : current Kalman hedge ratio
        prev_spread      : previous spread value (if None, uses 0 return)
        """
        spread = float(price_y - beta * price_x)
        if prev_spread is not None and prev_spread != 0:
            ret = abs((spread - prev_spread) / prev_spread)
        else:
            ret = 0.0
        return self.update(ret)

    @property
    def current_regime(self) -> RegimeLabel:
        return self._current_regime

    @property
    def size_multiplier(self) -> float:
        """Position size multiplier based on current regime."""
        cfg = self.cfg
        mapping = {
            RegimeLabel.LOW:     cfg.size_mult_low,
            RegimeLabel.NORMAL:  cfg.size_mult_normal,
            RegimeLabel.HIGH:    cfg.size_mult_high,
            RegimeLabel.EXTREME: cfg.size_mult_extreme,
        }
        return mapping[self._current_regime]

    @property
    def entry_allowed(self) -> bool:
        """Returns False when regime is too volatile for new entries."""
        order = [RegimeLabel.LOW, RegimeLabel.NORMAL, RegimeLabel.HIGH, RegimeLabel.EXTREME]
        block_idx = order.index(self.cfg.block_entry_regime)
        current_idx = order.index(self._current_regime)
        return current_idx < block_idx

    @property
    def percentile(self) -> float:
        """Current vol percentile rank [0, 100]."""
        return self._current_percentile

    def as_dict(self) -> dict:
        """Serialisable state snapshot for dashboard/logs."""
        return {
            "regime":          self._current_regime.value,
            "percentile":      round(self._current_percentile, 2),
            "size_multiplier": round(self.size_multiplier, 4),
            "entry_allowed":   self.entry_allowed,
            "buffer_len":      len(self._vol_buffer),
        }

    def reset(self) -> None:
        """Hard reset (e.g. between backtest folds)."""
        self._vol_buffer.clear()
        self._current_regime    = RegimeLabel.NORMAL
        self._candidate_regime  = RegimeLabel.NORMAL
        self._candidate_count   = 0
        self._current_percentile = 50.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _classify(self, pct: float) -> RegimeLabel:
        cfg = self.cfg
        if pct >= cfg.extreme_pct:
            return RegimeLabel.EXTREME
        if pct >= cfg.high_pct:
            return RegimeLabel.HIGH
        if pct <= cfg.low_pct:
            return RegimeLabel.LOW
        return RegimeLabel.NORMAL

    def _apply_hysteresis(self, candidate: RegimeLabel) -> None:
        """
        Prevent rapid regime flipping by requiring hysteresis_bars consecutive
        bars in the new regime before switching.
        """
        if candidate == self._candidate_regime:
            self._candidate_count += 1
        else:
            self._candidate_regime = candidate
            self._candidate_count  = 1

        if self._candidate_count >= self.cfg.hysteresis_bars:
            if candidate != self._current_regime:
                logger.info(
                    f"VolRegime: {self._current_regime.value} → {candidate.value} "
                    f"(pct={self._current_percentile:.1f})"
                )
            self._current_regime = candidate

"""
QuantLuna — Z-Score Momentum Strategy
Sprint 19

Logica (opusul Pairs Trading clasic):
  - Intra IN DIRECTIA z-score-ului cand depaseste threshold
  - LONG_SPREAD cand z > +threshold  (spread continua sa creasca)
  - SHORT_SPREAD cand z < -threshold (spread continua sa scada)
  - Exit cu trailing stop la 40% retragere din peak-z sau la exit_threshold

Score optim: trending/breakout, autocorr pozitiva, half-life lung (> 72h),
cointegrare slaba (relatia temporar rupta).
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional

import numpy as np
import pandas as pd
from loguru import logger

from strategy.base import BaseStrategy, MarketContext, Signal, TradeSignal


class ZScoreMomentum(BaseStrategy):

    def __init__(
        self,
        entry_threshold: float = 1.5,
        exit_threshold: float = 0.5,
        stop_threshold: float = 3.5,
        momentum_window: int = 3,
        cooldown: int = 3,
    ) -> None:
        self.entry_threshold = entry_threshold
        self.exit_threshold  = exit_threshold
        self.stop_threshold  = stop_threshold
        self.momentum_window = momentum_window
        self._base_cooldown  = cooldown
        self._zscore_buf: Deque[float] = deque(maxlen=momentum_window + 1)
        self._in_trade = False
        self._entry_side = 0
        self._bars_in_trade = 0
        self._cooldown_remaining = 0
        self._peak_z = 0.0

    @property
    def name(self) -> str:
        return "ZScoreMomentum"

    @property
    def version(self) -> str:
        return "1.0"

    def generate_live(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
        coint_valid: bool = True,
    ) -> TradeSignal:
        zscore = float(y)
        self._zscore_buf.append(zscore)
        sig, conf, reason = self._bar_logic(zscore, funding_annual, regime_multiplier)
        return TradeSignal(
            signal=sig, confidence=conf, reason=reason,
            strategy_name=self.name, zscore=zscore,
            regime_multiplier=regime_multiplier, timestamp=ts,
            meta={"peak_z": self._peak_z, "bars_in_trade": self._bars_in_trade},
        )

    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        coint_valid_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        df = df.copy()
        df["signal"] = int(Signal.EXIT)
        df["confidence"] = 0.0
        df["reason"] = ""
        df["strategy_name"] = self.name
        self.reset()
        if "zscore" not in df.columns:
            return df
        for i in range(len(df)):
            z    = float(df["zscore"].iloc[i])
            fund = float(funding_annual.iloc[i]) if funding_annual is not None else 0.0
            reg  = float(regime_multiplier.iloc[i]) if regime_multiplier is not None else 1.0
            if pd.isna(z): continue
            self._zscore_buf.append(z)
            sig, conf, reason = self._bar_logic(z, fund, reg)
            df.iat[i, df.columns.get_loc("signal")]        = int(sig)
            df.iat[i, df.columns.get_loc("confidence")]    = conf
            df.iat[i, df.columns.get_loc("reason")]        = reason
            df.iat[i, df.columns.get_loc("strategy_name")] = self.name
        return df

    def score(self, context: MarketContext) -> float:
        score = 0.30
        if context.regime == "trending":    score += 0.30
        elif context.regime == "breakout":  score += 0.25
        elif context.regime == "ranging":   score -= 0.20
        if context.spread_autocorr > 0.15:    score += 0.20
        elif context.spread_autocorr > 0.05:  score += 0.10
        elif context.spread_autocorr < -0.10: score -= 0.20
        hl = context.half_life_hours
        if hl > 72:   score += 0.15
        elif hl > 48: score += 0.05
        elif hl < 12: score -= 0.20
        if context.coint_pvalue > 0.10:   score += 0.10
        elif context.coint_pvalue < 0.01: score -= 0.15
        if context.vol_rank > 0.70:   score += 0.10
        elif context.vol_rank < 0.20: score -= 0.10
        return float(np.clip(score, 0.0, 1.0))

    def reset(self) -> None:
        self._zscore_buf.clear()
        self._in_trade = False; self._entry_side = 0
        self._bars_in_trade = 0; self._cooldown_remaining = 0
        self._peak_z = 0.0

    def _has_momentum(self, z: float) -> bool:
        buf = list(self._zscore_buf)
        if len(buf) < self.momentum_window: return False
        tail = buf[-self.momentum_window:]
        return all(v > 0 for v in tail) if z > 0 else all(v < 0 for v in tail)

    def _bar_logic(self, z: float, funding_annual: float, regime_multiplier: float):
        if self._in_trade:
            if abs(z) > abs(self._peak_z): self._peak_z = z
            if abs(self._peak_z) > 0 and abs(z) < abs(self._peak_z) * 0.60:
                return self._do_exit("trailing_stop")
        if self._in_trade:
            if self._entry_side == 1 and z < -self.stop_threshold:
                return self._do_exit("hard_stop_reversal")
            if self._entry_side == -1 and z > self.stop_threshold:
                return self._do_exit("hard_stop_reversal")
        if self._in_trade:
            if self._entry_side == 1 and z <= self.exit_threshold:
                return self._do_exit("momentum_exhausted")
            if self._entry_side == -1 and z >= -self.exit_threshold:
                return self._do_exit("momentum_exhausted")
            self._bars_in_trade += 1
            conf = float(np.clip(abs(z) / max(self.entry_threshold, 1e-9), 0.5, 1.0))
            return (Signal.LONG_SPREAD if self._entry_side == 1 else Signal.SHORT_SPREAD), conf, "hold"
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return Signal.EXIT, 0.0, "cooldown"
        if regime_multiplier <= 0.0:
            return Signal.EXIT, 0.0, "regime_breakdown"
        if z >= self.entry_threshold and self._has_momentum(z):
            conf = float(np.clip((z - self.entry_threshold) / self.entry_threshold, 0.0, 1.0))
            self._in_trade = True; self._entry_side = 1
            self._bars_in_trade = 1; self._peak_z = z
            return Signal.LONG_SPREAD, conf, "momentum_long"
        if z <= -self.entry_threshold and self._has_momentum(z):
            conf = float(np.clip((-z - self.entry_threshold) / self.entry_threshold, 0.0, 1.0))
            self._in_trade = True; self._entry_side = -1
            self._bars_in_trade = 1; self._peak_z = z
            return Signal.SHORT_SPREAD, conf, "momentum_short"
        return Signal.EXIT, 0.0, "no_signal"

    def _do_exit(self, reason: str):
        self._in_trade = False; self._entry_side = 0
        self._bars_in_trade = 0; self._cooldown_remaining = self._base_cooldown
        self._peak_z = 0.0
        return Signal.EXIT, 1.0, reason

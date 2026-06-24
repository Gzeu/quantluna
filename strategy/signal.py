"""
QuantLuna — Signal Generator

Produces directional trading signals from z-score:
  +1 → long Y / short X  (spread below lower band)
  -1 → short Y / long X  (spread above upper band)
   0 → flat / exit
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional
from loguru import logger

from config.settings import SignalConfig
from core.spread import SpreadEngine


class Signal(IntEnum):
    LONG_SPREAD  = 1    # Long Y, Short X
    SHORT_SPREAD = -1   # Short Y, Long X
    EXIT         = 0


@dataclass
class TradeSignal:
    signal: Signal
    zscore: float
    beta: float             # Current Kalman hedge ratio
    alpha: float
    spread: float
    uncertainty: float      # sqrt(P_beta) — hedge ratio confidence
    kalman_gain: float
    half_life_hours: Optional[float]
    confidence: float       # 0-1 based on signal strength and filter warmth
    timestamp: Optional[pd.Timestamp] = None
    reason: str = ""


class SignalGenerator:
    """
    Generates entry/exit signals from SpreadEngine output.

    Rules:
    - Only generate entry if filter is warm (>= 30 bars)
    - No new entries if uncertainty on beta is too high
    - Exit on z-score mean reversion or stop
    """

    def __init__(self, spread_engine: SpreadEngine, cfg: SignalConfig = None):
        self.engine = spread_engine
        self.cfg = cfg or SignalConfig()
        self._in_trade = False
        self._current_signal = Signal.EXIT

    def generate_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate signals on a pre-fitted spread DataFrame.
        Input: output of SpreadEngine.fit()
        Output: same DataFrame + 'signal', 'confidence' columns
        """
        df = df.copy()
        df["signal"] = Signal.EXIT
        df["confidence"] = 0.0

        for i in range(len(df)):
            row = df.iloc[i]
            if not row.get("is_warm", False):
                continue

            z = row["zscore"]
            if pd.isna(z):
                continue

            uncertainty = np.sqrt(row["P_beta"]) if "P_beta" in row else 0
            if uncertainty > 0.5:  # Hedge ratio too uncertain
                df.iloc[i, df.columns.get_loc("signal")] = Signal.EXIT
                continue

            if z <= -self.cfg.zscore_entry:
                sig = Signal.LONG_SPREAD
                conf = min(1.0, abs(z) / (self.cfg.zscore_entry * 2))
            elif z >= self.cfg.zscore_entry:
                sig = Signal.SHORT_SPREAD
                conf = min(1.0, abs(z) / (self.cfg.zscore_entry * 2))
            elif abs(z) <= self.cfg.zscore_exit:
                sig = Signal.EXIT
                conf = 1.0
            else:
                sig = self._current_signal  # Hold
                conf = 0.5

            # Hard stop
            if abs(z) >= self.cfg.zscore_stop:
                sig = Signal.EXIT
                conf = 1.0

            df.iloc[i, df.columns.get_loc("signal")] = int(sig)
            df.iloc[i, df.columns.get_loc("confidence")] = conf
            self._current_signal = sig

        return df

    def generate_live(self, y: float, x: float, ts=None) -> TradeSignal:
        """Single-bar update for live trading."""
        state = self.engine.update_one(y, x, ts=ts)
        z = state["zscore"]

        if not state["is_warm"]:
            return TradeSignal(Signal.EXIT, z, state["beta"], state["alpha"],
                               state["spread"], state["uncertainty"],
                               state["kalman_gain"], None, 0.0, ts,
                               reason="filter_warming_up")

        if state["uncertainty"] > 0.5:
            return TradeSignal(Signal.EXIT, z, state["beta"], state["alpha"],
                               state["spread"], state["uncertainty"],
                               state["kalman_gain"], None, 0.0, ts,
                               reason="high_uncertainty")

        if z <= -self.cfg.zscore_entry:
            sig, reason = Signal.LONG_SPREAD, "zscore_below_entry"
            conf = min(1.0, abs(z) / (self.cfg.zscore_entry * 2))
        elif z >= self.cfg.zscore_entry:
            sig, reason = Signal.SHORT_SPREAD, "zscore_above_entry"
            conf = min(1.0, abs(z) / (self.cfg.zscore_entry * 2))
        elif abs(z) <= self.cfg.zscore_exit:
            sig, reason = Signal.EXIT, "zscore_at_exit"
            conf = 1.0
        elif abs(z) >= self.cfg.zscore_stop:
            sig, reason = Signal.EXIT, "hard_stop"
            conf = 1.0
        else:
            sig, reason = self._current_signal, "hold"
            conf = 0.5

        self._current_signal = sig
        return TradeSignal(sig, z, state["beta"], state["alpha"],
                           state["spread"], state["uncertainty"],
                           state["kalman_gain"], None, conf, ts, reason=reason)

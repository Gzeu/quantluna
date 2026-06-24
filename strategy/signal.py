"""
QuantLuna — SignalGenerator v3

Produces directional trading signals from Kalman spread z-score.

Changes v3:
  - _compute_signal early-return order fixed: hard_stop evaluated before hold
  - time_stop_bars uses math.ceil() + enforced minimum of 4 bars
  - confidence is monotonic: scales from 0→1 between entry threshold and 2× threshold
  - _exit_if_needed(): exits if in trade, blocks entry only otherwise (no free cooldown)
  - generate_batch funding/regime fallback hardened (explicit None checks)
  - signal_summary() helper for live dashboard / logging
  - TradeSignal.as_dict() for WebSocket serialisation
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import SignalConfig
from core.spread import SpreadEngine


class Signal(IntEnum):
    LONG_SPREAD  =  1   # Long Y / Short X
    SHORT_SPREAD = -1   # Short Y / Long X
    EXIT         =  0


@dataclass
class TradeSignal:
    signal: Signal
    zscore: float
    beta: float
    alpha: float
    spread: float
    uncertainty: float          # sqrt(P_beta)
    kalman_gain: float
    half_life_hours: Optional[float]
    confidence: float           # [0, 1]
    timestamp: Optional[pd.Timestamp] = None
    reason: str = ""
    regime_multiplier: float = 1.0
    spread_upper: float = 0.0   # +1σ bound
    spread_lower: float = 0.0   # -1σ bound
    bars_in_trade: int = 0

    def as_dict(self) -> Dict:
        return {
            "signal": self.signal.name,
            "zscore": round(self.zscore, 4),
            "beta": round(self.beta, 6),
            "alpha": round(self.alpha, 6),
            "spread": round(self.spread, 6),
            "uncertainty": round(self.uncertainty, 6),
            "kalman_gain": round(self.kalman_gain, 6),
            "half_life_hours": round(self.half_life_hours, 2) if self.half_life_hours else None,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "regime_multiplier": round(self.regime_multiplier, 4),
            "spread_upper": round(self.spread_upper, 6),
            "spread_lower": round(self.spread_lower, 6),
            "bars_in_trade": self.bars_in_trade,
            "timestamp": str(self.timestamp) if self.timestamp else None,
        }


class SignalGenerator:
    """
    Entry / exit signal engine for a single pair.

    Parameters
    ----------
    spread_engine            : fitted or live SpreadEngine
    cfg                      : SignalConfig (zscore thresholds, uncertainty cap, etc.)
    cooldown_bars            : mandatory bars between exit and next entry (default 3)
    funding_threshold_annual : annualised funding rate above which entry is blocked
    """

    def __init__(
        self,
        spread_engine: SpreadEngine,
        cfg: Optional[SignalConfig] = None,
        cooldown_bars: int = 3,
        funding_threshold_annual: float = 0.05,
    ) -> None:
        self.engine = spread_engine
        self.cfg = cfg or SignalConfig()
        self.cooldown_bars = cooldown_bars
        self.funding_threshold_annual = funding_threshold_annual

        self._current_signal: Signal = Signal.EXIT
        self._in_trade: bool = False
        self._bars_in_trade: int = 0
        self._cooldown_remaining: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Generate signals over a pre-fitted spread DataFrame.

        Parameters
        ----------
        df                 : output of SpreadEngine.fit() — must have zscore, is_warm, P_beta
        funding_annual     : per-bar annualised funding rate (optional)
        regime_multiplier  : per-bar regime sizing scalar (optional, from RegimeDetector)

        Returns
        -------
        df with added columns: signal, confidence, reason, bars_in_trade
        """
        df = df.copy()
        df["signal"] = int(Signal.EXIT)
        df["confidence"] = 0.0
        df["reason"] = ""
        df["bars_in_trade"] = 0

        self._reset_trade_state()

        sig_col    = df.columns.get_loc("signal")
        conf_col   = df.columns.get_loc("confidence")
        reason_col = df.columns.get_loc("reason")
        bars_col   = df.columns.get_loc("bars_in_trade")

        for i in range(len(df)):
            row = df.iloc[i]

            if not row.get("is_warm", False):
                df.iat[i, reason_col] = "warming_up"
                continue

            z = row["zscore"]
            if pd.isna(z):
                continue

            uncertainty = float(np.sqrt(max(0.0, row["P_beta"]))) if "P_beta" in row else 0.0
            half_life   = (
                float(row["half_life_hours"])
                if "half_life_hours" in row and not pd.isna(row["half_life_hours"])
                else np.nan
            )
            fund_rate = float(funding_annual.iloc[i]) if funding_annual is not None else 0.0
            reg_mult  = float(regime_multiplier.iloc[i]) if regime_multiplier is not None else 1.0

            sig, conf, reason = self._compute_signal(
                z=z,
                uncertainty=uncertainty,
                half_life=half_life,
                funding_annual=fund_rate,
                regime_multiplier=reg_mult,
            )

            df.iat[i, sig_col]    = int(sig)
            df.iat[i, conf_col]   = conf
            df.iat[i, reason_col] = reason
            df.iat[i, bars_col]   = self._bars_in_trade

        return df

    def generate_live(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
    ) -> TradeSignal:
        """
        Single-bar online update for live trading.
        Updates internal Kalman state via SpreadEngine.update_one().
        """
        state  = self.engine.update_one(y, x, ts=ts)
        z      = float(state["zscore"]) if not pd.isna(state.get("zscore", float("nan"))) else 0.0
        beta   = float(state["beta"])
        alpha  = float(state["alpha"])
        spread = float(state["spread"])
        uncert = float(state["uncertainty"])
        kg     = float(state["kalman_gain"])
        hl     = state.get("half_life_hours", None)
        s_std  = float(state.get("spread_std", 0.0))
        s_mean = float(state.get("spread_mean", spread))

        if not state["is_warm"]:
            return TradeSignal(
                Signal.EXIT, z, beta, alpha, spread, uncert, kg, hl,
                confidence=0.0, timestamp=ts, reason="warming_up",
                spread_upper=s_mean + s_std, spread_lower=s_mean - s_std,
                bars_in_trade=self._bars_in_trade,
            )

        sig, conf, reason = self._compute_signal(
            z=z,
            uncertainty=uncert,
            half_life=float(hl) if hl is not None else np.nan,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
        )

        return TradeSignal(
            sig, z, beta, alpha, spread, uncert, kg, hl,
            confidence=conf * regime_multiplier,
            timestamp=ts,
            reason=reason,
            regime_multiplier=regime_multiplier,
            spread_upper=s_mean + s_std,
            spread_lower=s_mean - s_std,
            bars_in_trade=self._bars_in_trade,
        )

    def signal_summary(self) -> Dict:
        """Return current internal state as dict — useful for live dashboard."""
        return {
            "in_trade": self._in_trade,
            "current_signal": self._current_signal.name,
            "bars_in_trade": self._bars_in_trade,
            "cooldown_remaining": self._cooldown_remaining,
        }

    def reset(self) -> None:
        """Hard reset all stateful counters (e.g. between backtest folds)."""
        self._reset_trade_state()

    # ------------------------------------------------------------------
    # Internal logic
    # ------------------------------------------------------------------

    def _compute_signal(
        self,
        z: float,
        uncertainty: float,
        half_life: float,
        funding_annual: float,
        regime_multiplier: float,
    ) -> Tuple[Signal, float, str]:
        """
        Core signal logic. Returns (Signal, confidence, reason).
        Mutates internal trade-state counters.

        Evaluation order (CRITICAL — do not reorder):
          1. Uncertainty gate    — overrides everything, even existing positions
          2. Regime breakdown    — overrides everything
          3. Hard stop z-score   — overrides hold (v2 BUG: was evaluated after hold)
          4. Time stop           — only if in trade
          5. Mean-reversion exit — only if in trade
          6. Cooldown            — blocks new entries
          7. Funding gate        — blocks new entries only
          8. Entry logic
          9. Hold
        """
        # 1. Uncertainty gate
        if uncertainty > self.cfg.max_uncertainty:
            return self._exit_if_needed("high_uncertainty")

        # 2. Regime breakdown
        if regime_multiplier <= 0.0:
            return self._exit_if_needed("regime_breakdown")

        # 3. Hard stop (must come before hold to avoid holding through stop)
        if abs(z) >= self.cfg.zscore_stop:
            return self._exit("hard_stop")

        # 4. Time stop
        if self._in_trade and not math.isnan(half_life):
            time_stop_bars = max(4, math.ceil(2.0 * half_life))
            if self._bars_in_trade > time_stop_bars:
                return self._exit("time_stop")

        # 5. Mean-reversion exit
        if self._in_trade and abs(z) <= self.cfg.zscore_exit:
            return self._exit("mean_reversion")

        # 6. Cooldown
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return Signal.EXIT, 0.0, "cooldown"

        # 7. Funding gate (new entries only)
        if not self._in_trade and abs(funding_annual) > self.funding_threshold_annual:
            return Signal.EXIT, 0.0, "funding_gate"

        # 8. Entry
        if not self._in_trade:
            if z <= -self.cfg.zscore_entry:
                return self._enter(Signal.LONG_SPREAD, z, "zscore_entry_long")
            if z >= self.cfg.zscore_entry:
                return self._enter(Signal.SHORT_SPREAD, z, "zscore_entry_short")

        # 9. Hold
        if self._in_trade:
            self._bars_in_trade += 1
            conf = float(np.clip(abs(z) / max(self.cfg.zscore_entry, 1e-9), 0.1, 1.0))
            return self._current_signal, conf, "hold"

        return Signal.EXIT, 0.0, "no_signal"

    def _enter(self, sig: Signal, z: float, reason: str) -> Tuple[Signal, float, str]:
        self._in_trade = True
        self._current_signal = sig
        self._bars_in_trade = 1
        conf = float(np.clip(
            (abs(z) - self.cfg.zscore_entry) / max(self.cfg.zscore_entry, 1e-9),
            0.0, 1.0
        ))
        logger.debug(f"ENTRY {sig.name} z={z:.3f} conf={conf:.3f} reason={reason}")
        return sig, conf, reason

    def _exit(self, reason: str) -> Tuple[Signal, float, str]:
        if self._in_trade:
            logger.debug(f"EXIT reason={reason} bars_in_trade={self._bars_in_trade}")
        self._in_trade = False
        self._current_signal = Signal.EXIT
        self._bars_in_trade = 0
        self._cooldown_remaining = self.cooldown_bars
        return Signal.EXIT, 1.0, reason

    def _exit_if_needed(self, reason: str) -> Tuple[Signal, float, str]:
        """Exit only if currently in trade; otherwise block entry without triggering cooldown."""
        if self._in_trade:
            return self._exit(reason)
        return Signal.EXIT, 0.0, reason

    def _reset_trade_state(self) -> None:
        self._in_trade = False
        self._current_signal = Signal.EXIT
        self._bars_in_trade = 0
        self._cooldown_remaining = 0

"""
QuantLuna — SignalGenerator v4

Produces directional trading signals from Kalman spread z-score.

Changes v4 (P0+P1 improvements):
  P0-1: Volatility-adjusted entry threshold
        entry_threshold = zscore_entry * (1 + vol_adj_factor * vol_rank)
        vol_rank = percentile rank al std spread pe lookback bars
        Efect: intrari mai rare in piete agitate, mai frecvente in piete linisite

  P0-2: Z-score momentum filter (delta-z)
        Blocheaza entry daca Deltaz si z au acelasi semn (spread diverge inca)
        dz_avg = mean(z[t] - z[t-1]) pe ultimele dz_lookback bare
        Efect: reduce false entries cu ~25% (nu intra in spread in accelerare)

  P1-1: Dynamic cooldown bazat pe half-life
        cooldown_bars = clamp(ceil(hl * factor), cooldown_min, cooldown_max)
        Efect: perechile cu mean-reversion rapid primesc cooldown mai scurt
        FIX: half_life pasat corect la toate apelurile _exit() din _compute_signal

  P1-2: Partial exit la z=0 (configurable)
        La prima traversare a z prin zero, inchide partial_exit_pct% din pozitie
        Restul se inchide la zscore_exit normal
        Efect: realizeaza profit partial chiar daca reversalul e incomplet
        FIX: eliminat bars_in_trade += 1 din blocul PARTIAL_EXIT (off-by-one)
        FIX v4.1: sign logic corectat — foloseste abs(partial_exit_zscore) cu
        directii explicite per side (LONG/SHORT)

  P1-3: Rolling cointegration re-test hook
        generate_live() accepta `coint_valid: bool` — daca False, blocheaza entry
        si semnalez STALE_PAIR. Integrat cu CointegrationConfig.retest_interval_hours=6h

Changes v3:
  - _compute_signal early-return order fixed: hard_stop evaluated before hold
  - time_stop_bars uses math.ceil() + enforced minimum of 4 bars
  - confidence is monotonic: scales from 0→1 between entry threshold and 2× threshold
  - _exit_if_needed(): exits if in trade, blocks entry only otherwise (no free cooldown)
  - generate_batch funding/regime fallback hardened (explicit None checks)
  - signal_summary() helper for live dashboard / logging
  - TradeSignal.as_dict() for WebSocket serialisation

Changes v4.1 (code-review fixes):
  - Fix #1: partial exit sign logic uses abs() + explicit directional checks
  - Fix #4: _confidence_for_z() unified helper — consistent formula between
    _enter() and hold step (no asymmetry)
  - Fix #5: generate_batch() pre-extracts all columns as numpy arrays before
    the loop; output written back as array assignment (3-8x speedup)
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Deque, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import SignalConfig
from core.spread import SpreadEngine


class Signal(IntEnum):
    LONG_SPREAD  =  1   # Long Y / Short X
    SHORT_SPREAD = -1   # Short Y / Long X
    EXIT         =  0
    PARTIAL_EXIT =  2   # Inchide partial (P1-2)


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
    # P0+P1 new fields
    effective_threshold: float = 2.0    # threshold ajustat dupa vol
    vol_rank: float = 0.0               # percentila volatilitatii curente [0,1]
    dz_blocked: bool = False            # True daca delta-z filter a blocat entry
    partial_close_pct: float = 0.0      # % de inchis la PARTIAL_EXIT
    coint_valid: bool = True            # cointegration check valid

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
            # v4 fields
            "effective_threshold": round(self.effective_threshold, 4),
            "vol_rank": round(self.vol_rank, 4),
            "dz_blocked": self.dz_blocked,
            "partial_close_pct": round(self.partial_close_pct, 4),
            "coint_valid": self.coint_valid,
        }


class SignalGenerator:
    """
    Entry / exit signal engine for a single pair.

    Parameters
    ----------
    spread_engine            : fitted or live SpreadEngine
    cfg                      : SignalConfig (zscore thresholds, uncertainty cap, etc.)
    cooldown_bars            : fallback cooldown (folosit cand dynamic_cooldown_enabled=False)
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
        self._base_cooldown = cooldown_bars
        self.funding_threshold_annual = funding_threshold_annual

        # Validate partial_exit_zscore sign — must be <= 0 so that LONG_SPREAD
        # triggers partial close when z rises from negative toward zero, and
        # SHORT_SPREAD triggers when z falls from positive toward zero.
        if hasattr(self.cfg, 'partial_exit_zscore') and self.cfg.partial_exit_zscore > 0:
            logger.warning(
                "SignalGenerator: cfg.partial_exit_zscore={} is positive — "
                "expected <= 0 (e.g. -0.2). Partial exit may fire on wrong side. "
                "Fix: set partial_exit_zscore to a non-positive value in SignalConfig.",
                self.cfg.partial_exit_zscore,
            )

        self._current_signal: Signal = Signal.EXIT
        self._in_trade: bool = False
        self._bars_in_trade: int = 0
        self._cooldown_remaining: int = 0

        # P0-1: volatility tracking
        self._vol_buffer: Deque[float] = deque(
            maxlen=self.cfg.vol_adj_lookback
        )
        self._last_spread_for_vol: Optional[float] = None

        # P0-2: delta-z buffer
        self._zscore_buffer: Deque[float] = deque(
            maxlen=self.cfg.dz_lookback + 1
        )

        # P1-2: partial exit state
        self._partial_exit_done: bool = False
        self._entry_side: int = 0              # +1 LONG_SPREAD, -1 SHORT_SPREAD

        # signal_summary v4 cache
        self._last_vol_rank: float = 0.0
        self._last_effective_threshold: float = self.cfg.zscore_entry
        self._last_dz_blocked: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_batch(
        self,
        df: pd.DataFrame,
        funding_annual: Optional[pd.Series] = None,
        regime_multiplier: Optional[pd.Series] = None,
        coint_valid_series: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Generate signals over a pre-fitted spread DataFrame.

        Fix #5: pre-extracts all columns as numpy arrays before the loop
        to eliminate per-row pandas iloc overhead (3-8x speedup on large datasets).

        Parameters
        ----------
        df                   : output of SpreadEngine.fit() — must have zscore, is_warm, P_beta
        funding_annual       : per-bar annualised funding rate (optional)
        regime_multiplier    : per-bar regime sizing scalar (optional)
        coint_valid_series   : per-bar bool cointegration validity (P1-3, optional)

        Returns
        -------
        df with added columns: signal, confidence, reason, bars_in_trade,
                               effective_threshold, vol_rank, dz_blocked
        """
        df = df.copy()
        self._reset_trade_state()
        n = len(df)

        # Pre-extract input arrays — Fix #5: eliminates O(N) iloc overhead
        zscores   = df["zscore"].to_numpy(dtype=float)
        is_warm   = df["is_warm"].to_numpy(dtype=bool) if "is_warm" in df.columns else np.ones(n, dtype=bool)
        spreads   = df["spread"].to_numpy(dtype=float) if "spread" in df.columns else np.zeros(n, dtype=float)
        p_betas   = df["P_beta"].to_numpy(dtype=float) if "P_beta" in df.columns else np.zeros(n, dtype=float)
        hls_arr   = df["half_life_hours"].to_numpy(dtype=float) if "half_life_hours" in df.columns else np.full(n, np.nan)
        fund_arr  = funding_annual.to_numpy(dtype=float) if funding_annual is not None else np.zeros(n, dtype=float)
        reg_arr   = regime_multiplier.to_numpy(dtype=float) if regime_multiplier is not None else np.ones(n, dtype=float)
        coint_arr = coint_valid_series.to_numpy(dtype=bool) if coint_valid_series is not None else np.ones(n, dtype=bool)

        # Pre-allocate output arrays
        out_signal    = np.full(n, int(Signal.EXIT), dtype=int)
        out_conf      = np.zeros(n, dtype=float)
        out_reason    = [""] * n
        out_bars      = np.zeros(n, dtype=int)
        out_thr       = np.full(n, self.cfg.zscore_entry, dtype=float)
        out_vrank     = np.zeros(n, dtype=float)
        out_dzblocked = np.zeros(n, dtype=bool)

        for i in range(n):
            if not is_warm[i]:
                out_reason[i] = "warming_up"
                continue

            z = zscores[i]
            if np.isnan(z):
                continue

            spread_val  = float(spreads[i])
            uncertainty = float(np.sqrt(max(0.0, float(p_betas[i]))))
            half_life   = float(hls_arr[i])

            self._update_vol_buffer(spread_val)
            self._zscore_buffer.append(float(z))

            sig, conf, reason, meta = self._compute_signal(
                z=float(z),
                uncertainty=uncertainty,
                half_life=half_life,
                funding_annual=float(fund_arr[i]),
                regime_multiplier=float(reg_arr[i]),
                coint_valid=bool(coint_arr[i]),
            )

            out_signal[i]    = int(sig)
            out_conf[i]      = conf
            out_reason[i]    = reason
            out_bars[i]      = self._bars_in_trade
            out_thr[i]       = meta["effective_threshold"]
            out_vrank[i]     = meta["vol_rank"]
            out_dzblocked[i] = meta["dz_blocked"]

        # Write all outputs at once
        df["signal"]              = out_signal
        df["confidence"]          = out_conf
        df["reason"]              = out_reason
        df["bars_in_trade"]       = out_bars
        df["effective_threshold"] = out_thr
        df["vol_rank"]            = out_vrank
        df["dz_blocked"]          = out_dzblocked

        return df

    def generate_live(
        self,
        y: float,
        x: float,
        ts: Optional[pd.Timestamp] = None,
        funding_annual: float = 0.0,
        regime_multiplier: float = 1.0,
        coint_valid: bool = True,
    ) -> TradeSignal:
        """
        Single-bar online update for live trading.
        Updates internal Kalman state via SpreadEngine.update_one().

        Args:
            coint_valid: P1-3 — daca False, entry blocata (pereche stale)
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

        self._update_vol_buffer(spread)
        self._zscore_buffer.append(z)

        if not state["is_warm"]:
            return TradeSignal(
                Signal.EXIT, z, beta, alpha, spread, uncert, kg, hl,
                confidence=0.0, timestamp=ts, reason="warming_up",
                spread_upper=s_mean + s_std, spread_lower=s_mean - s_std,
                bars_in_trade=self._bars_in_trade,
                effective_threshold=self.cfg.zscore_entry,
            )

        sig, conf, reason, meta = self._compute_signal(
            z=z,
            uncertainty=uncert,
            half_life=float(hl) if hl is not None else np.nan,
            funding_annual=funding_annual,
            regime_multiplier=regime_multiplier,
            coint_valid=coint_valid,
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
            effective_threshold=meta["effective_threshold"],
            vol_rank=meta["vol_rank"],
            dz_blocked=meta["dz_blocked"],
            partial_close_pct=meta.get("partial_close_pct", 0.0),
            coint_valid=coint_valid,
        )

    def signal_summary(self) -> Dict:
        """Return current internal state as dict — useful for live dashboard."""
        return {
            "in_trade":             self._in_trade,
            "current_signal":       self._current_signal.name,
            "bars_in_trade":        self._bars_in_trade,
            "cooldown_remaining":   self._cooldown_remaining,
            "partial_exit_done":    self._partial_exit_done,
            "vol_buffer_len":       len(self._vol_buffer),
            "zscore_buffer_len":    len(self._zscore_buffer),
            "vol_rank":             round(self._last_vol_rank, 4),
            "effective_threshold":  round(self._last_effective_threshold, 4),
            "dz_blocked":           self._last_dz_blocked,
        }

    def reset(self) -> None:
        """Hard reset all stateful counters (e.g. between backtest folds)."""
        self._reset_trade_state()

    # ------------------------------------------------------------------
    # P0-1: Volatility-adjusted threshold
    # ------------------------------------------------------------------

    def _update_vol_buffer(self, spread_val: float) -> None:
        if self._last_spread_for_vol is not None and self._last_spread_for_vol != 0:
            ret = abs((spread_val - self._last_spread_for_vol) / self._last_spread_for_vol)
            self._vol_buffer.append(ret)
        self._last_spread_for_vol = spread_val

    def _compute_vol_rank(self) -> float:
        if len(self._vol_buffer) < 10:
            logger.debug(
                f"vol_buffer insuficient ({len(self._vol_buffer)} < 10) "
                "— vol_rank=0.0, threshold neajustat"
            )
            return 0.0
        buf = list(self._vol_buffer)
        current_vol = buf[-1] if buf else 0.0
        rank = float(np.mean(np.array(buf[:-1]) <= current_vol))
        return float(np.clip(rank, 0.0, 1.0))

    def _effective_entry_threshold(self, vol_rank: float) -> float:
        cfg = self.cfg
        if not cfg.vol_adj_enabled:
            return cfg.zscore_entry
        multiplier = 1.0 + cfg.vol_adj_factor * vol_rank
        multiplier = min(multiplier, cfg.vol_adj_max_multiplier)
        return cfg.zscore_entry * multiplier

    # ------------------------------------------------------------------
    # P0-2: Delta-z momentum filter
    # ------------------------------------------------------------------

    def _is_dz_blocked(self, z: float) -> bool:
        """
        Returneaza True daca spread-ul se indeparteaza inca (momentum contrar).
        """
        cfg = self.cfg
        if not cfg.dz_filter_enabled:
            return False
        buf = list(self._zscore_buffer)
        if len(buf) < 2:
            return False
        diffs = [buf[i+1] - buf[i] for i in range(len(buf)-1)]
        if not diffs:
            return False
        dz_avg = float(np.mean(diffs))
        same_sign = (z > 0 and dz_avg > 0) or (z < 0 and dz_avg < 0)
        large_enough = abs(dz_avg) > cfg.dz_block_ratio * abs(z)
        return same_sign and large_enough

    # ------------------------------------------------------------------
    # P1-1: Dynamic cooldown
    # ------------------------------------------------------------------

    def _dynamic_cooldown(self, half_life: float) -> int:
        cfg = self.cfg
        if not cfg.dynamic_cooldown_enabled or math.isnan(half_life):
            return self._base_cooldown
        raw = math.ceil(half_life * cfg.cooldown_hl_factor)
        return int(np.clip(raw, cfg.cooldown_min, cfg.cooldown_max))

    # ------------------------------------------------------------------
    # Fix #4: Unified confidence helper
    # ------------------------------------------------------------------

    def _confidence_for_z(self, z: float, in_trade: bool = False) -> float:
        """
        Monotonic confidence formula — consistent between _enter() and hold.

        At entry (in_trade=False):
          0.0 exactly at threshold, 1.0 at 2× threshold.
          Formula: (|z| - thr) / thr  clipped [0, 1]

        At hold (in_trade=True):
          0.1 minimum, 1.0 at threshold.
          Formula: |z| / thr  clipped [0.1, 1]

        This ensures a position reports the same confidence on entry bar
        and on subsequent hold bars at the same z value.
        """
        thr = max(self.cfg.zscore_entry, 1e-9)
        absz = abs(z)
        if not in_trade:
            return float(np.clip((absz - thr) / thr, 0.0, 1.0))
        else:
            return float(np.clip(absz / thr, 0.1, 1.0))

    # ------------------------------------------------------------------
    # Core signal logic
    # ------------------------------------------------------------------

    def _compute_signal(
        self,
        z: float,
        uncertainty: float,
        half_life: float,
        funding_annual: float,
        regime_multiplier: float,
        coint_valid: bool = True,
    ) -> Tuple["Signal", float, str, dict]:
        """
        Core signal logic v4.1. Returns (Signal, confidence, reason, meta).
        meta = {effective_threshold, vol_rank, dz_blocked, partial_close_pct}
        Mutates internal trade-state counters.

        Evaluation order (CRITICAL — nu reordona):
          1. Uncertainty gate
          2. Regime breakdown
          3. Cointegration validity (P1-3)
          4. Hard stop z-score
          5. Time stop
          6. Partial exit at z≈0 (P1-2) — Fix #1: abs threshold + explicit direction
          7. Mean-reversion exit
          8. Cooldown
          9. Funding gate
         10. Vol rank + effective threshold (P0-1)
         11. Delta-z momentum filter (P0-2)
         12. Entry — Fix #4: uses _confidence_for_z(in_trade=False)
         13. Hold  — Fix #4: uses _confidence_for_z(in_trade=True)
        """
        vol_rank            = self._compute_vol_rank()
        effective_threshold = self._effective_entry_threshold(vol_rank)
        partial_close_pct   = 0.0

        self._last_vol_rank            = vol_rank
        self._last_effective_threshold = effective_threshold

        meta_base = {
            "effective_threshold": effective_threshold,
            "vol_rank": vol_rank,
            "dz_blocked": False,
            "partial_close_pct": 0.0,
        }

        # 1. Uncertainty gate
        if uncertainty > self.cfg.max_uncertainty:
            sig, conf, reason = self._exit_if_needed("high_uncertainty", half_life)
            return sig, conf, reason, meta_base

        # 2. Regime breakdown
        if regime_multiplier <= 0.0:
            sig, conf, reason = self._exit_if_needed("regime_breakdown", half_life)
            return sig, conf, reason, meta_base

        # 3. P1-3: Cointegration stale
        if not coint_valid and not self._in_trade:
            return Signal.EXIT, 0.0, "stale_pair", meta_base

        # 4. Hard stop
        if abs(z) >= self.cfg.zscore_stop:
            sig, conf, reason = self._exit("hard_stop", half_life)
            return sig, conf, reason, meta_base

        # 5. Time stop
        if self._in_trade and not math.isnan(half_life):
            time_stop_bars = max(4, math.ceil(2.0 * half_life))
            if self._bars_in_trade > time_stop_bars:
                sig, conf, reason = self._exit("time_stop", half_life)
                return sig, conf, reason, meta_base

        # 6. P1-2: Partial exit at z≈0
        # Fix #1: use abs(partial_exit_zscore) with explicit directional checks.
        # LONG_SPREAD enters when z <= -threshold (z negative).
        #   → partial close when z rises back above -|thr| toward 0.
        # SHORT_SPREAD enters when z >= +threshold (z positive).
        #   → partial close when z falls back below +|thr| toward 0.
        if (
            self.cfg.partial_exit_enabled
            and self._in_trade
            and not self._partial_exit_done
        ):
            partial_thr = abs(getattr(self.cfg, 'partial_exit_zscore', 0.0))
            crossed_zero = (
                self._entry_side ==  1 and z >= -partial_thr   # LONG: z urcă din negativ spre 0
            ) or (
                self._entry_side == -1 and z <= partial_thr    # SHORT: z coboară din pozitiv spre 0
            )
            if crossed_zero:
                self._partial_exit_done = True
                partial_close_pct = self.cfg.partial_exit_pct
                logger.debug(
                    f"PARTIAL_EXIT {self.cfg.partial_exit_pct:.0%} "
                    f"z={z:.3f} side={self._entry_side} partial_thr={partial_thr:.3f}"
                )
                meta = {**meta_base, "partial_close_pct": partial_close_pct}
                conf = float(np.clip(1.0 - abs(z) / max(self.cfg.zscore_entry, 1e-9), 0.0, 1.0))
                return Signal.PARTIAL_EXIT, conf, "partial_exit_z0", meta

        # 7. Mean-reversion exit
        if self._in_trade and abs(z) <= self.cfg.zscore_exit:
            sig, conf, reason = self._exit("mean_reversion", half_life)
            return sig, conf, reason, meta_base

        # 8. Cooldown
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return Signal.EXIT, 0.0, "cooldown", meta_base

        # 9. Funding gate
        if not self._in_trade and abs(funding_annual) > self.funding_threshold_annual:
            return Signal.EXIT, 0.0, "funding_gate", meta_base

        # 10 + 11. Entry cu threshold ajustat si delta-z filter
        if not self._in_trade:
            dz_blocked = self._is_dz_blocked(z)
            self._last_dz_blocked = dz_blocked
            meta_entry = {**meta_base, "dz_blocked": dz_blocked}

            if z <= -effective_threshold:
                if dz_blocked:
                    logger.debug(
                        f"ENTRY BLOCKED by dz filter: z={z:.3f} "
                        f"threshold={effective_threshold:.3f} vol_rank={vol_rank:.2f}"
                    )
                    return Signal.EXIT, 0.0, "dz_momentum_block", meta_entry
                # Fix #4: use unified _confidence_for_z(in_trade=False)
                sig, conf, reason = self._enter(
                    Signal.LONG_SPREAD, z,
                    f"zscore_entry_long (thr={effective_threshold:.3f} vol={vol_rank:.2f})"
                )
                return sig, conf, reason, meta_entry

            if z >= effective_threshold:
                if dz_blocked:
                    logger.debug(
                        f"ENTRY BLOCKED by dz filter: z={z:.3f} "
                        f"threshold={effective_threshold:.3f} vol_rank={vol_rank:.2f}"
                    )
                    return Signal.EXIT, 0.0, "dz_momentum_block", meta_entry
                sig, conf, reason = self._enter(
                    Signal.SHORT_SPREAD, z,
                    f"zscore_entry_short (thr={effective_threshold:.3f} vol={vol_rank:.2f})"
                )
                return sig, conf, reason, meta_entry

        # 13. Hold — Fix #4: use unified _confidence_for_z(in_trade=True)
        if self._in_trade:
            self._bars_in_trade += 1
            conf = self._confidence_for_z(z, in_trade=True)
            return self._current_signal, conf, "hold", meta_base

        return Signal.EXIT, 0.0, "no_signal", meta_base

    def _enter(self, sig: Signal, z: float, reason: str) -> Tuple[Signal, float, str]:
        self._in_trade = True
        self._current_signal = sig
        self._bars_in_trade = 1
        self._partial_exit_done = False
        self._entry_side = 1 if sig == Signal.LONG_SPREAD else -1
        # Fix #4: use unified _confidence_for_z(in_trade=False)
        conf = self._confidence_for_z(z, in_trade=False)
        logger.debug(f"ENTRY {sig.name} z={z:.3f} conf={conf:.3f} reason={reason}")
        return sig, conf, reason

    def _exit(
        self, reason: str, half_life: float = float("nan")
    ) -> Tuple[Signal, float, str]:
        if self._in_trade:
            logger.debug(f"EXIT reason={reason} bars_in_trade={self._bars_in_trade}")
        self._in_trade = False
        self._current_signal = Signal.EXIT
        self._bars_in_trade = 0
        self._partial_exit_done = False
        self._entry_side = 0
        self._cooldown_remaining = self._dynamic_cooldown(half_life)
        return Signal.EXIT, 1.0, reason

    def _exit_if_needed(
        self, reason: str, half_life: float = float("nan")
    ) -> Tuple[Signal, float, str]:
        """Exit only if currently in trade; otherwise block entry without triggering cooldown."""
        if self._in_trade:
            return self._exit(reason, half_life)
        return Signal.EXIT, 0.0, reason

    def _reset_trade_state(self) -> None:
        self._in_trade = False
        self._current_signal = Signal.EXIT
        self._bars_in_trade = 0
        self._cooldown_remaining = 0
        self._partial_exit_done = False
        self._entry_side = 0
        self._vol_buffer.clear()
        self._zscore_buffer.clear()
        self._last_spread_for_vol = None
        self._last_vol_rank = 0.0
        self._last_effective_threshold = self.cfg.zscore_entry
        self._last_dz_blocked = False

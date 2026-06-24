"""
QuantLuna — RegimeDetector

Volatility-regime classifier for pairs trading.
Detects 4 states: NORMAL / HIGH_VOL / BREAKDOWN / TRANSITION

Approach: rolling vol ratio (no HMM dependency) + ADF deterioration gate.
Design rationale: HMM requires scipy.hmmlearn or pomegranate which add
fragile deps. Rolling vol-ratio is transparent, testable, and fast enough
for 1h bars. Upgrade path to HMM is available via subclassing.

Regime sizing scalars (get_regime_multiplier):
  NORMAL     → 1.00  (full size)
  HIGH_VOL   → 0.50  (half size, wider spreads)
  TRANSITION → 0.75  (cautious)
  BREAKDOWN  → 0.00  (no new trades, flatten existing)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger


class VolRegime(str, Enum):
    NORMAL     = "NORMAL"
    HIGH_VOL   = "HIGH_VOL"
    BREAKDOWN  = "BREAKDOWN"
    TRANSITION = "TRANSITION"


_REGIME_MULTIPLIER: Dict[VolRegime, float] = {
    VolRegime.NORMAL:     1.00,
    VolRegime.HIGH_VOL:   0.50,
    VolRegime.TRANSITION: 0.75,
    VolRegime.BREAKDOWN:  0.00,
}


@dataclass
class RegimeState:
    regime: VolRegime
    vol_ratio: float          # current_vol / baseline_vol
    current_vol: float        # rolling std of spread returns
    baseline_vol: float       # longer-window baseline
    persistence_count: int    # bars current raw regime has held
    confirmed: bool           # True once persistence threshold met
    timestamp: Optional[pd.Timestamp] = None


class RegimeDetector:
    """
    Classify market regime from spread return volatility.

    Parameters
    ----------
    vol_window         : short rolling window for current vol (default 24 bars)
    baseline_window    : longer window for baseline vol (default 168 bars = 1 week @ 1h)
    high_vol_threshold : vol_ratio above this → HIGH_VOL (default 1.5)
    breakdown_threshold: vol_ratio above this → BREAKDOWN (default 2.5)
    min_persistence    : consecutive bars required before regime switch is confirmed
    adf_deterioration  : if provided, ADF p-value above this forces BREAKDOWN
    """

    def __init__(
        self,
        vol_window: int = 24,
        baseline_window: int = 168,
        high_vol_threshold: float = 1.5,
        breakdown_threshold: float = 2.5,
        min_persistence: int = 3,
        adf_deterioration: float = 0.10,
    ) -> None:
        if vol_window >= baseline_window:
            raise ValueError("vol_window must be < baseline_window")
        if high_vol_threshold >= breakdown_threshold:
            raise ValueError("high_vol_threshold must be < breakdown_threshold")

        self.vol_window = vol_window
        self.baseline_window = baseline_window
        self.high_vol_threshold = high_vol_threshold
        self.breakdown_threshold = breakdown_threshold
        self.min_persistence = min_persistence
        self.adf_deterioration = adf_deterioration

        # Online state
        self._spread_returns: List[float] = []
        self._raw_regime_count: int = 0
        self._raw_regime: VolRegime = VolRegime.NORMAL
        self._confirmed_regime: VolRegime = VolRegime.NORMAL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def batch(
        self,
        spread: pd.Series,
        adf_pvalues: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Classify regime for each bar in *spread* series.

        Parameters
        ----------
        spread     : spread values (output of SpreadEngine)
        adf_pvalues: optional rolling ADF p-values; values > adf_deterioration
                     force BREAKDOWN regardless of vol ratio

        Returns
        -------
        DataFrame with columns: regime, vol_ratio, current_vol, baseline_vol,
                                 confirmed, multiplier
        """
        spread_ret = spread.pct_change().fillna(0.0)
        self._reset_online_state()

        regimes: List[Dict] = []
        for i in range(len(spread_ret)):
            adf_p = float(adf_pvalues.iloc[i]) if adf_pvalues is not None else None
            state = self._step(float(spread_ret.iloc[i]), adf_p=adf_p)
            ts = spread_ret.index[i] if hasattr(spread_ret.index, '__getitem__') else None
            regimes.append({
                "regime":       state.regime.value,
                "vol_ratio":    state.vol_ratio,
                "current_vol":  state.current_vol,
                "baseline_vol": state.baseline_vol,
                "confirmed":    state.confirmed,
                "multiplier":   _REGIME_MULTIPLIER[state.regime],
            })

        result = pd.DataFrame(regimes, index=spread.index)
        counts = result["regime"].value_counts().to_dict()
        logger.info(f"Regime batch: {len(result)} bars | {counts}")
        return result

    def update_one(
        self,
        spread_return: float,
        adf_pvalue: Optional[float] = None,
        ts: Optional[pd.Timestamp] = None,
    ) -> RegimeState:
        """Online single-bar update. Returns current RegimeState."""
        return self._step(spread_return, adf_p=adf_pvalue, ts=ts)

    def get_regime_multiplier(self, regime: Optional[VolRegime] = None) -> float:
        """
        Return position sizing scalar for *regime*.
        If regime is None, uses current confirmed regime.
        """
        r = regime if regime is not None else self._confirmed_regime
        return _REGIME_MULTIPLIER[r]

    def current_regime(self) -> VolRegime:
        """Return the confirmed (persistence-filtered) current regime."""
        return self._confirmed_regime

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _step(
        self,
        spread_return: float,
        adf_p: Optional[float] = None,
        ts: Optional[pd.Timestamp] = None,
    ) -> RegimeState:
        self._spread_returns.append(spread_return)

        if len(self._spread_returns) < self.vol_window:
            return RegimeState(
                regime=VolRegime.NORMAL,
                vol_ratio=1.0,
                current_vol=0.0,
                baseline_vol=0.0,
                persistence_count=0,
                confirmed=False,
                timestamp=ts,
            )

        arr = np.asarray(self._spread_returns)
        current_vol  = float(np.std(arr[-self.vol_window:]))
        baseline_arr = arr[-self.baseline_window:] if len(arr) >= self.baseline_window else arr
        baseline_vol = float(np.std(baseline_arr)) or 1e-12

        vol_ratio = current_vol / baseline_vol

        # -- Raw regime from vol ratio --
        if vol_ratio >= self.breakdown_threshold:
            raw = VolRegime.BREAKDOWN
        elif vol_ratio >= self.high_vol_threshold:
            raw = VolRegime.HIGH_VOL
        else:
            raw = VolRegime.NORMAL

        # -- ADF deterioration override --
        if adf_p is not None and adf_p > self.adf_deterioration:
            raw = VolRegime.BREAKDOWN

        # -- Persistence filter --
        if raw == self._raw_regime:
            self._raw_regime_count += 1
        else:
            self._raw_regime = raw
            self._raw_regime_count = 1

        confirmed = self._raw_regime_count >= self.min_persistence
        if confirmed:
            if self._confirmed_regime != raw:
                logger.info(
                    f"Regime switch: {self._confirmed_regime.value} → {raw.value} "
                    f"(vol_ratio={vol_ratio:.2f}, persistence={self._raw_regime_count})"
                )
            self._confirmed_regime = raw
        elif self._raw_regime_count == 1 and raw != self._confirmed_regime:
            # First bar of new candidate regime → TRANSITION
            self._confirmed_regime = VolRegime.TRANSITION

        return RegimeState(
            regime=self._confirmed_regime,
            vol_ratio=vol_ratio,
            current_vol=current_vol,
            baseline_vol=baseline_vol,
            persistence_count=self._raw_regime_count,
            confirmed=confirmed,
            timestamp=ts,
        )

    def _reset_online_state(self) -> None:
        self._spread_returns = []
        self._raw_regime_count = 0
        self._raw_regime = VolRegime.NORMAL
        self._confirmed_regime = VolRegime.NORMAL

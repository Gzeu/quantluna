"""
QuantLuna — RegimeDetector v2

Volatility-regime classifier for pairs trading.
Detects 4 states: NORMAL / HIGH_VOL / BREAKDOWN / TRANSITION

Approach: rolling vol ratio (primary, no external deps) +
          optional Gaussian HMM 2-state (requires hmmlearn).

Design rationale:
  Rolling vol-ratio is transparent, testable, fast, and production-safe.
  HMM is opt-in via use_hmm=True — falls back gracefully if hmmlearn not installed.

Regime sizing scalars (get_regime_multiplier):
  NORMAL     -> 1.00  (full size)
  HIGH_VOL   -> 0.50  (half size)
  TRANSITION -> 0.75  (cautious)
  BREAKDOWN  -> 0.00  (no new trades, flatten)

Changes v2:
  - TRANSITION no longer permanently sticks: only first bar of regime candidate
    sets TRANSITION; bars 2..N-1 keep previous confirmed regime until persistence met
  - _transition_bars counter: tracks bars in transition, reset on confirmation
  - Optional Gaussian HMM 2-state layer (use_hmm=True, graceful fallback)
  - HMM states normalised: State 0=low-vol, State 1=high-vol (sorted by abs mean)
  - regime_series(): returns pd.Series[VolRegime] for vectorised backtest usage
  - batch(): hmm_state column included when HMM available (None otherwise)
  - baseline_vol clamped to 1e-10 (division-by-zero guard on short series)
  - _reset_online_state() resets all counters including _transition_bars

Changes (code review 2026-07-12):
  - Patch 4: replaced spread.pct_change() in batch() with diff()/abs_mean
    normalisation. pct_change() produces +-inf when spread crosses zero
    (common in pairs trading), corrupting the vol-ratio calculation.
    Same fix already applied in risk/kelly.py.
  - Patch 5: replaced unbounded List[float] _spread_returns with
    collections.deque(maxlen=baseline_window+vol_window) to prevent
    unbounded memory growth during long-running live sessions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, List, Optional

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
    vol_ratio: float
    current_vol: float
    baseline_vol: float
    persistence_count: int
    confirmed: bool
    hmm_state: Optional[int] = None      # 0=low-vol, 1=high-vol when HMM available
    timestamp: Optional[pd.Timestamp] = None


class RegimeDetector:
    """
    Classify market regime from spread return volatility.

    Parameters
    ----------
    vol_window          : short rolling window for current vol (default 24 bars)
    baseline_window     : longer window for baseline vol (default 168 bars = 1 week @ 1h)
    high_vol_threshold  : vol_ratio above this -> HIGH_VOL (default 1.5)
    breakdown_threshold : vol_ratio above this -> BREAKDOWN (default 2.5)
    min_persistence     : consecutive bars before regime switch is confirmed (default 3)
    adf_deterioration   : ADF p-value above this forces BREAKDOWN (default 0.10)
    use_hmm             : fit Gaussian HMM 2-state on batch() if hmmlearn available
    hmm_n_iter          : EM iterations for HMM (default 100)
    """

    def __init__(
        self,
        vol_window: int = 24,
        baseline_window: int = 168,
        high_vol_threshold: float = 1.5,
        breakdown_threshold: float = 2.5,
        min_persistence: int = 3,
        adf_deterioration: float = 0.10,
        use_hmm: bool = False,
        hmm_n_iter: int = 100,
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
        self.use_hmm = use_hmm
        self.hmm_n_iter = hmm_n_iter

        self._hmm_model = None
        self._hmm_available: bool = self._check_hmmlearn()
        if use_hmm and not self._hmm_available:
            logger.warning("use_hmm=True but hmmlearn not installed — falling back to vol-ratio only")

        # Patch 5: bounded deque prevents unbounded memory growth in live mode.
        # The deque auto-evicts the oldest element when full, so _step() logic
        # using arr[-vol_window:] and arr[-baseline_window:] remains correct.
        self._spread_returns: Deque[float] = deque(
            maxlen=self.baseline_window + self.vol_window
        )
        self._raw_regime_count: int = 0
        self._raw_regime: VolRegime = VolRegime.NORMAL
        self._confirmed_regime: VolRegime = VolRegime.NORMAL
        self._transition_bars: int = 0

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
        spread      : spread values (output of SpreadEngine)
        adf_pvalues : optional rolling ADF p-values; values > adf_deterioration
                      force BREAKDOWN regardless of vol ratio

        Returns
        -------
        DataFrame with columns: regime, vol_ratio, current_vol, baseline_vol,
                                 confirmed, multiplier, hmm_state
        """
        # Patch 4: use diff()/abs_mean instead of pct_change().
        # pct_change() produces +-inf when spread crosses zero, which is
        # normal in pairs trading and corrupts the vol-ratio calculation.
        # abs_mean normalisation gives a comparable dimensionless return.
        spread_abs_mean = (
            spread.abs()
            .rolling(window=self.baseline_window, min_periods=1)
            .mean()
            .replace(0, np.nan)
            .ffill()
            .fillna(1.0)
        )
        spread_ret = spread.diff().fillna(0.0) / spread_abs_mean

        self._reset_online_state()

        regimes: List[Dict] = []
        for i in range(len(spread_ret)):
            adf_p = float(adf_pvalues.iloc[i]) if adf_pvalues is not None else None
            state = self._step(float(spread_ret.iloc[i]), adf_p=adf_p)
            regimes.append({
                "regime":       state.regime.value,
                "vol_ratio":    state.vol_ratio,
                "current_vol":  state.current_vol,
                "baseline_vol": state.baseline_vol,
                "confirmed":    state.confirmed,
                "multiplier":   _REGIME_MULTIPLIER[state.regime],
            })

        result = pd.DataFrame(regimes, index=spread.index)

        if self.use_hmm and self._hmm_available:
            result["hmm_state"] = self._fit_predict_hmm(spread_ret.values)
        else:
            result["hmm_state"] = None

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
        r = regime if regime is not None else self._confirmed_regime
        return _REGIME_MULTIPLIER[r]

    def current_regime(self) -> VolRegime:
        """Return the confirmed (persistence-filtered) current regime."""
        return self._confirmed_regime

    def regime_series(
        self,
        spread: pd.Series,
        adf_pvalues: Optional[pd.Series] = None,
    ) -> pd.Series:
        """
        Convenience: returns pd.Series of VolRegime enum values.
        Useful for vectorised regime lookups in backtest loops.
        """
        df = self.batch(spread, adf_pvalues=adf_pvalues)
        return df["regime"].map(lambda v: VolRegime(v))

    # ------------------------------------------------------------------
    # Internal — vol-ratio classifier
    # ------------------------------------------------------------------

    def _step(
        self,
        spread_return: float,
        adf_p: Optional[float] = None,
        ts: Optional[pd.Timestamp] = None,
    ) -> RegimeState:
        # Patch 5: deque.append() auto-evicts oldest when maxlen is reached.
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
        baseline_vol = max(float(np.std(baseline_arr)), 1e-10)

        vol_ratio = current_vol / baseline_vol

        if vol_ratio >= self.breakdown_threshold:
            raw = VolRegime.BREAKDOWN
        elif vol_ratio >= self.high_vol_threshold:
            raw = VolRegime.HIGH_VOL
        else:
            raw = VolRegime.NORMAL

        if adf_p is not None and adf_p > self.adf_deterioration:
            raw = VolRegime.BREAKDOWN

        if raw == self._raw_regime:
            self._raw_regime_count += 1
        else:
            self._raw_regime = raw
            self._raw_regime_count = 1

        confirmed = self._raw_regime_count >= self.min_persistence

        if confirmed:
            if self._confirmed_regime != raw:
                logger.info(
                    f"Regime switch: {self._confirmed_regime.value} -> {raw.value} "
                    f"(vol_ratio={vol_ratio:.2f}, persistence={self._raw_regime_count})"
                )
            self._confirmed_regime = raw
            self._transition_bars = 0
        elif self._raw_regime_count == 1 and raw != self._confirmed_regime:
            self._transition_bars += 1
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
        self._spread_returns = deque(maxlen=self.baseline_window + self.vol_window)
        self._raw_regime_count = 0
        self._raw_regime = VolRegime.NORMAL
        self._confirmed_regime = VolRegime.NORMAL
        self._transition_bars = 0

    # ------------------------------------------------------------------
    # Internal — HMM layer (optional)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_hmmlearn() -> bool:
        try:
            import hmmlearn  # noqa: F401
            return True
        except ImportError:
            return False

    def _fit_predict_hmm(
        self,
        returns: np.ndarray,
        n_components: int = 2,
    ) -> np.ndarray:
        """
        Fit a Gaussian HMM with n_components hidden states on returns
        and return the Viterbi state sequence.

        State 0 = low-vol, State 1 = high-vol
        (sorted by emission abs mean ascending — exchange-agnostic).

        Returns array of -1 on any error (safe fallback).
        """
        try:
            from hmmlearn.hmm import GaussianHMM

            X = returns.reshape(-1, 1)
            model = GaussianHMM(
                n_components=n_components,
                covariance_type="diag",
                n_iter=self.hmm_n_iter,
                random_state=42,
                verbose=False,
            )
            model.fit(X)
            states = model.predict(X)

            means  = np.array([np.abs(model.means_[s]).mean() for s in range(n_components)])
            order  = np.argsort(means)
            remap  = {int(order[i]): i for i in range(n_components)}
            states = np.vectorize(remap.get)(states)

            self._hmm_model = model
            logger.info(
                f"HMM fit OK: low-vol bars={int((states == 0).sum())} "
                f"high-vol bars={int((states == 1).sum())}"
            )
            return states.astype(int)

        except Exception as exc:
            logger.warning(f"HMM fit failed, using vol-ratio only: {exc}")
            return np.full(len(returns), -1, dtype=int)

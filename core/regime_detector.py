"""
QuantLuna — TrendRegimeDetector
Sprint 23

Classifies spread/price series into AutoSelector-compatible regimes:
  ranging   — mean-reverting, low autocorrelation, Hurst < 0.5
  trending  — directional, positive autocorr, Hurst > 0.5, moderate ADX
  breakout  — high ADX + high vol-rank (trending but explosive)
  unknown   — insufficient data (warm-up period)

Design principles:
  • Zero extra deps (numpy + pandas only)
  • All indicators are rolling — no lookahead
  • Persistence filter: regime must hold min_persistence bars before confirming
  • Hysteresis: current regime gets a bonus to prevent rapid switching
  • Fully compatible with MarketContext.regime field

Usage (batch):
    from core.regime_detector import TrendRegimeDetector
    det = TrendRegimeDetector(window=24)
    df["regime"] = det.classify_series(df["spread"])

Usage (online, bar-by-bar):
    det = TrendRegimeDetector(window=24)
    for bar in bars:
        regime = det.update(bar["spread"], bar["high"], bar["low"])

Integration with AutoSelectorRunner:
    runner = AutoSelectorRunner(cfg)
    result = runner.run(y=prices_y, x=prices_x)  # regime auto-detected
    # result["regime_distribution"] -> {"ranging": 1200, "trending": 800, ...}
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Regime labels (must match MarketContext.regime expected values)
REGIME_RANGING   = "ranging"
REGIME_TRENDING  = "trending"
REGIME_BREAKOUT  = "breakout"
REGIME_UNKNOWN   = "unknown"

REGIME_LABELS = [REGIME_RANGING, REGIME_TRENDING, REGIME_BREAKOUT, REGIME_UNKNOWN]


class TrendRegimeDetector:
    """
    Rolling trend-regime classifier for spread/price series.

    Parameters
    ----------
    window              : primary rolling window (default 24 bars)
    adx_window          : ADX smoothing window (default 14)
    autocorr_lag        : lag for autocorrelation (default 1)
    hurst_window        : window for Hurst exponent estimate (default 50)
    trending_adx        : ADX above this → trending candidate (default 20)
    breakout_adx        : ADX above this + high vol → breakout (default 30)
    breakout_vol_rank   : vol_rank above this required for breakout (default 0.70)
    ranging_autocorr    : autocorr below this → ranging candidate (default 0.05)
    min_persistence     : bars before regime switch confirmed (default 3)
    hysteresis_bonus    : score bonus for current regime (default 0.08)
    """

    def __init__(
        self,
        window: int = 24,
        adx_window: int = 14,
        autocorr_lag: int = 1,
        hurst_window: int = 50,
        trending_adx: float = 20.0,
        breakout_adx: float = 30.0,
        breakout_vol_rank: float = 0.70,
        ranging_autocorr: float = 0.05,
        min_persistence: int = 3,
        hysteresis_bonus: float = 0.08,
    ) -> None:
        self.window           = window
        self.adx_window       = adx_window
        self.autocorr_lag     = autocorr_lag
        self.hurst_window     = hurst_window
        self.trending_adx     = trending_adx
        self.breakout_adx     = breakout_adx
        self.breakout_vol_rank = breakout_vol_rank
        self.ranging_autocorr = ranging_autocorr
        self.min_persistence  = min_persistence
        self.hysteresis_bonus = hysteresis_bonus

        # Online state
        self._prices:   List[float] = []
        self._highs:    List[float] = []
        self._lows:     List[float] = []
        self._confirmed: str = REGIME_UNKNOWN
        self._candidate: str = REGIME_UNKNOWN
        self._candidate_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_series(
        self,
        prices: pd.Series,
        highs: Optional[pd.Series] = None,
        lows: Optional[pd.Series] = None,
    ) -> pd.Series:
        """
        Batch classify. Returns pd.Series of regime strings aligned to prices.index.
        If highs/lows not provided, approximated from prices ± rolling std.
        """
        self._reset()
        if highs is None:
            roll_std = prices.rolling(self.adx_window, min_periods=1).std().fillna(0)
            highs = prices + roll_std * 0.5
            lows  = prices - roll_std * 0.5

        regimes: List[str] = []
        for i in range(len(prices)):
            r = self.update(
                price=float(prices.iloc[i]),
                high=float(highs.iloc[i]),
                low=float(lows.iloc[i]),
            )
            regimes.append(r)
        return pd.Series(regimes, index=prices.index, name="regime")

    def classify_df(
        self,
        df: pd.DataFrame,
        price_col: str = "spread",
        high_col: Optional[str] = "high",
        low_col: Optional[str] = "low",
    ) -> pd.Series:
        """
        Classify from DataFrame. Uses high/low columns if available.
        Returns pd.Series of regime strings.
        """
        highs = df[high_col] if high_col and high_col in df.columns else None
        lows  = df[low_col]  if low_col  and low_col  in df.columns else None
        return self.classify_series(df[price_col], highs=highs, lows=lows)

    def update(
        self,
        price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
    ) -> str:
        """
        Online single-bar update. Returns confirmed regime string.
        """
        self._prices.append(price)
        self._highs.append(high if high is not None else price)
        self._lows.append(low   if low  is not None else price)

        if len(self._prices) < max(self.window, self.adx_window + 1):
            return REGIME_UNKNOWN

        raw = self._classify_raw()
        self._apply_persistence(raw)
        return self._confirmed

    def current(self) -> str:
        """Return current confirmed regime."""
        return self._confirmed

    def reset(self) -> None:
        self._reset()

    def regime_distribution(self, series: pd.Series) -> Dict[str, int]:
        """Convenience: classify series and return counts per regime."""
        classified = self.classify_series(series)
        return {r: int((classified == r).sum()) for r in REGIME_LABELS}

    # ------------------------------------------------------------------
    # Internal — indicators
    # ------------------------------------------------------------------

    def _adx(self) -> float:
        """
        Approximate ADX from internal high/low/close buffers.
        Uses Wilder smoothing over adx_window bars.
        Returns 0.0 if insufficient data.
        """
        n = self.adx_window
        if len(self._prices) < n + 1:
            return 0.0

        highs  = np.asarray(self._highs[-(n + 1):])
        lows   = np.asarray(self._lows[-(n + 1):])
        closes = np.asarray(self._prices[-(n + 1):])

        tr_arr, pdm_arr, mdm_arr = [], [], []
        for i in range(1, len(closes)):
            h, l, pc = highs[i], lows[i], closes[i - 1]
            tr  = max(h - l, abs(h - pc), abs(l - pc))
            pdm = max(h - highs[i - 1], 0.0) if (h - highs[i - 1]) > (lows[i - 1] - l) else 0.0
            mdm = max(lows[i - 1] - l, 0.0) if (lows[i - 1] - l) > (h - highs[i - 1]) else 0.0
            tr_arr.append(tr); pdm_arr.append(pdm); mdm_arr.append(mdm)

        tr_s  = float(np.sum(tr_arr))  or 1e-10
        pdi   = 100 * np.sum(pdm_arr) / tr_s
        mdi   = 100 * np.sum(mdm_arr) / tr_s
        dx    = 100 * abs(pdi - mdi) / (pdi + mdi + 1e-10)
        return float(dx)

    def _autocorr(self) -> float:
        """Rolling autocorrelation at lag autocorr_lag over last window bars."""
        n = self.window
        if len(self._prices) < n + self.autocorr_lag:
            return 0.0
        arr = np.asarray(self._prices[-n:])
        ret = np.diff(arr)
        if len(ret) <= self.autocorr_lag:
            return 0.0
        x, y = ret[:-self.autocorr_lag], ret[self.autocorr_lag:]
        if np.std(x) < 1e-10 or np.std(y) < 1e-10:
            return 0.0
        return float(np.corrcoef(x, y)[0, 1])

    def _hurst(self) -> float:
        """
        R/S Hurst exponent estimate.
        H < 0.5 → mean-reverting (ranging)
        H ~ 0.5 → random walk
        H > 0.5 → trending
        Returns 0.5 if insufficient data.
        """
        n = min(self.hurst_window, len(self._prices))
        if n < 20:
            return 0.5
        arr = np.asarray(self._prices[-n:])
        ret = np.diff(arr)
        mean_ret = np.mean(ret)
        deviations = np.cumsum(ret - mean_ret)
        r = np.max(deviations) - np.min(deviations)
        s = np.std(ret)
        if s < 1e-10 or r < 1e-10:
            return 0.5
        return float(np.clip(np.log(r / s) / np.log(n), 0.01, 0.99))

    def _vol_rank(self) -> float:
        """Percentile rank of current window vol vs baseline (last 3x window)."""
        n = self.window
        if len(self._prices) < n + 1:
            return 0.5
        arr  = np.asarray(self._prices)
        rets = np.diff(arr)
        curr_vol = float(np.std(rets[-n:]))
        base_rets = rets[-3 * n:] if len(rets) >= 3 * n else rets
        # rolling vol over base window in steps of n//2
        step = max(n // 2, 1)
        vols = [
            float(np.std(base_rets[i:i + n]))
            for i in range(0, max(len(base_rets) - n, 1), step)
        ]
        if not vols:
            return 0.5
        return float(np.mean(np.asarray(vols) <= curr_vol))

    # ------------------------------------------------------------------
    # Internal — regime scoring + persistence
    # ------------------------------------------------------------------

    def _classify_raw(self) -> str:
        """
        Score each regime and return the highest-scoring one.
        Applies hysteresis bonus to current confirmed regime.
        """
        adx      = self._adx()
        autocorr = self._autocorr()
        hurst    = self._hurst()
        vol_rank = self._vol_rank()

        scores: Dict[str, float] = {
            REGIME_RANGING:  0.0,
            REGIME_TRENDING: 0.0,
            REGIME_BREAKOUT: 0.0,
        }

        # Ranging: low ADX + negative/near-zero autocorr + low Hurst
        scores[REGIME_RANGING] += max(0.0, (self.trending_adx - adx) / self.trending_adx)
        scores[REGIME_RANGING] += max(0.0, self.ranging_autocorr - autocorr)
        scores[REGIME_RANGING] += max(0.0, 0.5 - hurst) * 2.0

        # Trending: moderate ADX + positive autocorr + high Hurst
        scores[REGIME_TRENDING] += min(1.0, adx / self.breakout_adx)
        scores[REGIME_TRENDING] += max(0.0, autocorr) * 2.0
        scores[REGIME_TRENDING] += max(0.0, hurst - 0.5) * 2.0

        # Breakout: high ADX + high vol_rank
        if adx >= self.breakout_adx and vol_rank >= self.breakout_vol_rank:
            scores[REGIME_BREAKOUT] += 1.5
        scores[REGIME_BREAKOUT] += max(0.0, adx - self.breakout_adx) / self.breakout_adx
        scores[REGIME_BREAKOUT] += max(0.0, vol_rank - self.breakout_vol_rank)

        # Hysteresis: bonus for current regime
        if self._confirmed in scores:
            scores[self._confirmed] += self.hysteresis_bonus

        return max(scores, key=lambda k: scores[k])

    def _apply_persistence(self, raw: str) -> None:
        if raw == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = raw
            self._candidate_count = 1

        if self._candidate_count >= self.min_persistence:
            self._confirmed = self._candidate

    def _reset(self) -> None:
        self._prices.clear()
        self._highs.clear()
        self._lows.clear()
        self._confirmed  = REGIME_UNKNOWN
        self._candidate  = REGIME_UNKNOWN
        self._candidate_count = 0

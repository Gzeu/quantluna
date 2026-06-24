"""
QuantLuna — Spread Engine

Computes the residual spread from Kalman hedge ratio and generates
z-score signals for entry/exit.

Fixes applied:
  - update_one() now returns half_life_hours, spread_mean, spread_std
    so that time_stop in SignalGenerator works correctly in live mode.
  - _spreads buffer is capped to 2x zscore_window to prevent unbounded growth.
  - half-life is estimated from the AR(1) autocorrelation of recent spreads.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional
from loguru import logger

from .kalman_filter import KalmanHedgeRatio

# Minimum samples needed for a meaningful half-life estimate.
_MIN_HALF_LIFE_SAMPLES = 20


def _estimate_half_life(spreads: np.ndarray, bar_freq_hours: float = 1.0) -> Optional[float]:
    """
    Estimate half-life of mean reversion from an AR(1) fit on spread lags.

    half_life_bars = -ln(2) / ln(rho)   where rho = AR(1) coefficient
    half_life_hours = half_life_bars * bar_freq_hours

    Returns None if the spread is non-stationary (rho >= 1) or if
    there are insufficient samples.
    """
    if len(spreads) < _MIN_HALF_LIFE_SAMPLES:
        return None
    # Demean
    s = spreads - spreads.mean()
    y_lag = s[:-1]
    y_cur = s[1:]
    if y_lag.std() < 1e-12:
        return None
    rho = float(np.corrcoef(y_lag, y_cur)[0, 1])
    # Non-mean-reverting or insufficient correlation
    if rho >= 1.0 or rho <= 0.0:
        return None
    half_life_bars = -np.log(2) / np.log(rho)
    return float(half_life_bars * bar_freq_hours)


class SpreadEngine:
    """
    Combines Kalman Filter output with spread normalisation.

    spread_t = y_t - (beta_t * x_t + alpha_t)
    z_score_t = (spread_t - mu_rolling) / sigma_rolling
    """

    def __init__(
        self,
        kalman: KalmanHedgeRatio,
        zscore_window: int = 100,
        min_warm_periods: int = 30,
        bar_freq_hours: float = 1.0,
    ):
        self.kalman = kalman
        self.zscore_window = zscore_window
        self.min_warm_periods = min_warm_periods
        self.bar_freq_hours = bar_freq_hours
        # FIX: cap buffer at 2x window to avoid unbounded growth
        self._spreads: list = []
        self._spread_buffer_max = zscore_window * 2

    def fit(self, y: pd.Series, x: pd.Series) -> pd.DataFrame:
        """Full batch fit. Returns spread + z-score DataFrame."""
        kf_df = self.kalman.fit(y, x)

        # The Kalman innovation IS the spread
        spread = kf_df["spread"]

        # Rolling z-score
        roll_mean = spread.rolling(self.zscore_window, min_periods=self.min_warm_periods).mean()
        roll_std  = spread.rolling(self.zscore_window, min_periods=self.min_warm_periods).std()
        zscore = (spread - roll_mean) / roll_std.replace(0, np.nan)

        # Half-life column: estimated over a rolling window of zscore_window bars
        half_life_bars = spread.rolling(self.zscore_window, min_periods=_MIN_HALF_LIFE_SAMPLES).apply(
            lambda s: _estimate_half_life(s.values, bar_freq_hours=self.bar_freq_hours) or np.nan,
            raw=True,
        )

        result = kf_df.copy()
        result["zscore"] = zscore
        result["spread_mean"] = roll_mean
        result["spread_std"] = roll_std
        result["half_life_hours"] = half_life_bars

        logger.info(
            f"SpreadEngine fit: z-score range [{zscore.min():.2f}, {zscore.max():.2f}], "
            f"spread_std={roll_std.iloc[-1]:.6f}, "
            f"half_life_hours={half_life_bars.iloc[-1]:.1f} "
            f"(window={self.zscore_window})"
        )
        return result

    def update_one(self, y: float, x: float, ts=None) -> dict:
        """
        Incremental update for live trading.

        Returns a dict with all fields required by SignalGenerator, including:
        - half_life_hours : float | None   (None if insufficient data)
        - spread_mean     : float
        - spread_std      : float
        These were previously missing, causing time_stop to never fire in live.
        """
        state = self.kalman.update(y, x, ts=ts)
        self._spreads.append(state.innovation)

        # FIX: purge old values to keep buffer bounded
        if len(self._spreads) > self._spread_buffer_max:
            self._spreads = self._spreads[-self.zscore_window:]

        spread_arr = np.array(self._spreads[-self.zscore_window:])
        mu = float(spread_arr.mean())
        sigma = float(spread_arr.std())
        zscore = (state.innovation - mu) / sigma if sigma > 1e-10 else 0.0

        half_life = _estimate_half_life(spread_arr, bar_freq_hours=self.bar_freq_hours)

        return {
            "beta": state.beta,
            "alpha": state.alpha,
            "spread": state.innovation,
            "zscore": zscore,
            "spread_mean": mu,
            "spread_std": sigma,
            "half_life_hours": half_life,
            "P_beta": state.P_beta,
            "kalman_gain": state.kalman_gain_beta,
            "uncertainty": self.kalman.uncertainty,
            "is_warm": self.kalman.is_warm,
        }

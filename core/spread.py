"""
QuantLuna — Spread Engine

Computes the residual spread from Kalman hedge ratio and generates
z-score signals for entry/exit.
"""
import numpy as np
import pandas as pd
from typing import Optional
from loguru import logger

from .kalman_filter import KalmanHedgeRatio


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
    ):
        self.kalman = kalman
        self.zscore_window = zscore_window
        self.min_warm_periods = min_warm_periods
        self._spreads: list = []

    def fit(self, y: pd.Series, x: pd.Series) -> pd.DataFrame:
        """Full batch fit. Returns spread + z-score DataFrame."""
        kf_df = self.kalman.fit(y, x)

        # The Kalman innovation IS the spread
        spread = kf_df["spread"]

        # Rolling z-score
        roll_mean = spread.rolling(self.zscore_window, min_periods=self.min_warm_periods).mean()
        roll_std  = spread.rolling(self.zscore_window, min_periods=self.min_warm_periods).std()
        zscore = (spread - roll_mean) / roll_std.replace(0, np.nan)

        result = kf_df.copy()
        result["zscore"] = zscore
        result["spread_mean"] = roll_mean
        result["spread_std"] = roll_std

        logger.info(
            f"SpreadEngine fit: z-score range [{zscore.min():.2f}, {zscore.max():.2f}], "
            f"spread_std={roll_std.iloc[-1]:.6f}"
        )
        return result

    def update_one(self, y: float, x: float, ts=None) -> dict:
        """Incremental update for live trading."""
        state = self.kalman.update(y, x, ts=ts)
        self._spreads.append(state.innovation)

        spread_arr = np.array(self._spreads[-self.zscore_window:])
        mu = spread_arr.mean()
        sigma = spread_arr.std()
        zscore = (state.innovation - mu) / sigma if sigma > 1e-10 else 0.0

        return {
            "beta": state.beta,
            "alpha": state.alpha,
            "spread": state.innovation,
            "zscore": zscore,
            "P_beta": state.P_beta,
            "kalman_gain": state.kalman_gain_beta,
            "uncertainty": self.kalman.uncertainty,
            "is_warm": self.kalman.is_warm,
        }

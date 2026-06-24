"""
QuantLuna — Regime Detector

Detects market regimes that may invalidate the cointegration relationship:
  - Trending regime (ADX > 25)
  - High volatility (spread vol spike)
  - Correlation breakdown
  - Funding rate anomaly
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from loguru import logger


class Regime(Enum):
    MEAN_REVERTING = "mean_reverting"   # Ideal for pairs trading
    TRENDING       = "trending"          # Avoid / reduce size
    HIGH_VOL       = "high_volatility"   # Reduce size
    BREAKDOWN      = "breakdown"         # Stop trading pair
    UNKNOWN        = "unknown"


@dataclass
class RegimeState:
    regime: Regime
    correlation_60: float
    spread_vol_ratio: float   # Current vol / rolling vol baseline
    hurst: Optional[float]
    funding_anomaly: bool
    tradeable: bool
    reason: str


class RegimeDetector:
    """
    Monitors ongoing health of a pair relationship.

    Parameters
    ----------
    corr_min : float
        Minimum rolling correlation to consider pair healthy.
    vol_ratio_max : float
        Max spread vol vs baseline before flagging high-vol regime.
    """

    def __init__(
        self,
        corr_min: float = 0.60,
        vol_ratio_max: float = 2.5,
        vol_window: int = 20,
        corr_window: int = 60,
    ):
        self.corr_min = corr_min
        self.vol_ratio_max = vol_ratio_max
        self.vol_window = vol_window
        self.corr_window = corr_window

    def detect(self, y: pd.Series, x: pd.Series, spread: pd.Series,
               funding_rate: Optional[pd.Series] = None) -> pd.DataFrame:
        """
        Batch regime detection over full history.
        Returns DataFrame with regime labels per bar.
        """
        results = pd.DataFrame(index=spread.index)

        # Rolling correlation
        results["corr"] = y.rolling(self.corr_window).corr(x)

        # Spread volatility ratio
        spread_vol = spread.rolling(self.vol_window).std()
        baseline_vol = spread.rolling(self.vol_window * 5).std()
        results["vol_ratio"] = spread_vol / baseline_vol.replace(0, np.nan)

        # Hurst on rolling window
        results["hurst"] = self._rolling_hurst(spread)

        # Funding anomaly (if provided)
        if funding_rate is not None:
            results["funding_anomaly"] = abs(funding_rate) > 0.001  # > 0.1% per 8h
        else:
            results["funding_anomaly"] = False

        # Classify regime
        results["regime"] = Regime.UNKNOWN.value
        results["tradeable"] = False

        for i in range(len(results)):
            row = results.iloc[i]
            corr = row["corr"]
            vr = row["vol_ratio"]
            hurst = row["hurst"]

            if pd.isna(corr) or pd.isna(vr):
                continue

            if corr < self.corr_min:
                regime = Regime.BREAKDOWN
                tradeable = False
            elif vr > self.vol_ratio_max:
                regime = Regime.HIGH_VOL
                tradeable = False
            elif hurst is not None and hurst > 0.6:
                regime = Regime.TRENDING
                tradeable = False
            elif hurst is not None and hurst < 0.45:
                regime = Regime.MEAN_REVERTING
                tradeable = True
            else:
                regime = Regime.MEAN_REVERTING
                tradeable = True

            results.iloc[i, results.columns.get_loc("regime")] = regime.value
            results.iloc[i, results.columns.get_loc("tradeable")] = tradeable

        logger.info(
            f"Regime detection: "
            f"{(results['regime']==Regime.MEAN_REVERTING.value).sum()} MR bars, "
            f"{(results['regime']==Regime.BREAKDOWN.value).sum()} breakdown bars"
        )
        return results

    def _rolling_hurst(self, spread: pd.Series, window: int = 100) -> pd.Series:
        """Rolling Hurst exponent."""
        def hurst(ts):
            ts = ts.dropna().values
            if len(ts) < 20:
                return np.nan
            lags = range(2, min(20, len(ts) // 4))
            try:
                tau = [np.std(np.subtract(ts[lag:], ts[:-lag])) for lag in lags]
                return np.polyfit(np.log(lags), np.log(tau), 1)[0]
            except Exception:
                return np.nan

        return spread.rolling(window).apply(hurst, raw=False)

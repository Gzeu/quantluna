"""
QuantLuna — Multi-Timeframe Signal Confirmation (Sprint 16)

Confirms entry signals by requiring agreement across multiple timeframes.
Prevents false entries on noise by demanding alignment of short + long horizon z-scores.

Logic:
  - HTF (High TimeFrame): slow z-score on 4h/daily resampled spread
  - LTF (Low TimeFrame): fast z-score on 1h (standard signal)
  - Entry allowed only when HTF and LTF z-scores agree in direction
  - Prevents counter-trend mean-reversion entries on noisy short-TF signals

Usage:
    from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig

    mtf = MultiTimeframeConfirmation(MTFConfig(htf_resample="4h", htf_zscore_min=0.5))
    confirmed = mtf.confirm(ltf_zscore=2.3, htf_df=htf_data)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class MTFConfig:
    """Configuration for multi-timeframe signal confirmation."""

    # Resample rule for high timeframe (e.g. "4h", "1d", "8h")
    htf_resample: str = "4h"

    # Minimum absolute z-score on HTF to allow entry
    htf_zscore_min: float = 0.5

    # HTF z-score rolling window (in HTF bars)
    htf_zscore_window: int = 20

    # Require same sign: HTF z-score must agree with LTF direction
    require_htf_alignment: bool = True

    # Allow entry when HTF z-score is near zero (range: ±htf_neutral_band)
    # — treats neutral HTF as "no opinion" rather than blocking
    htf_neutral_band: float = 0.3

    # Max staleness: HTF bar must be at most this many LTF bars old
    max_htf_staleness_bars: int = 4


class MultiTimeframeConfirmation:
    """
    Confirms LTF entry signal using a high-timeframe z-score.

    Parameters
    ----------
    cfg : MTFConfig
    """

    def __init__(self, cfg: Optional[MTFConfig] = None) -> None:
        self.cfg = cfg or MTFConfig()
        self._htf_zscore_cache: Optional[float] = None
        self._htf_cache_ts: Optional[pd.Timestamp] = None

    def build_htf_zscore(
        self,
        ltf_df: pd.DataFrame,
        spread_col: str = "spread",
    ) -> pd.Series:
        """
        Resample LTF spread to HTF and compute rolling z-score.

        Parameters
        ----------
        ltf_df     : DataFrame with DatetimeIndex and 'spread' column
        spread_col : name of spread column

        Returns
        -------
        pd.Series with HTF z-score indexed at HTF timestamps
        """
        if spread_col not in ltf_df.columns:
            raise ValueError(f"Column '{spread_col}' not found in DataFrame")

        htf = ltf_df[spread_col].resample(self.cfg.htf_resample).last().dropna()

        if len(htf) < self.cfg.htf_zscore_window + 1:
            logger.warning(
                f"MTF: insufficient HTF bars ({len(htf)}) for window "
                f"{self.cfg.htf_zscore_window}. Returning zeros."
            )
            return pd.Series(0.0, index=htf.index)

        roll_mean = htf.rolling(self.cfg.htf_zscore_window).mean()
        roll_std  = htf.rolling(self.cfg.htf_zscore_window).std(ddof=1).replace(0, np.nan)
        z_htf     = (htf - roll_mean) / roll_std
        return z_htf.fillna(0.0)

    def confirm(
        self,
        ltf_zscore: float,
        htf_zscore: float,
    ) -> bool:
        """
        Confirm whether an LTF entry is aligned with HTF regime.

        Parameters
        ----------
        ltf_zscore : current bar z-score on low timeframe
        htf_zscore : current HTF z-score (from build_htf_zscore or cached)

        Returns
        -------
        True if entry is confirmed, False if blocked
        """
        cfg = self.cfg

        # HTF is in neutral band → no strong opinion, allow entry
        if abs(htf_zscore) <= cfg.htf_neutral_band:
            logger.debug(
                f"MTF: HTF z={htf_zscore:.3f} in neutral band ±{cfg.htf_neutral_band} → PASS"
            )
            return True

        # HTF hasn't reached minimum signal strength
        if abs(htf_zscore) < cfg.htf_zscore_min:
            logger.debug(
                f"MTF: HTF z={htf_zscore:.3f} below min {cfg.htf_zscore_min} → BLOCK"
            )
            return False

        if not cfg.require_htf_alignment:
            return True

        # Require HTF and LTF agree in direction
        ltf_sign = np.sign(ltf_zscore)
        htf_sign = np.sign(htf_zscore)

        if ltf_sign == 0 or htf_sign == 0:
            return True

        aligned = ltf_sign == htf_sign
        if not aligned:
            logger.debug(
                f"MTF: LTF z={ltf_zscore:.3f} vs HTF z={htf_zscore:.3f} "
                f"— MISALIGNED → BLOCK"
            )
        else:
            logger.debug(
                f"MTF: LTF z={ltf_zscore:.3f} vs HTF z={htf_zscore:.3f} "
                f"— ALIGNED → PASS"
            )

        return aligned

    def confirm_from_df(
        self,
        ltf_zscore: float,
        ltf_df: pd.DataFrame,
        current_ts: pd.Timestamp,
        spread_col: str = "spread",
    ) -> bool:
        """
        Full pipeline: build HTF z-score from LTF DataFrame and confirm.

        Parameters
        ----------
        ltf_zscore  : current bar z-score
        ltf_df      : historical LTF DataFrame
        current_ts  : current bar timestamp for staleness check
        spread_col  : spread column name
        """
        z_htf_series = self.build_htf_zscore(ltf_df, spread_col=spread_col)

        if z_htf_series.empty:
            return True

        # Find latest HTF bar not newer than current_ts
        valid = z_htf_series[z_htf_series.index <= current_ts]
        if valid.empty:
            return True

        htf_zscore = float(valid.iloc[-1])
        return self.confirm(ltf_zscore=ltf_zscore, htf_zscore=htf_zscore)

    def batch_confirm(
        self,
        ltf_df: pd.DataFrame,
        zscore_col: str = "zscore",
        spread_col: str = "spread",
    ) -> pd.Series:
        """
        Vectorised confirmation for backtesting.

        Adds a boolean 'mtf_confirmed' column aligned to ltf_df index.

        Parameters
        ----------
        ltf_df     : DataFrame with DatetimeIndex, zscore + spread columns
        zscore_col : LTF z-score column
        spread_col : spread column for HTF resampling

        Returns
        -------
        pd.Series[bool] aligned to ltf_df.index
        """
        z_htf_series = self.build_htf_zscore(ltf_df, spread_col=spread_col)

        # Forward-fill HTF z-score to LTF index
        z_htf_ltf = z_htf_series.reindex(
            ltf_df.index, method="ffill"
        ).fillna(0.0)

        confirmed = pd.Series(True, index=ltf_df.index)

        for i, ts in enumerate(ltf_df.index):
            ltf_z = float(ltf_df[zscore_col].iloc[i]) if not pd.isna(ltf_df[zscore_col].iloc[i]) else 0.0
            htf_z = float(z_htf_ltf.iloc[i])
            confirmed.iloc[i] = self.confirm(ltf_zscore=ltf_z, htf_zscore=htf_z)

        return confirmed

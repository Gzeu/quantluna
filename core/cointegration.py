"""
QuantLuna — Cointegration Testing

Tests:
  1. Engle-Granger two-step (ADF on residuals)
  2. Johansen trace + eigenvalue test
  3. Half-life of mean reversion (AR(1) on spread)
  4. Hurst exponent (mean-reverting < 0.5)
"""
import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import adfuller, coint
from statsmodels.tsa.vector_ar.vecm import coint_johansen
from dataclasses import dataclass
from typing import Optional, Tuple
from loguru import logger


@dataclass
class CointegrationResult:
    is_cointegrated: bool
    eg_pvalue: float          # Engle-Granger p-value
    adf_pvalue: float         # ADF on residuals
    adf_statistic: float
    johansen_trace_stat: Optional[float]
    johansen_pvalue_approx: Optional[float]
    half_life_hours: Optional[float]
    hurst_exponent: Optional[float]
    static_beta: float        # OLS hedge ratio for reference
    static_alpha: float
    spread_mean: float
    spread_std: float
    n_periods: int
    verdict: str              # Human-readable summary


class CointegrationTest:
    """
    Full cointegration testing suite for a pair.

    Parameters
    ----------
    significance : float
        p-value threshold. Default 0.05.
    min_half_life : float
        Minimum half-life in hours. Below this → too fast / noisy.
    max_half_life : float
        Maximum half-life in hours. Above this → too slow to trade.
    """

    def __init__(
        self,
        significance: float = 0.05,
        min_half_life: float = 12.0,
        max_half_life: float = 168.0,
    ):
        self.sig = significance
        self.min_hl = min_half_life
        self.max_hl = max_half_life

    def run(self, y: pd.Series, x: pd.Series, freq_hours: float = 1.0) -> CointegrationResult:
        """
        Run full cointegration test suite.

        Parameters
        ----------
        y, x  : aligned price series (log prices recommended)
        freq_hours : bar frequency in hours (1h → 1.0, 4h → 4.0)
        """
        if len(y) < 100:
            raise ValueError("Need at least 100 observations")

        # --- Static OLS hedge ratio ---
        beta, alpha, _, _, _ = stats.linregress(x, y)
        spread = y - (beta * x + alpha)

        # --- Engle-Granger ---
        eg_score, eg_pvalue, _ = coint(y, x)

        # --- ADF on residuals ---
        adf_result = adfuller(spread, autolag="AIC")
        adf_stat, adf_pvalue = adf_result[0], adf_result[1]

        # --- Johansen (if enough data) ---
        johansen_trace = None
        johansen_pval = None
        if len(y) >= 200:
            try:
                data = pd.concat([y, x], axis=1).dropna()
                jres = coint_johansen(data.values, det_order=0, k_ar_diff=1)
                johansen_trace = float(jres.lr1[0])
                # Critical values at 5%: jres.cvt[0, 1]
                johansen_pval = 0.01 if johansen_trace > jres.cvt[0, 1] else 0.10
            except Exception as e:
                logger.warning(f"Johansen failed: {e}")

        # --- Half-life ---
        half_life = self._calc_half_life(spread, freq_hours)

        # --- Hurst ---
        hurst = self._hurst_exponent(spread.values)

        # --- Decision ---
        is_coint = (
            adf_pvalue < self.sig
            and eg_pvalue < self.sig
            and (half_life is None or self.min_hl <= half_life <= self.max_hl)
        )

        verdict = self._build_verdict(
            is_coint, adf_pvalue, eg_pvalue, half_life, hurst
        )

        return CointegrationResult(
            is_cointegrated=is_coint,
            eg_pvalue=eg_pvalue,
            adf_pvalue=adf_pvalue,
            adf_statistic=adf_stat,
            johansen_trace_stat=johansen_trace,
            johansen_pvalue_approx=johansen_pval,
            half_life_hours=half_life,
            hurst_exponent=hurst,
            static_beta=beta,
            static_alpha=alpha,
            spread_mean=float(spread.mean()),
            spread_std=float(spread.std()),
            n_periods=len(spread),
            verdict=verdict,
        )

    # ------------------------------------------------------------------
    def _calc_half_life(self, spread: pd.Series, freq_hours: float) -> Optional[float]:
        """AR(1) half-life: HL = -log(2) / log(|rho|)"""
        try:
            spread_lag = spread.shift(1).dropna()
            spread_diff = spread.diff().dropna()
            aligned = pd.concat([spread_diff, spread_lag], axis=1).dropna()
            aligned.columns = ["diff", "lag"]
            beta_ar, _, _, _, _ = stats.linregress(aligned["lag"], aligned["diff"])
            if beta_ar >= 0:
                return None  # Not mean-reverting
            hl_periods = -np.log(2) / np.log(abs(1 + beta_ar))
            return hl_periods * freq_hours
        except Exception:
            return None

    def _hurst_exponent(self, ts: np.ndarray, lags_range: int = 20) -> Optional[float]:
        """R/S Hurst exponent. < 0.5 = mean reverting, 0.5 = random walk."""
        try:
            lags = range(2, min(lags_range, len(ts) // 4))
            tau = [np.std(np.subtract(ts[lag:], ts[:-lag])) for lag in lags]
            poly = np.polyfit(np.log(lags), np.log(tau), 1)
            return poly[0]
        except Exception:
            return None

    def _build_verdict(self, is_coint, adf_p, eg_p, hl, hurst) -> str:
        parts = []
        if not is_coint:
            if adf_p >= self.sig:
                parts.append(f"ADF FAIL (p={adf_p:.3f})")
            if eg_p >= self.sig:
                parts.append(f"EG FAIL (p={eg_p:.3f})")
            if hl is not None and hl < self.min_hl:
                parts.append(f"HL too short ({hl:.1f}h < {self.min_hl}h)")
            if hl is not None and hl > self.max_hl:
                parts.append(f"HL too long ({hl:.1f}h > {self.max_hl}h)")
            return "REJECTED: " + "; ".join(parts) if parts else "REJECTED"
        return (
            f"PASS — ADF p={adf_p:.4f}, EG p={eg_p:.4f}, "
            f"HL={hl:.1f}h, Hurst={hurst:.3f}"
        )

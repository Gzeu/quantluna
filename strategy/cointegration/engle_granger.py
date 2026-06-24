"""
QuantLuna — Engle-Granger Cointegration Test  (Sprint 9)

Implementare completă a testului Engle-Granger în doi pași:
  1. Regresie OLS: Y = alpha + beta * X + epsilon
  2. ADF (Augmented Dickey-Fuller) pe reziduuri epsilon

Dacă reziduurile sunt staționare (I(0)), perechea este cointegrated.

Limite reale:
- Engle-Granger presupune exact un vector de cointegration — adică o singură
  relație liniară stabilă între Y și X. Nu poate detecta multiple vectori.
- Beta (hedge ratio) este estimat static (OLS pe întregul sample). Nu este
  adaptiv în timp — pentru hedge ratio dinamic folosiți Kalman Filter.
- Testul are putere scăzută pe serii scurte (< 200 bare).
- Condiție necesară: ambele serii trebuie să fie I(1) — testați cu ADF
  individual înainte. Dacă una e I(0), cointegration nu are sens.
- P-values sunt aproximate prin interpolarea tabelelor MacKinnon (1996).
  Statsmodels folosește aceleași tabele — rezultatele sunt comparabile.
- Nu folosiți p-value drept singur criteriu. Verificați și stabilitatea
  hedge ratio (rolling OLS) și residual diagnostics (Ljung-Box, normalitate).

Dependențe: numpy, statsmodels
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller


@dataclass
class EGResult:
    """
    Rezultatul complet al testului Engle-Granger.

    Atribute principale:
      is_cointegrated  — True dacă p_adf < alpha_threshold (nu validare finală)
      p_value          — p-value ADF pe reziduuri (MacKinnon)
      adf_stat         — statistică ADF
      critical_values  — dict {'1%', '5%', '10%'}
      alpha            — intercept OLS
      beta             — hedge ratio static OLS (Y = alpha + beta * X)
      residuals        — seria reziduuri epsilon (pd.Series)
      n_obs            — număr observații folosite
      adf_lags         — lags selectate automat (AIC) în ADF
      half_life_bars   — half-life estimată pe reziduuri (AR1 approx), None dacă AR coef >= 0
      notes            — avertismente generate automat
    """
    is_cointegrated: bool
    p_value: float
    adf_stat: float
    critical_values: dict
    alpha: float
    beta: float
    residuals: pd.Series
    n_obs: int
    adf_lags: int
    half_life_bars: Optional[float]
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        status = "COINTEGRATED" if self.is_cointegrated else "NOT cointegrated"
        hl = f"{self.half_life_bars:.1f}" if self.half_life_bars else "N/A"
        lines = [
            f"Engle-Granger Result: {status}",
            f"  ADF stat:      {self.adf_stat:.4f}",
            f"  p-value:       {self.p_value:.4f}",
            f"  critical 5%:   {self.critical_values.get('5%', 'N/A')}",
            f"  beta (OLS):    {self.beta:.6f}",
            f"  alpha:         {self.alpha:.6f}",
            f"  half-life:     {hl} bars",
            f"  n_obs:         {self.n_obs}",
            f"  ADF lags:      {self.adf_lags}",
        ]
        if self.notes:
            lines.append("  NOTES: " + " | ".join(self.notes))
        return "\n".join(lines)


class EngleGrangerTest:
    """
    Rulează testul Engle-Granger complet pe o pereche (Y, X).

    Parametri:
      alpha_threshold  — pragul de semnificație pentru ADF p-value (default 0.05)
      trend            — trend specification pentru ADF: 'c' (constant),
                         'ct' (constant + trend), 'nc' (fără nici unul)
                         Folosiți 'c' pentru majority of crypto pairs.
      max_lags         — număr maxim de lags ADF. None = autoselect (AIC)
      min_obs          — număr minim de observații. Sub acest prag, testul refuză.
    """

    def __init__(
        self,
        alpha_threshold: float = 0.05,
        trend: str = "c",
        max_lags: Optional[int] = None,
        min_obs: int = 150,
    ) -> None:
        self.alpha_threshold = alpha_threshold
        self.trend = trend
        self.max_lags = max_lags
        self.min_obs = min_obs

    def run(
        self,
        y: pd.Series,
        x: pd.Series,
        sym_y: str = "Y",
        sym_x: str = "X",
    ) -> EGResult:
        """
        Rulează testul pe seriile y și x (close prices, aliniate pe index).
        Returnează EGResult cu toate detaliile.
        """
        y, x = self._align_and_clean(y, x)
        n = len(y)
        notes = []

        if n < self.min_obs:
            notes.append(f"sample_too_small ({n} < {self.min_obs})")

        if n < 50:
            # Nu putem rula nimic util
            return EGResult(
                is_cointegrated=False,
                p_value=1.0,
                adf_stat=0.0,
                critical_values={},
                alpha=0.0,
                beta=0.0,
                residuals=pd.Series(dtype=float),
                n_obs=n,
                adf_lags=0,
                half_life_bars=None,
                notes=[f"insufficient_data ({n} obs)"],
            )

        # Step 1: OLS Y = alpha + beta * X
        beta, alpha = self._ols(y.to_numpy(), x.to_numpy())
        residuals = pd.Series(y.to_numpy() - alpha - beta * x.to_numpy(), index=y.index)

        # Step 2: ADF pe reziduuri
        adf_result = adfuller(
            residuals.dropna().to_numpy(),
            maxlag=self.max_lags,
            regression=self.trend,
            autolag="AIC",
        )
        adf_stat = float(adf_result[0])
        p_value = float(adf_result[1])
        adf_lags = int(adf_result[2])
        critical_values = {k: float(v) for k, v in adf_result[4].items()}

        # Half-life din AR(1) pe reziduuri
        half_life = self._half_life_ar1(residuals.dropna().to_numpy())

        # Avertismente utile
        if p_value < self.alpha_threshold and half_life is not None and half_life > 120:
            notes.append("half_life_very_long (>120 bars) — mean reversion lentă")
        if p_value < self.alpha_threshold and n < 200:
            notes.append("low_power_small_sample — confirmați cu Johansen")
        if abs(beta) < 1e-6:
            notes.append("beta_near_zero — pair probabil necorelat")

        is_cointegrated = p_value < self.alpha_threshold

        return EGResult(
            is_cointegrated=is_cointegrated,
            p_value=p_value,
            adf_stat=adf_stat,
            critical_values=critical_values,
            alpha=float(alpha),
            beta=float(beta),
            residuals=residuals,
            n_obs=n,
            adf_lags=adf_lags,
            half_life_bars=half_life,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Helpers private
    # ------------------------------------------------------------------

    @staticmethod
    def _align_and_clean(y: pd.Series, x: pd.Series) -> tuple[pd.Series, pd.Series]:
        df = pd.DataFrame({"y": y, "x": x}).dropna()
        return df["y"], df["x"]

    @staticmethod
    def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
        """OLS simplu cu constant: Y = alpha + beta * X."""
        X = np.column_stack([np.ones(len(x)), x])
        coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
        alpha, beta = coefs[0], coefs[1]
        return float(beta), float(alpha)

    @staticmethod
    def _half_life_ar1(residuals: np.ndarray) -> Optional[float]:
        """
        Estimează half-life din regresia AR(1) pe prime diferențe:
          delta_e_t = kappa * e_{t-1} + noise
          half_life = -ln(2) / kappa
        Returnează None dacă kappa >= 0 (lipsă mean reversion).
        """
        if len(residuals) < 20:
            return None
        lag = residuals[:-1]
        delta = np.diff(residuals)
        # OLS fără constant
        kappa = float(np.dot(lag, delta) / np.dot(lag, lag))
        if kappa >= 0:
            return None
        return float(-np.log(2) / kappa)

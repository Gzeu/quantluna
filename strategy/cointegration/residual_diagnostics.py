"""
QuantLuna — Residual Diagnostics  (Sprint 9)

Diagnostice pe reziduuri după testul de cointegration.
Un test de cointegration pozitiv (p < 0.05) NU e suficient singur.
Reziduurile trebuie să fie și:
  1. Fără autocorelare sistematică (Ljung-Box)
  2. Cu distribuție aproximativ normală sau cel puțin simetrică (Jarque-Bera)
  3. Stabil ca varianță în timp (ARCH LM test pentru heteroskedasticity)
  4. Fără structural breaks vizibili (rolling mean / CUSUM simplu)
  5. Cu half-life în interval operațional (tipic 2-72 bare pe 1h data)

De ce contează:
- Autocorelare puternică în reziduuri indică model incomplet (lags sau
  variabile omise) — z-score va genera semnale false sistematic
- Kurtosis mare (fat tails) crește riscul de wicks adverse pe crypto
- ARCH effects (volatilitate clustering) invalidează ipoteza de sigma
  constantă în z-score calculation — position sizing va fi greșit
- Half-life prea scurtă (< 2 bare) = microstructure noise, nu mean reversion reală
- Half-life prea lungă (> 120 bare pe 1h) = mean reversion prea lentă pentru
  un trade pe care îl ții deschis rezonabil

Limite reale:
- Ljung-Box: sensibil la alegerea lag-ului. Folosim sqrt(n) ca default.
- Jarque-Bera: are putere mare pe crypto din cauza fat tails tipice.
  Un JB pozitiv nu invalidează automat strategia — contează magnitudinea.
- ARCH LM: lagul standard e 5. Poate fi zgomotos pe sample mic.
- Structural break detection simplist (rolling zscore of mean) — nu înlocuiește
  Chow test sau QLR test, dar este suficient ca warning operațional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

try:
    from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
    from statsmodels.stats.stattools import jarque_bera
except ImportError as exc:  # pragma: no cover
    raise ImportError("statsmodels >= 0.14 necesar pentru ResidualDiagnostics") from exc


@dataclass
class ResidualReport:
    """
    Raport complet de diagnostice reziduuri.

    passed_all  — True dacă toate testele individuale sunt la nivel acceptable.
                  Considerați acest flag ca "green light for further analysis",
                  nu ca garanție de profitabilitate.
    """
    # Ljung-Box
    lb_stat: float
    lb_pvalue: float
    lb_lags: int
    lb_passed: bool          # True dacă p > 0.05 (fără autocorelare semnificativă)

    # Jarque-Bera
    jb_stat: float
    jb_pvalue: float
    jb_skew: float
    jb_kurtosis: float
    jb_passed: bool          # True dacă p > 0.01 (distribuție acceptabilă)

    # ARCH LM
    arch_stat: float
    arch_pvalue: float
    arch_lags: int
    arch_passed: bool        # True dacă p > 0.05 (fără efecte ARCH semnificative)

    # Half-life
    half_life_bars: Optional[float]
    half_life_passed: bool   # True dacă 2 <= hl <= 120

    # Structural stability
    stability_passed: bool   # True dacă nu există break vizibil
    stability_break_idx: Optional[int]

    # Statistici descriptive
    mean: float
    std: float
    skew: float
    kurtosis: float
    n_obs: int

    passed_all: bool
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        checks = [
            ("Ljung-Box (no autocorr)", self.lb_passed, f"p={self.lb_pvalue:.4f}, lags={self.lb_lags}"),
            ("Jarque-Bera (normality)", self.jb_passed, f"p={self.jb_pvalue:.4f}, kurt={self.jb_kurtosis:.2f}"),
            ("ARCH LM (no het-sked)", self.arch_passed, f"p={self.arch_pvalue:.4f}"),
            ("Half-life [2,120] bars", self.half_life_passed,
             f"{self.half_life_bars:.1f}" if self.half_life_bars else "N/A"),
            ("Structural stability", self.stability_passed,
             f"break@bar={self.stability_break_idx}" if self.stability_break_idx else "OK"),
        ]
        lines = [f"Residual Diagnostics — {'ALL PASSED' if self.passed_all else 'ISSUES FOUND'}"]
        for name, ok, detail in checks:
            icon = "✓" if ok else "✗"
            lines.append(f"  [{icon}] {name:<35} {detail}")
        lines.append(f"  Descriptive: mean={self.mean:.4f}  std={self.std:.4f}  "
                     f"skew={self.skew:.3f}  kurt={self.kurtosis:.3f}  n={self.n_obs}")
        if self.notes:
            lines.append("  NOTES: " + " | ".join(self.notes))
        return "\n".join(lines)


class ResidualDiagnostics:
    """
    Rulează setul complet de diagnostice pe o serie de reziduuri.

    Parametri:
      lb_lags             — lags pentru Ljung-Box. None = int(sqrt(n))
      arch_lags           — lags pentru ARCH LM test (default 5)
      half_life_min       — minim half-life acceptabil în bare (default 2)
      half_life_max       — maxim half-life acceptabil în bare (default 120)
      stability_window    — fereastra rolling pentru break detection (default 60)
      stability_zscore_thr— prag z-score rolling mean pentru break flag (default 2.5)
    """

    def __init__(
        self,
        lb_lags: Optional[int] = None,
        arch_lags: int = 5,
        half_life_min: float = 2.0,
        half_life_max: float = 120.0,
        stability_window: int = 60,
        stability_zscore_thr: float = 2.5,
    ) -> None:
        self.lb_lags = lb_lags
        self.arch_lags = arch_lags
        self.half_life_min = half_life_min
        self.half_life_max = half_life_max
        self.stability_window = stability_window
        self.stability_zscore_thr = stability_zscore_thr

    def run(self, residuals: pd.Series) -> ResidualReport:
        r = residuals.dropna().astype(float)
        n = len(r)
        arr = r.to_numpy()
        notes = []

        if n < 30:
            return self._empty_report(n, ["insufficient_data"])

        lags = self.lb_lags or max(5, int(np.sqrt(n)))
        lags = min(lags, n // 4)

        # --- Ljung-Box ---
        try:
            lb = acorr_ljungbox(arr, lags=[lags], return_df=True)
            lb_stat = float(lb["lb_stat"].iloc[-1])
            lb_pvalue = float(lb["lb_pvalue"].iloc[-1])
        except Exception:
            lb_stat, lb_pvalue = 0.0, 1.0
            notes.append("ljungbox_error")
        lb_passed = lb_pvalue > 0.05

        # --- Jarque-Bera ---
        try:
            jb_stat_val, jb_pvalue, jb_skew_val, jb_kurt_val = jarque_bera(arr)
            jb_stat = float(jb_stat_val)
            jb_pvalue = float(jb_pvalue)
            jb_skew_val = float(jb_skew_val)
            jb_kurtosis = float(jb_kurt_val)
        except Exception:
            jb_stat, jb_pvalue, jb_skew_val, jb_kurtosis = 0.0, 1.0, 0.0, 3.0
            notes.append("jarquebera_error")
        jb_passed = jb_pvalue > 0.01
        if jb_kurtosis > 10:
            notes.append(f"fat_tails kurtosis={jb_kurtosis:.1f} — risc wicks mare")

        # --- ARCH LM ---
        try:
            arch_lm = het_arch(arr, nlags=self.arch_lags)
            arch_stat = float(arch_lm[0])
            arch_pvalue = float(arch_lm[1])
        except Exception:
            arch_stat, arch_pvalue = 0.0, 1.0
            notes.append("arch_lm_error")
        arch_passed = arch_pvalue > 0.05
        if not arch_passed:
            notes.append("ARCH_effects — position sizing fix sigma nu e valid")

        # --- Half-life ---
        half_life = self._half_life_ar1(arr)
        if half_life is not None:
            half_life_passed = self.half_life_min <= half_life <= self.half_life_max
            if half_life < self.half_life_min:
                notes.append(f"half_life_too_short ({half_life:.1f}) — posibil microstructure noise")
            if half_life > self.half_life_max:
                notes.append(f"half_life_too_long ({half_life:.1f}) — mean reversion prea lentă")
        else:
            half_life_passed = False
            notes.append("no_mean_reversion (AR coef >= 0)")

        # --- Structural stability (rolling mean z-score) ---
        break_idx, stability_passed = self._stability_check(arr)
        if not stability_passed:
            notes.append(f"structural_break_detected @ bar {break_idx}")

        # Statistici descriptive
        mean_val = float(arr.mean())
        std_val = float(arr.std())
        skew_val = float(pd.Series(arr).skew())
        kurt_val = float(pd.Series(arr).kurtosis() + 3.0)  # excess → total

        passed_all = lb_passed and arch_passed and half_life_passed and stability_passed

        return ResidualReport(
            lb_stat=lb_stat,
            lb_pvalue=lb_pvalue,
            lb_lags=lags,
            lb_passed=lb_passed,
            jb_stat=jb_stat,
            jb_pvalue=jb_pvalue,
            jb_skew=jb_skew_val,
            jb_kurtosis=jb_kurtosis,
            jb_passed=jb_passed,
            arch_stat=arch_stat,
            arch_pvalue=arch_pvalue,
            arch_lags=self.arch_lags,
            arch_passed=arch_passed,
            half_life_bars=half_life,
            half_life_passed=half_life_passed,
            stability_passed=stability_passed,
            stability_break_idx=break_idx,
            mean=mean_val,
            std=std_val,
            skew=skew_val,
            kurtosis=kurt_val,
            n_obs=n,
            passed_all=passed_all,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Helpers private
    # ------------------------------------------------------------------

    @staticmethod
    def _half_life_ar1(arr: np.ndarray) -> Optional[float]:
        if len(arr) < 20:
            return None
        lag = arr[:-1]
        delta = np.diff(arr)
        kappa = float(np.dot(lag, delta) / (np.dot(lag, lag) + 1e-12))
        if kappa >= 0:
            return None
        return float(-np.log(2) / kappa)

    def _stability_check(
        self, arr: np.ndarray
    ) -> tuple[Optional[int], bool]:
        """
        Rolling mean z-score: dacă media rolling deviation depășeste pragul,
        semnalăm un posibil break structural.
        Returnează (break_index, is_stable).
        """
        w = self.stability_window
        if len(arr) < w * 2:
            return None, True
        series = pd.Series(arr)
        rolling_mean = series.rolling(w).mean().dropna()
        global_mean = float(series.mean())
        global_std = float(series.std()) or 1.0
        z_of_rolling_mean = (rolling_mean - global_mean) / global_std
        breach = z_of_rolling_mean.abs() > self.stability_zscore_thr
        if breach.any():
            first_breach_iloc = int(breach.to_numpy().argmax())
            return first_breach_iloc, False
        return None, True

    @staticmethod
    def _empty_report(n: int, notes: list) -> ResidualReport:
        return ResidualReport(
            lb_stat=0.0, lb_pvalue=1.0, lb_lags=0, lb_passed=False,
            jb_stat=0.0, jb_pvalue=1.0, jb_skew=0.0, jb_kurtosis=3.0, jb_passed=False,
            arch_stat=0.0, arch_pvalue=1.0, arch_lags=0, arch_passed=False,
            half_life_bars=None, half_life_passed=False,
            stability_passed=False, stability_break_idx=None,
            mean=0.0, std=0.0, skew=0.0, kurtosis=3.0,
            n_obs=n, passed_all=False, notes=notes,
        )

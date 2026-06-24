"""
QuantLuna — Johansen Cointegration Test  (Sprint 9)

Testul Johansen este metoda preferată față de Engle-Granger când:
  - Lucrezi cu mai mult de 2 serii (basket trading)
  - Vrei să detectezi numărul exact de vectori de cointegration
  - Vrei hedge ratios derivate direct din eigenvectors (nu OLS static)
  - Vrei testul atât Trace cât și Max-Eigenvalue pentru robustness

Implementare pe baza statsmodels.tsa.vector_ar.vecm.coint_johansen.

Limite reale:
  - Johansen presupune toate seriile I(1). Verificați individual cu ADF.
  - k_ar_diff (numărul de lags VAR) afectează semnificativ rezultatele.
    Default: autoselect prin AIC pe VAR fit (max_lags=5). Pe crypto 1h,
    k_ar_diff=1 sau 2 este frecvent optim.
  - Tabelele critice sunt asimptotice. Pe sample mic (< 200 obs), puterea
    testului scade și pot apărea false positives.
  - Det_order (deterministic term):
      -1 = fără constant (rar util)
       0 = constant în ecuația de cointegration (cel mai comun)
       1 = constant + trend liniar (folosiți cu precauție pe crypto)
  - Vectorii de cointegration normalizați din eigenvectors sunt hedge ratios
    relative, nu absolute. Standardizați pe primul element (echivalent cu
    β[0] = 1.0) înainte de a le folosi în strategie.
  - Johansen pe 2 serii produce același rezultat ca Engle-Granger dacă
    există exact 1 vector. Rulați ambele pentru confirmare încrucișată.

Dependențe: numpy, statsmodels
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.vector_ar.vecm import coint_johansen
except ImportError as exc:  # pragma: no cover
    raise ImportError("statsmodels >= 0.14 necesar pentru JohansenTest") from exc


@dataclass
class JohansenResult:
    """
    Rezultatul complet al testului Johansen.

    Atribute principale:
      n_cointegrating_vectors  — număr de vectori estimat (r)
      trace_stats              — statistici Trace pentru fiecare ipoteză
      trace_crit_95            — valori critice 95% pentru Trace
      max_eig_stats            — statistici Max-Eigenvalue
      max_eig_crit_95          — valori critice 95% pentru Max-Eigenvalue
      eigenvectors             — matrice (n_series × n_series), coloane = vectori cointegrare
      hedge_ratios             — hedge ratios normalizate: listă de dict {sym: ratio}
                                 Primul simbol are ratio = 1.0 (normalizare standard)
      eigenvalues              — valorile proprii descrescătoare
      symbols                  — lista de simboluri în ordinea input
      is_cointegrated          — True dacă r >= 1 (cel puțin un vector)
      notes                    — avertismente
    """
    n_cointegrating_vectors: int
    trace_stats: List[float]
    trace_crit_95: List[float]
    max_eig_stats: List[float]
    max_eig_crit_95: List[float]
    eigenvectors: np.ndarray
    hedge_ratios: List[Dict[str, float]]
    eigenvalues: List[float]
    symbols: List[str]
    is_cointegrated: bool
    det_order: int
    k_ar_diff: int
    n_obs: int
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        status = f"r = {self.n_cointegrating_vectors} cointegrating vector(s)"
        lines = [
            f"Johansen Result: {status} | {'COINTEGRATED' if self.is_cointegrated else 'NOT cointegrated'}",
            f"  symbols:       {self.symbols}",
            f"  det_order:     {self.det_order}  |  k_ar_diff: {self.k_ar_diff}  |  n_obs: {self.n_obs}",
            "  Trace test:",
        ]
        for i, (stat, crit) in enumerate(zip(self.trace_stats, self.trace_crit_95)):
            sig = "*" if stat > crit else " "
            lines.append(f"    H0: r <= {i}   stat={stat:.3f}  crit95={crit:.3f} {sig}")
        lines.append("  Max-Eigenvalue test:")
        for i, (stat, crit) in enumerate(zip(self.max_eig_stats, self.max_eig_crit_95)):
            sig = "*" if stat > crit else " "
            lines.append(f"    H0: r =  {i}   stat={stat:.3f}  crit95={crit:.3f} {sig}")
        if self.hedge_ratios:
            lines.append("  Hedge ratios (vector 0, normalized):")
            for sym, ratio in self.hedge_ratios[0].items():
                lines.append(f"    {sym}: {ratio:.6f}")
        if self.notes:
            lines.append("  NOTES: " + " | ".join(self.notes))
        return "\n".join(lines)


class JohansenTest:
    """
    Rulează testul Johansen pe un set de 2+ serii.

    Parametri:
      det_order   — termenul determinist în ecuația VECM
                    -1: fără termen; 0: constant; 1: constant + trend
      k_ar_diff   — numărul de lags diferențe în VAR (= p-1 unde p e ordinul VAR)
                    None = autoselect AIC (max 5 lags)
      min_obs     — obs minime necesare
    """

    def __init__(
        self,
        det_order: int = 0,
        k_ar_diff: Optional[int] = None,
        min_obs: int = 150,
        max_lags_aic: int = 5,
    ) -> None:
        if det_order not in (-1, 0, 1):
            raise ValueError("det_order trebuie să fie -1, 0 sau 1")
        self.det_order = det_order
        self.k_ar_diff = k_ar_diff
        self.min_obs = min_obs
        self.max_lags_aic = max_lags_aic

    def run(
        self,
        data: pd.DataFrame,
        symbols: Optional[List[str]] = None,
    ) -> JohansenResult:
        """
        Parametri:
          data    — DataFrame cu coloanele = prețuri/close pentru fiecare simbol.
                    Trebuie să fie aliniate și fără NaN.
          symbols — liste de nume simboluri (default: coloane DataFrame)
        """
        data = data.dropna()
        syms = list(symbols or data.columns)
        n_series = data.shape[1]
        n_obs = len(data)
        notes = []

        if n_obs < self.min_obs:
            notes.append(f"sample_too_small ({n_obs} < {self.min_obs})")

        if n_obs < 50 or n_series < 2:
            return self._empty_result(syms, n_obs, notes)

        # Autoselect k_ar_diff via AIC dacă nu e specificat
        k = self.k_ar_diff
        if k is None:
            k = self._select_lags_aic(data.to_numpy(), self.max_lags_aic)
            notes.append(f"k_ar_diff autoselected via AIC: {k}")

        k = max(1, k)

        try:
            res = coint_johansen(data.to_numpy(), det_order=self.det_order, k_ar_diff=k)
        except Exception as exc:
            notes.append(f"johansen_error: {exc}")
            return self._empty_result(syms, n_obs, notes)

        # Număr vectori de cointegration: câte ipoteze H0: r<=i sunt respinse
        # Folosim testul Trace ca primar, Max-Eigenvalue ca confirmare
        trace_stats = res.lr1.tolist()
        trace_crit_95 = res.cvt[:, 1].tolist()  # coloana 1 = 95%
        max_eig_stats = res.lr2.tolist()
        max_eig_crit_95 = res.cvm[:, 1].tolist()

        n_coint = 0
        for i in range(n_series - 1):
            if trace_stats[i] > trace_crit_95[i]:
                n_coint = i + 1
            else:
                break

        eigenvalues = res.eig.tolist()
        eigenvectors = res.evec  # shape (n_series, n_series)

        # Hedge ratios normalizate: vector[0][0] = 1.0
        hedge_ratios = []
        for vec_idx in range(min(n_coint if n_coint > 0 else 1, n_series)):
            vec = eigenvectors[:, vec_idx]
            norm = vec[0] if abs(vec[0]) > 1e-12 else 1.0
            normalized = vec / norm
            hedge_ratios.append({sym: float(normalized[i]) for i, sym in enumerate(syms)})

        # Avertismente
        if n_coint == 0 and n_obs < 300:
            notes.append("low_power — încercați mai multe date")
        if n_coint > 1:
            notes.append(f"multiple_coint_vectors (r={n_coint}) — folosiți doar vectorul 0")
        if k >= 4:
            notes.append("high_k_ar_diff — verificați că nu există overfit pe lags")

        return JohansenResult(
            n_cointegrating_vectors=n_coint,
            trace_stats=trace_stats,
            trace_crit_95=trace_crit_95,
            max_eig_stats=max_eig_stats,
            max_eig_crit_95=max_eig_crit_95,
            eigenvectors=eigenvectors,
            hedge_ratios=hedge_ratios,
            eigenvalues=eigenvalues,
            symbols=syms,
            is_cointegrated=n_coint >= 1,
            det_order=self.det_order,
            k_ar_diff=k,
            n_obs=n_obs,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Helpers private
    # ------------------------------------------------------------------

    def _select_lags_aic(self, data: np.ndarray, max_lags: int) -> int:
        """Selectează k_ar_diff care minimizează AIC pe un VAR simplu."""
        try:
            from statsmodels.tsa.vector_ar.var_model import VAR
            model = VAR(data)
            result = model.select_order(maxlags=max_lags)
            return max(1, int(result.aic))
        except Exception:
            return 1

    @staticmethod
    def _empty_result(syms: List[str], n_obs: int, notes: list) -> "JohansenResult":
        return JohansenResult(
            n_cointegrating_vectors=0,
            trace_stats=[],
            trace_crit_95=[],
            max_eig_stats=[],
            max_eig_crit_95=[],
            eigenvectors=np.array([]),
            hedge_ratios=[],
            eigenvalues=[],
            symbols=syms,
            is_cointegrated=False,
            det_order=0,
            k_ar_diff=0,
            n_obs=n_obs,
            notes=notes,
        )

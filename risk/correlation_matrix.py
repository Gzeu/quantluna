"""
QuantLuna — Spread Correlation Matrix  (Sprint 10)

Menține o matrice de corelație rolling între spread-urile active.
Utilizat de PortfolioAllocator pentru:
  1. Blocare poziții noi când corelația cu un spread existent > threshold
  2. Penalizare Kelly pentru perechi corelate (diversification discount)
  3. Detectare concentration risk când ≥2 spread-uri mișcă împreună

De ce contează în pairs trading:
  - Dacă ETH/BTC spread și SOL/BTC spread sunt corelate > 0.7, expunerea
    reală la BTC este dublată față de ce arată sizing-ul individual.
  - Kelly standard ignoră corelația între strategii simultane.
    Kelly cross-pair penalizează explicit pozițiile redundante.
  - Correlation breakdown (corelație scade brusc) este un semnal de regim
    shift — spread-urile nu mai evoluează împreună, ergo sizing anterior
    e incorect.

Limite reale:
  - Corelație pe returns spot nu captează cointegration breakdown.
    Un pair poate fi cointegrat și totuși să aibă spread returns necorelate
    pe ferestre scurte.
  - Rolling window fixă nu se adaptează la regime shifts rapide.
    La volatilitate extremă, corelația se poate schimba în câteva bare.
  - Minimum sample: dacă un spread are < min_history bare, corelația
    cu el nu este calculată și este tratată ca 0.0 (permisivă).
  - Shrinkage Ledoit-Wolf este aplicat pe matrice pentru a reduce
    estimator noise pe sample mic. Dezactivabil dacă n > 500.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class CorrelationMatrixConfig:
    window: int = 120              # bare pentru rolling correlation
    min_history: int = 30          # bare minime înainte de a calcula corr
    max_corr_threshold: float = 0.70  # blocare poziție dacă |corr| > prag
    use_ledoit_wolf: bool = True   # shrinkage estimator pentru noise reduction
    shrink_threshold_n: int = 500  # dezactivare shrinkage dacă n > prag


class SpreadCorrelationMatrix:
    """
    Menține și actualizează matricea de corelație rolling între spread-uri active.

    Utilizare:
      matrix = SpreadCorrelationMatrix()
      matrix.update("ETH/BTC", spread_value_float)
      allowed, max_corr, correlated_with = matrix.check_new_pair("SOL/BTC", spread_series)
    """

    def __init__(self, cfg: Optional[CorrelationMatrixConfig] = None) -> None:
        self.cfg = cfg or CorrelationMatrixConfig()
        # pair_id -> deque de spread values (ultimele `window` valori)
        self._buffers: Dict[str, list] = {}

    def update(self, pair_id: str, spread_value: float) -> None:
        """Adaugă o nouă valoare de spread pentru pair_id."""
        if pair_id not in self._buffers:
            self._buffers[pair_id] = []
        buf = self._buffers[pair_id]
        buf.append(float(spread_value))
        # păstrăm doar ultimele `window` valori
        if len(buf) > self.cfg.window:
            self._buffers[pair_id] = buf[-self.cfg.window:]

    def remove(self, pair_id: str) -> None:
        """Elimină un pair din matrice la închiderea poziției."""
        self._buffers.pop(pair_id, None)

    def check_new_pair(
        self,
        candidate_id: str,
        candidate_spread: pd.Series,
    ) -> Tuple[bool, float, List[str]]:
        """
        Verifică dacă un pair candidat poate fi adăugat fără a depăși
        corelația maximă față de spread-urile active.

        Returns:
          (allowed, max_abs_corr, list_of_correlated_pairs)
          allowed = False dacă max_abs_corr > threshold
        """
        active_pairs = [
            pid for pid, buf in self._buffers.items()
            if len(buf) >= self.cfg.min_history and pid != candidate_id
        ]

        if not active_pairs:
            return True, 0.0, []

        cand_arr = candidate_spread.dropna().to_numpy()[-self.cfg.window:]
        if len(cand_arr) < self.cfg.min_history:
            # Nu avem suficiente date — permitem dar marcăm ca incert
            return True, 0.0, ["insufficient_history"]

        correlated: List[str] = []
        max_abs_corr = 0.0

        for pid in active_pairs:
            buf = np.array(self._buffers[pid])
            n = min(len(cand_arr), len(buf))
            if n < self.cfg.min_history:
                continue
            c = float(np.corrcoef(cand_arr[-n:], buf[-n:])[0, 1])
            if np.isnan(c):
                continue
            abs_c = abs(c)
            if abs_c > max_abs_corr:
                max_abs_corr = abs_c
            if abs_c > self.cfg.max_corr_threshold:
                correlated.append(f"{pid}(corr={c:.3f})")

        allowed = len(correlated) == 0
        return allowed, max_abs_corr, correlated

    def get_correlation_matrix(self) -> pd.DataFrame:
        """
        Returnează matricea de corelație curentă ca DataFrame.
        Perechi cu date insuficiente sunt NaN.
        Dacă use_ledoit_wolf, aplică shrinkage.
        """
        pairs = [
            pid for pid, buf in self._buffers.items()
            if len(buf) >= self.cfg.min_history
        ]
        if len(pairs) < 2:
            return pd.DataFrame()

        min_len = min(len(self._buffers[p]) for p in pairs)
        data = np.column_stack([np.array(self._buffers[p])[-min_len:] for p in pairs])

        if self.cfg.use_ledoit_wolf and min_len <= self.cfg.shrink_threshold_n:
            try:
                from sklearn.covariance import LedoitWolf
                lw = LedoitWolf()
                lw.fit(data)
                cov = lw.covariance_
                std = np.sqrt(np.diag(cov))
                std[std < 1e-12] = 1e-12
                corr_mat = cov / np.outer(std, std)
            except ImportError:
                corr_mat = np.corrcoef(data.T)
        else:
            corr_mat = np.corrcoef(data.T)

        np.fill_diagonal(corr_mat, 1.0)
        return pd.DataFrame(corr_mat, index=pairs, columns=pairs)

    def diversification_discount(
        self,
        candidate_id: str,
        candidate_spread: pd.Series,
    ) -> float:
        """
        Returnează un factor de discount [0.0, 1.0] pentru Kelly sizing.
        discount = 1.0 - max(|corr|, 0) * 0.5
        Exemplu: corr=0.6 → discount=0.70 → Kelly allocation * 0.70

        Această penalizare nu elimină poziția dar reduce sizing-ul
        proporțional cu redundanța față de portfolio-ul existent.
        """
        _, max_corr, _ = self.check_new_pair(candidate_id, candidate_spread)
        return max(0.0, 1.0 - max_corr * 0.5)

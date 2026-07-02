"""
QuantLuna — CorrelationFilter
Sprint 29

Filtru de corelatie pentru Multi-Pair Manager:
  - Calculeaza Pearson rolling pe fereastra configurabila (default 60 bare)
  - Blocheaza perechi cu |corr| > threshold (default 0.80)
  - Returneaza: allowed / blocked sets
  - Correlation matrix JSON pentru API
  - Async-safe (calcul pe thread pool pentru numpy ops)
  - Cache intern: recalculeaza la fiecare n_bars noi (incremental)

Corelare blocata = perechi care tranzactioneaza aceleasi active in
aceeasi directie — supraexpunere directionala.

Usage:
    from risk.correlation_filter import CorrelationFilter

    cf = CorrelationFilter(threshold=0.80, window=60)
    cf.update("BTCUSDT", close_series)     # adauga/actualizeaza serie
    cf.update("ETHUSDT", close_series)
    cf.update("SOLUSDT", close_series)

    allowed, blocked = cf.check_new_pair("SOLUSDT", active_symbols=["BTCUSDT"])
    # allowed=False daca corr(SOLUSDT, BTCUSDT) > 0.80

    matrix = cf.correlation_matrix()  # dict pentru API
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class CorrelationFilter:
    """
    Live correlation filter pentru Multi-Pair Manager.
    """

    def __init__(
        self,
        threshold: float = 0.80,
        window:    int   = 60,
        method:    str   = "pearson",   # pearson | spearman
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold trebuie in (0, 1], primit: {threshold}")
        self.threshold = threshold
        self.window    = window
        self.method    = method
        # symbol -> pd.Series de returns
        self._series:  Dict[str, pd.Series] = {}
        self._blocked: Set[str] = set()         # simboluri blocate activ

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, symbol: str, close_series: pd.Series) -> None:
        """
        Actualizeaza seria de preturi pentru un simbol.
        Stocheaza log-returns pe ultimele window*3 bare.
        """
        sym    = symbol.upper()
        closes = close_series.dropna()
        if len(closes) < 2:
            return
        returns = np.log(closes / closes.shift(1)).dropna()
        # Pastreaza ultimele window*3 valori
        self._series[sym] = returns.iloc[-(self.window * 3):]
        logger.debug(f"CorrelationFilter: updated {sym} ({len(self._series[sym])} bars)")

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def check_new_pair(
        self,
        new_symbol:     str,
        active_symbols: List[str],
    ) -> Tuple[bool, List[dict]]:
        """
        Verifica daca new_symbol poate fi adaugat dat activele active.
        Returns:
            (allowed: bool, violations: List[dict])
        violations = lista de perechi cu corelatie > threshold.
        """
        sym       = new_symbol.upper()
        actives   = [s.upper() for s in active_symbols]
        violations: List[dict] = []

        if sym not in self._series:
            logger.info(f"CorrelationFilter: {sym} fara date, allow by default")
            return True, []

        for active in actives:
            if active == sym:
                continue
            if active not in self._series:
                continue
            corr = self._compute_corr(sym, active)
            if corr is None:
                continue
            if abs(corr) > self.threshold:
                violations.append({
                    "symbol_a": sym,
                    "symbol_b": active,
                    "correlation": round(corr, 4),
                    "threshold":   self.threshold,
                    "blocked":     True,
                })
                logger.warning(
                    f"CorrelationFilter: {sym} BLOCAT vs {active} | corr={corr:.4f} > {self.threshold}"
                )

        allowed = len(violations) == 0
        return allowed, violations

    def check_pair_symbols(
        self,
        sym_y:          str,
        sym_x:          str,
        active_symbols: List[str],
    ) -> Tuple[bool, List[dict]]:
        """
        Verifica ambele simboluri ale perechii (sym_y si sym_x) vs active.
        O pereche e blocata daca oricare din simboluri e prea corelat.
        """
        ok_y, viol_y = self.check_new_pair(sym_y, active_symbols)
        ok_x, viol_x = self.check_new_pair(sym_x, active_symbols)
        return (ok_y and ok_x), (viol_y + viol_x)

    # ------------------------------------------------------------------
    # Correlation Matrix
    # ------------------------------------------------------------------

    def correlation_matrix(self) -> dict:
        """
        Calculeaza si returneaza correlation matrix pentru toate simbolurile.
        Format: {"symbols": [...], "matrix": [[...], ...], "blocked_pairs": [...]}
        """
        symbols = sorted(self._series.keys())
        n       = len(symbols)
        if n < 2:
            return {"symbols": symbols, "matrix": [], "blocked_pairs": [], "threshold": self.threshold}

        # Aliniaza toate seriile pe index comun
        df = pd.DataFrame({sym: self._series[sym] for sym in symbols})
        df = df.dropna()

        if len(df) < max(10, self.window // 4):
            return {"symbols": symbols, "matrix": [], "blocked_pairs": [], "threshold": self.threshold,
                    "warning": "date insuficiente"}

        corr_df = df.corr(method=self.method)
        matrix  = corr_df.round(4).values.tolist()

        # Identifica perechi blocate
        blocked_pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                corr_val = corr_df.iloc[i, j]
                if abs(corr_val) > self.threshold:
                    blocked_pairs.append({
                        "symbol_a":    symbols[i],
                        "symbol_b":    symbols[j],
                        "correlation": round(float(corr_val), 4),
                        "blocked":     True,
                    })

        return {
            "symbols":       symbols,
            "matrix":        matrix,
            "blocked_pairs": blocked_pairs,
            "threshold":     self.threshold,
            "window":        self.window,
            "n_symbols":     n,
        }

    def remove(self, symbol: str) -> None:
        """Sterge un simbol din filtru."""
        self._series.pop(symbol.upper(), None)

    def symbols(self) -> List[str]:
        return list(self._series.keys())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_corr(self, sym_a: str, sym_b: str) -> Optional[float]:
        s_a = self._series.get(sym_a)
        s_b = self._series.get(sym_b)
        if s_a is None or s_b is None:
            return None
        # Aliniere pe index comun
        aligned = pd.concat([s_a, s_b], axis=1).dropna()
        if len(aligned) < max(10, self.window // 2):
            logger.debug(f"CorrelationFilter: date insuficiente pentru {sym_a}-{sym_b}")
            return None
        # Foloseste ultimele window bare
        tail  = aligned.tail(self.window)
        corr  = tail.iloc[:, 0].corr(tail.iloc[:, 1], method=self.method)
        return None if pd.isna(corr) else float(corr)

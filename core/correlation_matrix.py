"""
core/correlation_matrix.py — matrice de corelatie live intre perechile tranzactionate.

Monitorizeaza corelatia rolling intre spread-urile perechilor pentru a detecta
cand doua perechi sunt prea corelate (risc de concentrare) sau decorelate
(potential breakdown al cointegrării).
"""
from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np


class CorrelationMatrix:
    """
    Calculeaza si monitorizeaza corelatia rolling intre serii de preturi.

    Usage::

        cm = CorrelationMatrix(window=60, high_corr_threshold=0.85)
        cm.update("BTCUSDT", 29000.0)
        cm.update("ETHUSDT", 1850.0)
        matrix = cm.get_matrix()
        alerts = cm.get_high_correlation_pairs()
    """

    def __init__(
        self,
        window: int = 60,
        high_corr_threshold: float = 0.85,
        low_corr_threshold: float = 0.30,
    ) -> None:
        self._window = window
        self._high_thr = high_corr_threshold
        self._low_thr = low_corr_threshold
        self._series: Dict[str, deque] = {}

    def update(self, symbol: str, value: float) -> None:
        if symbol not in self._series:
            self._series[symbol] = deque(maxlen=self._window)
        self._series[symbol].append(float(value))

    def get_correlation(self, sym_a: str, sym_b: str) -> Optional[float]:
        if sym_a not in self._series or sym_b not in self._series:
            return None
        a = np.array(self._series[sym_a])
        b = np.array(self._series[sym_b])
        n = min(len(a), len(b))
        if n < 5:
            return None
        a, b = a[-n:], b[-n:]
        if a.std() < 1e-10 or b.std() < 1e-10:
            return None
        return float(np.corrcoef(a, b)[0, 1])

    def get_matrix(self) -> Dict[Tuple[str, str], float]:
        symbols = list(self._series.keys())
        result = {}
        for i, s1 in enumerate(symbols):
            for s2 in symbols[i + 1:]:
                corr = self.get_correlation(s1, s2)
                if corr is not None:
                    result[(s1, s2)] = round(corr, 4)
        return result

    def get_high_correlation_pairs(self) -> List[Tuple[str, str, float]]:
        return [
            (a, b, c)
            for (a, b), c in self.get_matrix().items()
            if abs(c) >= self._high_thr
        ]

    def get_decorrelated_pairs(self) -> List[Tuple[str, str, float]]:
        """Perechi care au pierdut corelatia (potential breakdown)."""
        return [
            (a, b, c)
            for (a, b), c in self.get_matrix().items()
            if abs(c) < self._low_thr
        ]

    @property
    def symbols(self) -> List[str]:
        return list(self._series.keys())

    def reset(self, symbol: Optional[str] = None) -> None:
        if symbol:
            self._series.pop(symbol, None)
        else:
            self._series.clear()

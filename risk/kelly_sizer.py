"""
risk/kelly_sizer.py — Kelly Criterion position sizer.

Calculeaza marimea optima a pozitiei folosind Full Kelly si Fractional Kelly.
Integrat cu performance_analytics pentru win_rate si avg_win/avg_loss
din istoricul real de tranzactii.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KellyResult:
    kelly_fraction: float
    fractional_kelly: float
    recommended_pct: float
    capital_usdt: float
    position_usdt: float
    capped: bool


class KellySizer:
    """
    Kelly Criterion sizer cu fractional scaling si hard cap.

    Usage::

        sizer = KellySizer(kelly_fraction=0.25, max_pct=0.10)
        result = sizer.size(
            capital_usdt=10000.0,
            win_rate=0.60,
            avg_win=150.0,
            avg_loss=100.0,
        )
        print(f"Position: {result.position_usdt:.2f} USDT")
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        max_pct: float = 0.10,
        min_pct: float = 0.005,
    ) -> None:
        self._kelly_fraction = kelly_fraction
        self._max_pct = max_pct
        self._min_pct = min_pct

    def size(
        self,
        capital_usdt: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> KellyResult:
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return self._fallback(capital_usdt)

        b = abs(avg_win / avg_loss)
        p = win_rate
        q = 1.0 - p
        full_kelly = (b * p - q) / b

        if full_kelly <= 0:
            return self._fallback(capital_usdt)

        frac = full_kelly * self._kelly_fraction
        capped = False

        if frac > self._max_pct:
            frac = self._max_pct
            capped = True
        elif frac < self._min_pct:
            frac = self._min_pct

        return KellyResult(
            kelly_fraction=round(full_kelly, 6),
            fractional_kelly=round(frac, 6),
            recommended_pct=round(frac * 100, 4),
            capital_usdt=capital_usdt,
            position_usdt=round(capital_usdt * frac, 2),
            capped=capped,
        )

    def size_from_analytics(self, capital_usdt: float, analytics) -> KellyResult:
        """Convenience: accepta un obiect PerformanceMetrics."""
        return self.size(
            capital_usdt=capital_usdt,
            win_rate=analytics.win_rate,
            avg_win=analytics.avg_win,
            avg_loss=abs(analytics.avg_loss),
        )

    def _fallback(self, capital_usdt: float) -> KellyResult:
        frac = self._min_pct
        return KellyResult(
            kelly_fraction=0.0,
            fractional_kelly=frac,
            recommended_pct=round(frac * 100, 4),
            capital_usdt=capital_usdt,
            position_usdt=round(capital_usdt * frac, 2),
            capped=False,
        )

"""
strategy/signal_combiner.py — multi-signal combiner cu ponderare adaptiva.

Combina semnale independente (z-score Kalman, momentum, regim, volum)
folosind ponderi configurabile si returneaza un scor final normalizat
[-1, 1] cu directia si intensitatea tranzactiei recomandate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Signal:
    name: str
    value: float
    weight: float = 1.0
    enabled: bool = True


@dataclass
class CombinedSignal:
    score: float
    direction: str
    strength: str
    signals: List[Signal] = field(default_factory=list)
    veto: bool = False
    veto_reason: Optional[str] = None

    @property
    def should_trade(self) -> bool:
        return not self.veto and abs(self.score) >= 0.3


class SignalCombiner:
    """
    Combina semnale cu ponderi si aplica veto-uri hard.

    Usage::

        combiner = SignalCombiner()
        combiner.add_signal("zscore", zscore_value, weight=2.0)
        combiner.add_signal("momentum", mom_value, weight=0.5)
        combiner.add_veto("regime", lambda ctx: ctx.get("regime") == "high_vol")

        result = combiner.combine({"regime": "normal"})
        if result.should_trade:
            place_order(result.direction)
    """

    def __init__(self, entry_threshold: float = 0.3) -> None:
        self._entry_threshold = entry_threshold
        self._signals: List[Signal] = []
        self._veto_checks: List[tuple] = []

    def add_signal(self, name: str, value: float, weight: float = 1.0, enabled: bool = True) -> None:
        clipped = max(-1.0, min(1.0, float(value)))
        self._signals.append(Signal(name=name, value=clipped, weight=weight, enabled=enabled))

    def add_veto(self, reason: str, check_fn) -> None:
        self._veto_checks.append((reason, check_fn))

    def combine(self, context: Optional[Dict] = None) -> CombinedSignal:
        context = context or {}

        for reason, check_fn in self._veto_checks:
            try:
                if check_fn(context):
                    return CombinedSignal(
                        score=0.0,
                        direction="FLAT",
                        strength="none",
                        signals=list(self._signals),
                        veto=True,
                        veto_reason=reason,
                    )
            except Exception:
                pass

        active = [s for s in self._signals if s.enabled]
        if not active:
            return CombinedSignal(score=0.0, direction="FLAT", strength="none")

        total_weight = sum(s.weight for s in active)
        if total_weight < 1e-9:
            return CombinedSignal(score=0.0, direction="FLAT", strength="none")

        score = sum(s.value * s.weight for s in active) / total_weight
        score = max(-1.0, min(1.0, score))

        if score > self._entry_threshold:
            direction = "LONG"
        elif score < -self._entry_threshold:
            direction = "SHORT"
        else:
            direction = "FLAT"

        abs_score = abs(score)
        if abs_score >= 0.75:
            strength = "strong"
        elif abs_score >= 0.5:
            strength = "moderate"
        elif abs_score >= 0.3:
            strength = "weak"
        else:
            strength = "none"

        return CombinedSignal(
            score=score,
            direction=direction,
            strength=strength,
            signals=list(active),
        )

    def reset(self) -> None:
        self._signals.clear()

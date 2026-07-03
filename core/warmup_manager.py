"""
core/warmup_manager.py — manager pentru perioada de warmup a filtrului Kalman.

In primele N bare, modelul nu este inca stabil. Acest modul tracheaza
starea de warmup si blocheaza semnalele pana cand filtrul converge.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WarmupStatus:
    ready: bool
    bars_received: int
    bars_required: int
    pct_complete: float
    message: str


class WarmupManager:
    """
    Blocheaza trading-ul in perioada de warmup a filtrului.

    Usage::

        warmup = WarmupManager(bars_required=100)
        for bar in bars:
            warmup.tick()
            if not warmup.ready:
                continue
            # signal generation
    """

    def __init__(self, bars_required: int = 100, pair: str = "") -> None:
        self._required = max(1, bars_required)
        self._pair = pair
        self._count = 0
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def bars_received(self) -> int:
        return self._count

    def tick(self, n: int = 1) -> bool:
        if self._ready:
            return True
        self._count += n
        if self._count >= self._required:
            self._ready = True
        return self._ready

    def reset(self) -> None:
        self._count = 0
        self._ready = False

    def status(self) -> WarmupStatus:
        pct = min(100.0, self._count / self._required * 100.0)
        if self._ready:
            msg = f"Ready ({self._pair})" if self._pair else "Ready"
        else:
            remaining = self._required - self._count
            msg = f"Warmup: {remaining} bars remaining"
            if self._pair:
                msg = f"[{self._pair}] {msg}"
        return WarmupStatus(
            ready=self._ready,
            bars_received=self._count,
            bars_required=self._required,
            pct_complete=round(pct, 1),
            message=msg,
        )

    def force_ready(self) -> None:
        """Forteaza starea ready (util pentru backtest cu date istorice suficiente)."""
        self._count = self._required
        self._ready = True

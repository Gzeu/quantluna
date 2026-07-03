"""
strategy/entry_filter.py — filtru multi-criteriu pentru intrari in pozitie.

Filtreaza intrari pe baza de:
- Ora zilei (evita ore cu lichiditate scazuta)
- Volum relativ (nu intra pe volum scazut)
- Regim de volatilitate
- Spread prea larg
- Pozitie deja deschisa pe pereche
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Set


@dataclass
class EntryFilterConfig:
    min_volume_ratio: float = 0.5
    max_spread_pct: float = 0.15
    forbidden_hours_utc: tuple = (0, 1, 2, 3, 22, 23)
    allowed_regimes: tuple = ("normal", "elevated")
    max_open_pairs: int = 5


@dataclass
class FilterResult:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


class EntryFilter:
    """
    Aplica filtre de intrare inainte de plasarea unui ordin.

    Usage::

        filt = EntryFilter(EntryFilterConfig())
        result = filt.check(
            pair="BTCUSDT/ETHUSDT",
            volume_ratio=0.8,
            spread_pct=0.05,
            regime="normal",
            open_pairs={"BTCUSDT/BNBUSDT"},
        )
        if not result:
            logger.info("Entry blocked: %s", result.reason)
    """

    def __init__(self, config: Optional[EntryFilterConfig] = None) -> None:
        self._cfg = config or EntryFilterConfig()

    def check(
        self,
        pair: str,
        volume_ratio: float = 1.0,
        spread_pct: float = 0.0,
        regime: str = "normal",
        open_pairs: Optional[Set[str]] = None,
        now: Optional[datetime] = None,
    ) -> FilterResult:
        open_pairs = open_pairs or set()
        now = now or datetime.now(timezone.utc)

        if now.hour in self._cfg.forbidden_hours_utc:
            return FilterResult(False, f"forbidden hour UTC {now.hour}")

        if volume_ratio < self._cfg.min_volume_ratio:
            return FilterResult(False, f"volume too low ({volume_ratio:.2f} < {self._cfg.min_volume_ratio})")

        if spread_pct > self._cfg.max_spread_pct:
            return FilterResult(False, f"spread too wide ({spread_pct:.3f}% > {self._cfg.max_spread_pct}%)")

        if regime not in self._cfg.allowed_regimes:
            return FilterResult(False, f"regime '{regime}' not allowed")

        if len(open_pairs) >= self._cfg.max_open_pairs:
            return FilterResult(False, f"max open pairs reached ({self._cfg.max_open_pairs})")

        if pair in open_pairs:
            return FilterResult(False, f"pair '{pair}' already open")

        return FilterResult(True, "ok")

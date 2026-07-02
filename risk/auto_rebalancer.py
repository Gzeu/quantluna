"""
QuantLuna — Auto-Rebalancer
Sprint 30

Rebalansare automata capital intre perechi bazata pe Sharpe rolling:
  - Creste alocarea la perechi cu Sharpe > sharpe_target
  - Reduce alocarea la perechi cu Sharpe < sharpe_floor
  - Constrangeri: min_alloc_pct / max_alloc_pct per pereche
  - Max total capital utilizat: max_total_pct (default 100%)
  - Cooldown: minim cooldown_h ore intre rebalansari
  - Dry-run mode: calculeaza noile alocari fara a le aplica
  - Emite REBALANCE event via AlertDispatcher (optional)

Algoritm:
  1. Colecteaza Sharpe rolling per pereche
  2. Rank descendent dupa Sharpe
  3. Proportional allocation: alloc_i = total_capital * (sharpe_i / sum_sharpe)
     cu clamp [min_alloc, max_alloc]
  4. Normalizeaza sa nu depaseasca max_total
  5. Aplica sau returneaza dry-run

Usage:
    from risk.auto_rebalancer import AutoRebalancer

    rb = AutoRebalancer(total_capital=10000.0)
    rb.update_pair("BTC/ETH",  sharpe=1.8, current_alloc=2000.0)
    rb.update_pair("SOL/BNB",  sharpe=0.4, current_alloc=1500.0)
    rb.update_pair("ETH/AVAX", sharpe=1.1, current_alloc=2500.0)

    result = rb.compute_rebalance(dry_run=True)
    print(result["allocations"])   # noile alocari propuse
    print(result["changes"])       # delta per pereche
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PairMetric:
    pair:          str
    sharpe:        float
    current_alloc: float      # USDT
    n_trades:      int   = 0
    pnl_usdt:      float = 0.0
    last_updated:  float = field(default_factory=time.monotonic)


@dataclass
class RebalanceResult:
    timestamp:     str
    dry_run:       bool
    total_capital: float
    allocations:   Dict[str, float]   # pair -> new alloc USDT
    changes:       Dict[str, float]   # pair -> delta USDT
    sharpe_map:    Dict[str, float]   # pair -> sharpe folosit
    skipped:       bool = False
    skip_reason:   str  = ""

    def to_dict(self) -> dict:
        return {
            "timestamp":     self.timestamp,
            "dry_run":       self.dry_run,
            "total_capital": self.total_capital,
            "allocations":   {k: round(v, 2) for k, v in self.allocations.items()},
            "changes":       {k: round(v, 2) for k, v in self.changes.items()},
            "sharpe_map":    {k: round(v, 4) for k, v in self.sharpe_map.items()},
            "skipped":       self.skipped,
            "skip_reason":   self.skip_reason,
        }


class AutoRebalancer:
    """
    Rebalansare capital bazata pe Sharpe rolling.
    """

    def __init__(
        self,
        total_capital:   float = 10_000.0,
        min_alloc_pct:   float = 0.05,    # min 5% per pereche
        max_alloc_pct:   float = 0.40,    # max 40% per pereche
        max_total_pct:   float = 1.00,    # max 100% capital utilizat
        sharpe_target:   float = 1.0,     # Sharpe tinta
        sharpe_floor:    float = 0.0,     # Sharpe sub care reduci
        cooldown_h:      float = 24.0,    # ore intre rebalansari
        min_pairs:       int   = 1,       # minim perechi pentru rebalansare
    ) -> None:
        self.total_capital  = total_capital
        self.min_alloc_pct  = min_alloc_pct
        self.max_alloc_pct  = max_alloc_pct
        self.max_total_pct  = max_total_pct
        self.sharpe_target  = sharpe_target
        self.sharpe_floor   = sharpe_floor
        self.cooldown_s     = cooldown_h * 3600.0
        self.min_pairs      = min_pairs

        self._pairs:   Dict[str, PairMetric] = {}
        self._history: List[dict]            = []
        self._last_rebalance_ts: float       = 0.0

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_pair(
        self,
        pair:          str,
        sharpe:        float,
        current_alloc: float,
        n_trades:      int   = 0,
        pnl_usdt:      float = 0.0,
    ) -> None:
        """Actualizeaza metricile unei perechi."""
        self._pairs[pair] = PairMetric(
            pair=pair, sharpe=sharpe, current_alloc=current_alloc,
            n_trades=n_trades, pnl_usdt=pnl_usdt,
        )

    def remove_pair(self, pair: str) -> None:
        self._pairs.pop(pair, None)

    def update_capital(self, new_capital: float) -> None:
        self.total_capital = new_capital

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def compute_rebalance(self, dry_run: bool = True) -> RebalanceResult:
        """
        Calculeaza noile alocari.
        Daca dry_run=False, actualizeaza current_alloc si salveaza in history.
        """
        ts = datetime.now(timezone.utc).isoformat()

        # Cooldown check
        if not dry_run:
            elapsed = time.monotonic() - self._last_rebalance_ts
            if self._last_rebalance_ts > 0 and elapsed < self.cooldown_s:
                remaining_h = (self.cooldown_s - elapsed) / 3600.0
                return RebalanceResult(
                    timestamp=ts, dry_run=dry_run,
                    total_capital=self.total_capital,
                    allocations={}, changes={}, sharpe_map={},
                    skipped=True,
                    skip_reason=f"Cooldown activ: {remaining_h:.1f}h ramase",
                )

        pairs = list(self._pairs.values())
        if len(pairs) < self.min_pairs:
            return RebalanceResult(
                timestamp=ts, dry_run=dry_run,
                total_capital=self.total_capital,
                allocations={}, changes={}, sharpe_map={},
                skipped=True, skip_reason=f"Prea putine perechi ({len(pairs)} < {self.min_pairs})",
            )

        # Sharpe-proportional allocation
        sharpe_map = {p.pair: max(p.sharpe, 0.01) for p in pairs}  # evita 0/negativ
        total_sharpe = sum(sharpe_map.values())

        min_alloc  = self.total_capital * self.min_alloc_pct
        max_alloc  = self.total_capital * self.max_alloc_pct
        max_total  = self.total_capital * self.max_total_pct

        raw_allocs = {
            p: (s / total_sharpe) * max_total
            for p, s in sharpe_map.items()
        }

        # Clamp
        clamped = {p: max(min_alloc, min(max_alloc, v)) for p, v in raw_allocs.items()}

        # Normalizeaza daca suma > max_total
        total_alloc = sum(clamped.values())
        if total_alloc > max_total:
            scale = max_total / total_alloc
            clamped = {p: v * scale for p, v in clamped.items()}

        # Calculeaza changes
        changes = {
            p: clamped[p] - self._pairs[p].current_alloc
            for p in clamped
        }

        result = RebalanceResult(
            timestamp=ts, dry_run=dry_run,
            total_capital=self.total_capital,
            allocations=clamped,
            changes=changes,
            sharpe_map=sharpe_map,
        )

        if not dry_run:
            # Aplica
            for p, new_alloc in clamped.items():
                self._pairs[p].current_alloc = new_alloc
            self._last_rebalance_ts = time.monotonic()
            self._history.append(result.to_dict())
            logger.info(
                f"[REBALANCER] Aplicat: {len(clamped)} perechi, "
                f"capital={self.total_capital:.0f} USDT"
            )

        return result

    def should_rebalance(self) -> bool:
        """True daca cooldown-ul a expirat si exista date."""
        if not self._pairs:
            return False
        elapsed = time.monotonic() - self._last_rebalance_ts
        return elapsed >= self.cooldown_s

    def history(self, limit: int = 20) -> List[dict]:
        return self._history[-limit:]

    def status(self) -> dict:
        elapsed = time.monotonic() - self._last_rebalance_ts
        next_in_h = max(0.0, (self.cooldown_s - elapsed) / 3600.0)
        return {
            "n_pairs":          len(self._pairs),
            "total_capital":    self.total_capital,
            "last_rebalance":   self._history[-1]["timestamp"] if self._history else None,
            "next_rebalance_h": round(next_in_h, 2),
            "cooldown_h":       self.cooldown_s / 3600.0,
            "min_alloc_pct":    self.min_alloc_pct,
            "max_alloc_pct":    self.max_alloc_pct,
            "n_rebalances":     len(self._history),
            "pairs": [
                {
                    "pair":          m.pair,
                    "sharpe":        round(m.sharpe, 4),
                    "current_alloc": round(m.current_alloc, 2),
                    "pnl_usdt":      round(m.pnl_usdt, 2),
                    "n_trades":      m.n_trades,
                }
                for m in self._pairs.values()
            ],
        }

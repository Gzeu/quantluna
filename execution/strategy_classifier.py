"""
execution/strategy_classifier.py — QuantLuna Strategy Classifier v1.0
Sprint S29 v3.9 — 2026-07-12

La boot, BootScanResult contine TOATE pozitiile din cont.
Acest modul le clasifica in 3 categorii:

  PAIRS_LEG   — face parte dintr-o pereche cointegrata (symbol_y / symbol_x)
                configurata explicit in BybitLiveRunnerConfig
  SOLO_HEDGE  — pozitie singulara pe un simbol care nu e in nicio pereche
                (ex: EGLDUSDT LONG, EGLDUSDT SHORT in hedge mode)
  ORPHAN      — simbol complet necunoscut — nu se atinge, alertă Telegram

Output: ClassifiedBootResult
  .pairs        → list[AdoptedPosition]  — pentru PairsRunner (existent)
  .solo_hedges  → list[SoloHedgeGroup]   — pentru SingleHedgeManager (nou)
  .orphans      → list[OpenPosition]     — alertă, fara actiune

Usage::

    from execution.strategy_classifier import StrategyClassifier
    from execution.position_reconciler import BootScanResult

    classifier = StrategyClassifier(
        pairs=[("BTCUSDT", "ETHUSDT")],   # din config
    )
    classified = classifier.classify(boot_result)
    # classified.solo_hedges → [SoloHedgeGroup(symbol="EGLDUSDT", ...)]
    # classified.pairs       → [AdoptedPosition(...)]
    # classified.orphans     → []
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from loguru import logger

from execution.position_reconciler import AdoptedPosition, BootScanResult, OpenPosition


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SoloHedgeGroup:
    """
    Grup de pozitii pe acelasi simbol solo (pot fi LONG + SHORT in hedge mode,
    sau doar una dintre ele).

    Exemplu EGLD hedge mode Bybit:
        long_leg  = OpenPosition("EGLDUSDT", "long",  qty=10, ...)
        short_leg = OpenPosition("EGLDUSDT", "short", qty=10, ...)

    Exemplu EGLD directional:
        long_leg  = OpenPosition("EGLDUSDT", "long", qty=10, ...)
        short_leg = None
    """
    symbol: str
    long_leg: Optional[OpenPosition] = None
    short_leg: Optional[OpenPosition] = None

    @property
    def is_hedge(self) -> bool:
        """True daca are atat long cat si short deschise (hedge mode Bybit)."""
        return self.long_leg is not None and self.short_leg is not None

    @property
    def net_qty(self) -> float:
        """Cantitate neta (long - short). 0 = perfect hedged."""
        long_qty  = self.long_leg.qty  if self.long_leg  else 0.0
        short_qty = self.short_leg.qty if self.short_leg else 0.0
        return long_qty - short_qty

    @property
    def total_upnl(self) -> float:
        upnl = 0.0
        if self.long_leg:
            upnl += self.long_leg.unrealised_pnl
        if self.short_leg:
            upnl += self.short_leg.unrealised_pnl
        return upnl

    @property
    def dominant_side(self) -> str:
        """Returneaza 'long', 'short' sau 'hedge'."""
        if self.is_hedge:
            return "hedge"
        if self.long_leg:
            return "long"
        return "short"

    def __repr__(self) -> str:
        parts = [f"SoloHedgeGroup({self.symbol} {self.dominant_side}"]
        if self.long_leg:
            parts.append(
                f" LONG qty={self.long_leg.qty:.6f} @ {self.long_leg.entry_price:.4f}"
                f" uPnL={self.long_leg.unrealised_pnl:+.4f}"
            )
        if self.short_leg:
            parts.append(
                f" SHORT qty={self.short_leg.qty:.6f} @ {self.short_leg.entry_price:.4f}"
                f" uPnL={self.short_leg.unrealised_pnl:+.4f}"
            )
        parts.append(f" net={self.net_qty:+.6f} uPnL={self.total_upnl:+.4f})")
        return "".join(parts)


@dataclass
class ClassifiedBootResult:
    """Output complet al clasificarii."""
    pairs: List[AdoptedPosition] = field(default_factory=list)
    solo_hedges: List[SoloHedgeGroup] = field(default_factory=list)
    orphans: List[OpenPosition] = field(default_factory=list)

    def log_summary(self) -> None:
        logger.info(
            "StrategyClassifier: %d perechi | %d solo_hedges | %d orphans",
            len(self.pairs), len(self.solo_hedges), len(self.orphans),
        )
        for sg in self.solo_hedges:
            logger.info("  SoloHedge: %s", sg)
        for op in self.orphans:
            logger.warning(
                "  ORPHAN (neatins): %s %s qty=%.6f uPnL=%+.4f",
                op.symbol, op.side, op.qty, op.unrealised_pnl,
            )

    def to_telegram_msg(self) -> str:
        lines = []
        if self.solo_hedges:
            lines.append(f"\U0001f9e9 *Solo Hedges detectate: {len(self.solo_hedges)}*")
            for sg in self.solo_hedges:
                mode = "HEDGE" if sg.is_hedge else sg.dominant_side.upper()
                lines.append(
                    f"  \u2699\ufe0f `{sg.symbol}` {mode} "
                    f"net=`{sg.net_qty:+.4f}` uPnL=`{sg.total_upnl:+.2f}`"
                )
        if self.orphans:
            lines.append(f"\u26a0\ufe0f *Pozitii ORFANE ({len(self.orphans)}) — netinute:*")
            for op in self.orphans:
                lines.append(
                    f"  \u2753 `{op.symbol}` {op.side.upper()} "
                    f"qty=`{op.qty:.6f}` uPnL=`{op.unrealised_pnl:+.2f}`"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# StrategyClassifier
# ---------------------------------------------------------------------------

class StrategyClassifier:
    """
    Clasifica pozitiile dintr-un BootScanResult in 3 categorii.

    Parameters
    ----------
    pairs : list of (symbol_y, symbol_x) tuples
        Perechile configurate explicit (din BybitLiveRunnerConfig sau env vars).
        Orice simbol care apare intr-o pereche este exclus din solo_hedges.

    Exemplu
    -------
    classifier = StrategyClassifier(pairs=[("BTCUSDT", "ETHUSDT")])
    result = classifier.classify(boot_scan_result)
    """

    def __init__(self, pairs: List[Tuple[str, str]]) -> None:
        self._pairs = pairs
        # Set cu TOATE simbolurile din perechi configurate
        self._pair_symbols: set[str] = {
            sym for pair in pairs for sym in pair
        }

    def classify(self, boot_result: BootScanResult) -> ClassifiedBootResult:
        """
        Clasifica toate pozitiile din BootScanResult.

        Returns
        -------
        ClassifiedBootResult cu pairs, solo_hedges, orphans populate.
        """
        output = ClassifiedBootResult()

        # 1. Perechi configurate — adoptia e deja facuta in boot_result.adopted
        if boot_result.adopted and boot_result.adopted.has_position:
            output.pairs.append(boot_result.adopted)

        # 2. Pozitii care NU fac parte din perechile configurate
        non_pair_positions = [
            pos for pos in boot_result.positions
            if pos.symbol not in self._pair_symbols
        ]

        # Grupeaza pe simbol (pentru hedge mode: acelasi simbol poate
        # aparea de 2 ori — o data LONG, o data SHORT)
        symbol_groups: dict[str, SoloHedgeGroup] = {}
        for pos in non_pair_positions:
            if pos.symbol not in symbol_groups:
                symbol_groups[pos.symbol] = SoloHedgeGroup(symbol=pos.symbol)
            group = symbol_groups[pos.symbol]
            if pos.side == "long":
                if group.long_leg is not None:
                    logger.warning(
                        "StrategyClassifier: 2 legi LONG detectate pt %s — pastreaza prima",
                        pos.symbol,
                    )
                else:
                    group.long_leg = pos
            elif pos.side == "short":
                if group.short_leg is not None:
                    logger.warning(
                        "StrategyClassifier: 2 legi SHORT detectate pt %s — pastreaza prima",
                        pos.symbol,
                    )
                else:
                    group.short_leg = pos
            else:
                # side necunoscut — trateaza ca orfan
                output.orphans.append(pos)
                continue

        # 3. Separa solo_hedges de orfani
        # Criteriu orfan: simbol cu side=none sau qty=0 dupa parsare
        # Toate grupurile valide merg in solo_hedges
        for symbol, group in symbol_groups.items():
            has_valid_leg = (
                (group.long_leg is not None and group.long_leg.qty > 0)
                or (group.short_leg is not None and group.short_leg.qty > 0)
            )
            if has_valid_leg:
                output.solo_hedges.append(group)
                logger.info(
                    "StrategyClassifier: %s → SoloHedge (%s)",
                    symbol, group.dominant_side,
                )
            else:
                # Nu ar trebui sa ajunga aici (qty=0 filtrat in scan_all_positions)
                logger.debug(
                    "StrategyClassifier: %s ignorat (qty=0)", symbol
                )

        output.log_summary()
        return output

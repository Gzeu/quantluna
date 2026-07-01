"""
execution/position_scanner.py  —  QuantLuna Orphan Position Scanner

Scop:
  La startup (sau la cerere), scanează TOATE pozițiile deschise de pe exchange
  şi le clasifică:
    MANAGED   — găsite şi în checkpoint (deja gestionate de bot)
    ORPHAN    — deschise pe cont dar fără checkpoint (create manual sau de alt bot)
    STALE     — în checkpoint dar nu pe exchange (deja închise extern)

For ORPHAN positions:
  - Calculează PnL curent
  - Estimează hedge ratio implicat
  - Propune adopt sau close imediat

Usage:
    scanner = PositionScanner(exchange, checkpoint)
    report  = await scanner.scan()
    for p in report.orphans:
        print(p.symbol, p.side, p.qty, p.unrealized_pnl)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from execution.checkpoint import PositionCheckpoint

logger = logging.getLogger(__name__)


@dataclass
class ExchangePosition:
    symbol: str
    side: str           # 'long' sau 'short'
    qty: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float
    notional_usdt: float
    liquidation_price: float
    margin_used: float
    raw: dict = field(default_factory=dict)

    @property
    def pnl_pct(self) -> float:
        if self.notional_usdt == 0:
            return 0.0
        return self.unrealized_pnl / self.notional_usdt

    @property
    def distance_to_liq_pct(self) -> float:
        """Distanța relativă până la lichidare (pozitiv = safe)."""
        if self.liquidation_price <= 0 or self.mark_price <= 0:
            return 999.0
        if self.side == 'long':
            return (self.mark_price - self.liquidation_price) / self.mark_price
        else:
            return (self.liquidation_price - self.mark_price) / self.mark_price


@dataclass
class ScanReport:
    managed: List[ExchangePosition] = field(default_factory=list)
    orphans: List[ExchangePosition] = field(default_factory=list)
    stale_checkpoints: List[str]    = field(default_factory=list)  # simboluri
    total_orphan_pnl: float         = 0.0
    total_orphan_notional: float    = 0.0
    scan_error: Optional[str]       = None

    @property
    def has_orphans(self) -> bool:
        return len(self.orphans) > 0

    def summary(self) -> str:
        return (
            f"Scan: {len(self.managed)} gestionate, "
            f"{len(self.orphans)} orfane (PnL={self.total_orphan_pnl:+.2f} USDT), "
            f"{len(self.stale_checkpoints)} checkpoint-uri stale"
        )


class PositionScanner:
    """
    Scanează pozițiile de pe exchange şi le clasifică față de checkpoint.

    Args:
        exchange:    ccxt async exchange instance (fetch_positions)
        checkpoint:  PositionCheckpoint instance
        min_notional: ignoră poziții sub această valoare (dust)
    """

    def __init__(
        self,
        exchange,
        checkpoint: PositionCheckpoint,
        min_notional: float = 1.0,
    ) -> None:
        self._exchange   = exchange
        self._cp         = checkpoint
        self._min_notional = min_notional

    async def scan(self) -> ScanReport:
        """
        Execută scanarea completă. Returnează ScanReport.
        """
        report = ScanReport()

        # 1. Fetch toate pozițiile de pe exchange
        try:
            raw_positions = await self._exchange.fetch_positions()
        except Exception as exc:
            err = f"fetch_positions() failed: {exc}"
            logger.error(f"[Scanner] {err}")
            report.scan_error = err
            return report

        # Filtrare poziții reale (qty > 0)
        active = [
            p for p in raw_positions
            if abs(p.get('contracts', 0) or 0) > 0
            and abs(p.get('notional', 0) or 0) >= self._min_notional
        ]

        # 2. Încarcă checkpoint-ul curent
        cp_state = self._cp.load()
        cp_symbols: set[str] = set()
        if cp_state:
            cp_symbols.add(cp_state.sym_y.upper())
            cp_symbols.add(cp_state.sym_x.upper())

        # 3. Clasifică fiecare poziție
        seen_symbols: set[str] = set()
        for raw in active:
            ep = self._parse_position(raw)
            if ep is None:
                continue
            seen_symbols.add(ep.symbol.upper())
            base = ep.symbol.split('/')[0].upper()

            is_managed = (
                ep.symbol.upper() in cp_symbols
                or base in cp_symbols
            )

            if is_managed:
                report.managed.append(ep)
                logger.info(
                    f"[Scanner] MANAGED: {ep.symbol} {ep.side} "
                    f"qty={ep.qty:.4f} PnL={ep.unrealized_pnl:+.2f}"
                )
            else:
                report.orphans.append(ep)
                report.total_orphan_pnl     += ep.unrealized_pnl
                report.total_orphan_notional += ep.notional_usdt
                logger.warning(
                    f"[Scanner] ORPHAN: {ep.symbol} {ep.side} "
                    f"qty={ep.qty:.4f} entry={ep.entry_price:.4f} "
                    f"mark={ep.mark_price:.4f} PnL={ep.unrealized_pnl:+.2f} "
                    f"dist_liq={ep.distance_to_liq_pct:.1%}"
                )

        # 4. Detectează checkpoint-uri stale
        if cp_state:
            for sym in cp_symbols:
                if sym not in seen_symbols:
                    report.stale_checkpoints.append(sym)
                    logger.warning(
                        f"[Scanner] STALE checkpoint: {sym} nu mai e pe exchange"
                    )

        logger.info(f"[Scanner] {report.summary()}")
        return report

    def _parse_position(self, raw: dict) -> Optional[ExchangePosition]:
        try:
            qty = abs(float(raw.get('contracts', 0) or 0))
            if qty < 1e-8:
                return None

            side = raw.get('side', '').lower()
            if side not in ('long', 'short'):
                # fallback din 'info'
                info = raw.get('info', {})
                side_raw = str(info.get('side', info.get('posSide', 'long'))).lower()
                side = 'long' if 'long' in side_raw or 'buy' in side_raw else 'short'

            entry  = float(raw.get('entryPrice', 0) or 0)
            mark   = float(raw.get('markPrice', 0) or entry)
            notional = float(raw.get('notional', qty * mark) or (qty * mark))
            upnl   = float(raw.get('unrealizedPnl', 0) or 0)
            lev    = float(raw.get('leverage', 1) or 1)
            liqp   = float(raw.get('liquidationPrice', 0) or 0)
            margin = float(raw.get('initialMargin', 0) or raw.get('margin', 0) or 0)

            return ExchangePosition(
                symbol=raw.get('symbol', ''),
                side=side,
                qty=qty,
                entry_price=entry,
                mark_price=mark,
                unrealized_pnl=upnl,
                leverage=lev,
                notional_usdt=abs(notional),
                liquidation_price=liqp,
                margin_used=margin,
                raw=raw,
            )
        except Exception as exc:
            logger.warning(f"[Scanner] parse_position failed: {exc} | raw={raw}")
            return None

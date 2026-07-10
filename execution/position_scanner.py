"""
QuantLuna — Position Scanner (Sprint 17)

Scans exchange positions and classifies them as MANAGED (known to QuantLuna
via checkpoint) or ORPHAN (opened externally or after a crash).

Used by AdoptionEngine to decide what to do with orphan positions.

Usage:
    scanner = PositionScanner(exchange, checkpoint)
    report  = await scanner.scan()
    for pos in report.orphans:
        # handle orphan
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class ExchangePosition:
    Parsed representation of a single exchange position.
    symbol: str
    side: str
    qty: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float
    notional_usdt: float
    liquidation_price: float
    margin_used: float

    @property
    def pnl_pct(self) -> float:
        Unrealised PnL as fraction of notional.
        if self.notional_usdt == 0:
            return 0.0
        return self.unrealized_pnl / self.notional_usdt

    @property
    def distance_to_liq_pct(self) -> float:
        Distance from current mark price to liquidation price as fraction.
        if self.mark_price == 0:
            return 1.0
        if self.side == "long":
            return (self.mark_price - self.liquidation_price) / self.mark_price
        else:
            return (self.liquidation_price - self.mark_price) / self.mark_price


@dataclass
class ScanReport:
    Result of a single position scan.
    managed: List[ExchangePosition] = field(default_factory=list)
    orphans: List[ExchangePosition] = field(default_factory=list)
    scan_error: Optional[str] = None

    @property
    def has_orphans(self) -> bool:
        return len(self.orphans) > 0

    def summary(self) -> str:
        Return a summary string of the scan report.
        return f"managed={len(self.managed)} orphans={len(self.orphans)}"


class PositionScanner:
    Compares exchange positions against the local checkpoint to find orphans.

    Parameters
    ----------
    exchange   : async CCXT exchange object (must have fetch_positions)
    checkpoint : checkpoint object with .load(symbol) method
    min_notional: minimum notional value to consider a position valid

    def __init__(self, exchange: Any, checkpoint: Any, min_notional: float = 0.0) -> None:
        self._exchange = exchange
        self._checkpoint = checkpoint
        self._min_notional = min_notional

    async def scan(self) -> ScanReport:
        Fetch all open positions and classify each one.
        report = ScanReport()
        try:
            raw_positions = await self._exchange.fetch_positions()
        except Exception as exc:
            report.scan_error = str(exc)
            logger.error(f"PositionScanner: fetch_positions failed: {exc}")
            return report

        cp_state = self._checkpoint.load()

        for raw in raw_positions:
            pos = self._parse_position(raw)
            if pos is None:
                continue
            if cp_state is not None:
                is_managed = (pos.symbol == cp_state.sym_y or pos.symbol == cp_state.sym_x)
            else:
                is_managed = False

            if is_managed:
                report.managed.append(pos)
            else:
                report.orphans.append(pos)
                logger.warning(f"PositionScanner: ORPHAN detected: {pos.symbol} side={pos.side} qty={pos.qty}")

        logger.info(
            f"PositionScanner: managed={len(report.managed)} orphans={len(report.orphans)}"
        )
        return report

    def _parse_position(self, raw: Dict) -> Optional[ExchangePosition]:
        Parse a raw CCXT position dict into ExchangePosition. Returns None if invalid.
        try:
            qty = float(raw.get("contracts") or raw.get("size") or 0)
            if qty == 0:
                return None
            symbol = raw.get("symbol") or raw.get("info", {}).get("symbol", "")
            side = (raw.get("side") or "long").lower()
            if side not in ("long", "short"):
                side = "long"
            return ExchangePosition(
                symbol=symbol,
                side=side,
                qty=qty,
                entry_price=float(raw.get("entryPrice") or raw.get("avgPrice") or 0),
                mark_price=float(raw.get("markPrice") or 0),
                unrealized_pnl=float(raw.get("unrealizedPnl") or raw.get("unrealisedPnl") or 0),
                leverage=float(raw.get("leverage") or 1),
                notional_usdt=float(raw.get("notional") or 0),
                liquidation_price=float(raw.get("liquidationPrice") or 0),
                margin_used=float(raw.get("initialMargin") or 0),
            )
        except Exception as exc:
            logger.warning(f"PositionScanner: parse error: {exc} raw={raw}")
            return None
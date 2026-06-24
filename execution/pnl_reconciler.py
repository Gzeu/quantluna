"""
QuantLuna — PnLReconciler Sprint 6

Reconciliere open P&L: compară valoarea calculată local (mark price WS)
cu pozițiile reale returnate de fetch_position() pe exchange.

Scopul principal: detectare drift înainte ca acesta să devină o problemă
operațională (execuție parțială, fee neraportate, liquidare parțială,
network gap în WebSocket price feed).

Design:
- Rulează ca asyncio.Task, polling la interval configurabil
- Fetch fetch_positions() via CCXT pentru ambele legs
- Calculează realized + unrealized PnL conform exchange-ului
- Compară cu StateSnapshot.open_pnl_usd (calculat local din mark price)
- Dacă |drift| > drift_alert_usd: publică alert în StateBus + log warning
- Publică reconciled_open_pnl, position_size_y, position_size_x în bus

Limitări / Riscuri reale:
- fetch_positions() are latență REST (50-200ms), nu e timp real
- La rollover de funding sau execuție de fee, pnl de pe exchange poate
  sări brusc; nu interpreta ca eroare de calcul local
- Dacă WS price feed e stale > 10s, driftul va fi artificial mare;
  FundingMonitor și LiveTrader ar trebui să detecteze WS stale separat
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional

import ccxt.async_support as ccxt
from loguru import logger


@dataclass
class ReconcilerConfig:
    sym_y: str                      # e.g. "ETH/USDT:USDT"
    sym_x: str                      # e.g. "BTC/USDT:USDT"
    poll_interval_s: float = 30.0   # seconds between reconciliation cycles
    drift_alert_usd: float = 5.0    # USD drift threshold for warning
    exchange_id: str = "bybit"
    testnet: bool = False


@dataclass
class ReconciliationResult:
    exchange_open_pnl: float = 0.0
    local_open_pnl: float = 0.0
    drift_usd: float = 0.0
    position_size_y: float = 0.0
    position_size_x: float = 0.0
    entry_price_y: float = 0.0
    entry_price_x: float = 0.0
    alert: bool = False
    error: Optional[str] = None


class PnLReconciler:
    """
    Async polling reconciler pentru open P&L.

    Usage:
        reconciler = PnLReconciler(cfg, exchange, bus)
        task = asyncio.create_task(reconciler.run())
    """

    def __init__(self, cfg: ReconcilerConfig, exchange: ccxt.Exchange, bus) -> None:
        self.cfg = cfg
        self.exchange = exchange
        self.bus = bus
        self._last_result: ReconciliationResult = ReconciliationResult()

    async def run(self) -> None:
        """Main reconciliation loop."""
        logger.info(
            f"PnLReconciler started — {self.cfg.sym_y} / {self.cfg.sym_x} "
            f"poll={self.cfg.poll_interval_s}s drift_alert=${self.cfg.drift_alert_usd}"
        )
        while True:
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                logger.info("PnLReconciler stopped (cancelled)")
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"PnLReconciler error: {exc}")
            await asyncio.sleep(self.cfg.poll_interval_s)

    async def _reconcile(self) -> None:
        """Single reconciliation cycle."""
        result = ReconciliationResult()

        try:
            positions = await self.exchange.fetch_positions(
                [self.cfg.sym_y, self.cfg.sym_x]
            )
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            logger.warning(f"fetch_positions failed: {exc}")
            self._publish(result)
            return

        pos_map: Dict[str, dict] = {}
        for p in positions:
            sym = p.get("symbol", "")
            if sym in (self.cfg.sym_y, self.cfg.sym_x):
                pos_map[sym] = p

        pos_y = pos_map.get(self.cfg.sym_y, {})
        pos_x = pos_map.get(self.cfg.sym_x, {})

        result.position_size_y = float(pos_y.get("contracts", 0.0) or 0.0)
        result.position_size_x = float(pos_x.get("contracts", 0.0) or 0.0)
        result.entry_price_y   = float(pos_y.get("entryPrice", 0.0) or 0.0)
        result.entry_price_x   = float(pos_x.get("entryPrice", 0.0) or 0.0)

        # unrealizedPnl = exchange calcul (mark price vs entry, include funding acumulat)
        unrealized_y = float(pos_y.get("unrealizedPnl", 0.0) or 0.0)
        unrealized_x = float(pos_x.get("unrealizedPnl", 0.0) or 0.0)
        result.exchange_open_pnl = unrealized_y + unrealized_x

        # local open P&L din StateSnapshot
        snapshot = self.bus.snapshot()
        result.local_open_pnl = getattr(snapshot, "open_pnl_usd", 0.0)

        result.drift_usd = abs(result.exchange_open_pnl - result.local_open_pnl)
        result.alert = result.drift_usd > self.cfg.drift_alert_usd

        if result.alert:
            logger.warning(
                f"P&L DRIFT ALERT: exchange={result.exchange_open_pnl:.2f} "
                f"local={result.local_open_pnl:.2f} "
                f"drift=${result.drift_usd:.2f} (threshold=${self.cfg.drift_alert_usd})"
            )

        self._last_result = result
        self._publish(result)

    def _publish(self, result: ReconciliationResult) -> None:
        """Push reconciliation data to StateBus."""
        self.bus.update({
            "reconciled_open_pnl": result.exchange_open_pnl,
            "pnl_drift_usd": result.drift_usd,
            "pnl_drift_alert": result.alert,
            "position_size_y": result.position_size_y,
            "position_size_x": result.position_size_x,
            "entry_price_y": result.entry_price_y,
            "entry_price_x": result.entry_price_x,
        })

    @property
    def last_result(self) -> ReconciliationResult:
        return self._last_result

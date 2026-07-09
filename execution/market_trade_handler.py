"""
execution/market_trade_handler.py  —  QuantLuna Market Trade Handler (Sprint 28)

Problema rezolvată:
  Când un trade este deschis din afara botului (manual, alt script, API extern)
  pe exchange, botul trebuie să:
    1. Detecteze poziția nouă (via polling sau WebSocket)
    2. O adopte → seteze SL + TP via AdoptionEngine
    3. Monitorizeze închiderea (fill TP/SL sau close extern)
    4. Pornească automat un nou ciclu de tranzacționare după close

Flux complet
------------

  Exchange WS / REST poll
        |
        v
  MarketTradeHandler._scan_loop()
        |
        v  (poziție nouă detectată, nu e a botului)
  AdoptionEngine.adopt_and_protect(position)
        |
        +---> OrderManager.submit(tp_req)  →  on_fill → _trigger_restart
        +---> OrderManager.submit(sl_req)  →  on_fill → _trigger_restart
        |
        v  (sau close extern detectat de _monitor_adopted_loop)
  ResumeManager.restart_after_external_close(symbol, on_cycle_restart)
        |
        v
  on_cycle_restart(symbol)  — callback furnizat de LiveTrader / BytbitLiveRunner

Usage
-----
    handler = MarketTradeHandler(
        exchange=ccxt_bybit,
        order_manager=order_manager,
        checkpoint=checkpoint,
        adoption_config=AdoptionConfig(restart_cooldown_s=15.0),
        on_cycle_restart=live_trader.start_new_cycle,
        venue="bybit",
        poll_interval_s=5.0,
    )
    await handler.start()
    # ... later
    await handler.stop()
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from loguru import logger

from execution.adoption_engine import AdoptionConfig, AdoptionDecision, AdoptionEngine
from execution.order_manager import OrderManager
from execution.resume_manager import ResumeManager


@dataclass
class MarketTradeHandlerConfig:
    # Seconds between position polls
    poll_interval_s: float = 5.0
    # Seconds between checks for already-adopted positions being closed
    monitor_interval_s: float = 10.0
    # Minimum USD notional to consider a position worth adopting
    min_notional_usdt: float = 5.0
    # Venue name used when creating OrderRequests
    venue: str = "bybit"
    # Whether to log every poll (verbose)
    verbose: bool = False


@dataclass
class _AdoptedEntry:
    """Internal record of an adopted position being monitored."""
    symbol: str
    side: str
    qty: float
    entry_price: float
    adopted_at: float = field(default_factory=time.time)
    tp_local_id: Optional[str] = None
    sl_local_id: Optional[str] = None


class MarketTradeHandler:
    """
    Polls exchange positions at regular intervals, adopts any external trades,
    places protection orders, and restarts the trading cycle on close.

    Parameters
    ----------
    exchange         : authenticated async CCXT exchange instance
    order_manager    : OrderManager (already has routers registered)
    checkpoint       : PositionCheckpoint
    adoption_config  : AdoptionConfig (tp/sl pcts, restart cooldown, etc.)
    alert_cfg        : optional AlertConfig for notifications
    on_cycle_restart : async callable(symbol: str) — called when a position closes
                       and a fresh cycle should start. Typically
                       live_trader.start_new_cycle or bybit_runner.trigger_entry
    venue            : exchange venue name for order routing
    poll_interval_s  : how often to poll positions (seconds)
    monitor_interval_s: how often to check adopted positions for external close
    symbols          : optional list of symbols to watch; if None, watches all
    """

    def __init__(
        self,
        exchange: Any,
        order_manager: OrderManager,
        checkpoint: Any,
        adoption_config: Optional[AdoptionConfig] = None,
        alert_cfg: Any = None,
        on_cycle_restart: Optional[Callable[[str], Coroutine]] = None,
        venue: str = "bybit",
        poll_interval_s: float = 5.0,
        monitor_interval_s: float = 10.0,
        symbols: Optional[List[str]] = None,
    ) -> None:
        self._exchange    = exchange
        self._om          = order_manager
        self._checkpoint  = checkpoint
        self._alert_cfg   = alert_cfg
        self._restart_cb  = on_cycle_restart
        self._venue       = venue
        self._poll_s      = poll_interval_s
        self._monitor_s   = monitor_interval_s
        self._symbols     = symbols  # None = watch all

        cfg = adoption_config or AdoptionConfig()
        self._adoption = AdoptionEngine(
            exchange=exchange,
            checkpoint=checkpoint,
            order_manager=order_manager,
            config=cfg,
            on_cycle_restart=self._handle_cycle_restart,
        )
        self._resume = ResumeManager(
            checkpoint=checkpoint,
            exchange=exchange,
            alert_cfg=alert_cfg,
        )

        # Symbols currently managed by the bot (its own orders)
        # populated by register_bot_symbol() so we don't adopt our own positions
        self._bot_symbols: Set[str] = set()

        # Adopted positions currently being monitored for external close
        self._adopted: Dict[str, _AdoptedEntry] = {}  # symbol → entry

        self._running = False
        self._tasks: List[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Bot symbol registry — avoid re-adopting own positions
    # ------------------------------------------------------------------

    def register_bot_symbol(self, symbol: str) -> None:
        """Tell the handler that `symbol` is managed by the bot itself."""
        self._bot_symbols.add(symbol.upper())

    def unregister_bot_symbol(self, symbol: str) -> None:
        self._bot_symbols.discard(symbol.upper())

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start polling and monitoring loops."""
        if self._running:
            logger.warning("MarketTradeHandler: already running")
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._scan_loop(),    name="mth_scan"),
            asyncio.create_task(self._monitor_loop(), name="mth_monitor"),
        ]
        logger.info(
            f"MarketTradeHandler: started | venue={self._venue} "
            f"poll={self._poll_s}s monitor={self._monitor_s}s"
        )

    async def stop(self) -> None:
        """Stop all background tasks."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("MarketTradeHandler: stopped")

    # ------------------------------------------------------------------
    # Main scan loop — detect external positions
    # ------------------------------------------------------------------

    async def _scan_loop(self) -> None:
        """
        Periodically fetch open positions from the exchange.
        For each position NOT managed by the bot → adopt + protect.
        """
        while self._running:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"MarketTradeHandler._scan_loop error: {exc}")
            await asyncio.sleep(self._poll_s)

    async def _scan_once(self) -> None:
        """Single position-scan iteration."""
        try:
            all_positions = await self._exchange.fetch_positions(self._symbols)
        except Exception as exc:
            logger.warning(f"MarketTradeHandler: fetch_positions failed: {exc}")
            return

        for pos_raw in all_positions:
            qty = abs(float(pos_raw.get("contracts", 0) or 0))
            if qty < 1e-8:
                continue  # no open position

            symbol = pos_raw.get("symbol", "").upper()
            notional = abs(float(pos_raw.get("notional", 0) or qty * float(pos_raw.get("markPrice", 0) or 0)))

            if notional < self._adoption.cfg.min_notional_adopt:
                continue  # too small, skip

            if symbol in self._bot_symbols:
                continue  # our own position, skip

            if symbol in self._adopted:
                continue  # already adopted

            # External position detected — adopt it
            side_raw = pos_raw.get("side", "").lower()
            side = "long" if side_raw in ("long", "buy") else "short"
            entry_price = float(
                pos_raw.get("entryPrice")
                or pos_raw.get("entry_price")
                or pos_raw.get("averagePrice")
                or 0.0
            )
            pnl_pct = float(pos_raw.get("percentage", 0) or 0) / 100.0
            liq_price = float(pos_raw.get("liquidationPrice", 0) or 0)
            distance_to_liq = (
                abs(entry_price - liq_price) / entry_price
                if entry_price > 0 and liq_price > 0
                else 1.0
            )

            position_dict = {
                "symbol":               symbol,
                "side":                 side,
                "qty":                  qty,
                "entry_price":          entry_price,
                "notional_usdt":        notional,
                "pnl_pct":              pnl_pct,
                "distance_to_liq_pct": distance_to_liq,
            }

            logger.info(
                f"MarketTradeHandler: external position detected "
                f"{symbol} {side} qty={qty:.4f} entry={entry_price:.4f} "
                f"notional=${notional:.2f} — adopting"
            )

            try:
                result = await self._adoption.adopt_and_protect(
                    position=position_dict,
                    venue=self._venue,
                )
                if result.decision == AdoptionDecision.ADOPT:
                    self._adopted[symbol] = _AdoptedEntry(
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        entry_price=entry_price,
                        tp_local_id=result.tp_local_id,
                        sl_local_id=result.sl_local_id,
                    )
                    logger.info(
                        f"MarketTradeHandler: adopted {symbol} "
                        f"tp_id={result.tp_local_id} sl_id={result.sl_local_id}"
                    )
                elif result.decision == AdoptionDecision.CLOSE_NOW:
                    logger.warning(
                        f"MarketTradeHandler: position {symbol} closed immediately: {result.reason}"
                    )
                # MONITOR_ONLY: no action needed
            except Exception as exc:
                logger.error(f"MarketTradeHandler: adopt_and_protect failed for {symbol}: {exc}")

    # ------------------------------------------------------------------
    # Monitor loop — detect external close of adopted positions
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        """
        Periodically checks if adopted positions have been closed externally
        (i.e. position no longer exists on exchange but our TP/SL orders
        are still open / cancelled without fill).
        """
        while self._running:
            try:
                await self._monitor_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"MarketTradeHandler._monitor_loop error: {exc}")
            await asyncio.sleep(self._monitor_s)

    async def _monitor_once(self) -> None:
        """Single monitoring iteration for adopted positions."""
        if not self._adopted:
            return

        closed_symbols: List[str] = []

        for symbol, entry in list(self._adopted.items()):
            try:
                positions = await self._exchange.fetch_positions([symbol])
            except Exception as exc:
                logger.warning(f"MarketTradeHandler: fetch_positions({symbol}) failed: {exc}")
                continue

            qty_on_exchange = 0.0
            for p in positions:
                sym = p.get("symbol", "").upper()
                if symbol in sym:
                    qty_on_exchange = abs(float(p.get("contracts", 0) or 0))
                    break

            if qty_on_exchange < 1e-8:
                # Position gone — closed externally (SL hit, manual, liquidation)
                logger.info(
                    f"MarketTradeHandler: adopted position {symbol} no longer on exchange "
                    f"— triggering restart (external close)"
                )
                closed_symbols.append(symbol)

                # Cancel any still-open TP/SL orders to avoid orphan orders
                for lid in (entry.tp_local_id, entry.sl_local_id):
                    if lid:
                        status = self._om.get_status(lid)
                        from execution.order_manager import OrderStatus
                        if status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY):
                            await self._om.cancel(lid)
                            logger.debug(f"MarketTradeHandler: cancelled orphan order {lid}")

                # Trigger restart via ResumeManager
                await self._resume.restart_after_external_close(
                    symbol=symbol,
                    on_cycle_restart=self._handle_cycle_restart,
                    cooldown_s=self._adoption.cfg.restart_cooldown_s,
                    alert_msg=(
                        f"[MarketTradeHandler] Poziție adoptată {symbol} închisă extern — "
                        f"restart ciclu în {self._adoption.cfg.restart_cooldown_s}s"
                    ),
                )

        for symbol in closed_symbols:
            self._adopted.pop(symbol, None)

    # ------------------------------------------------------------------
    # Cycle restart handler
    # ------------------------------------------------------------------

    async def _handle_cycle_restart(self, symbol: str) -> None:
        """
        Internal bridge: removes symbol from adopted dict and calls
        the user-supplied on_cycle_restart callback.
        """
        self._adopted.pop(symbol, None)

        if self._restart_cb is not None:
            logger.info(f"MarketTradeHandler: calling on_cycle_restart for {symbol}")
            try:
                await self._restart_cb(symbol)
            except Exception as exc:
                logger.error(f"MarketTradeHandler: on_cycle_restart({symbol}) failed: {exc}")
        else:
            logger.warning(
                f"MarketTradeHandler: no on_cycle_restart registered for {symbol} "
                f"— set on_cycle_restart= at init to auto-restart."
            )

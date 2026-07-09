"""
QuantLuna — MarketTradeHandler (Sprint 28 rev-2)

Rev-2 changes vs rev-1:
  - REMOVED: REST polling loop (_scan_loop / _scan_once)
  - ADDED:   BybitPrivateWS push stream for position + order + execution events
    Latency: REST poll ~5 000 ms  →  WS push ~50–150 ms  (-98%)
  - Fallback: if BybitPrivateWS is unavailable (no creds) the handler
    silently skips adoption (safe in dry/paper mode)
  - _monitor_loop kept: detects external close of adopted positions
    by checking if the WS position event shows size == 0

Flow:
    BybitPrivateWS  →  on_position handler
            │  (external position detected)
            ▼
    AdoptionEngine.adopt_and_protect()
            │
            ├─ OrderManager.submit(tp)  →  on_fill → _trigger_restart
            └─ OrderManager.submit(sl)  →  on_fill → _trigger_restart

    BybitPrivateWS  →  on_position handler  (size == 0)
            │  (position closed externally)
            ▼
    ResumeManager.restart_after_external_close(symbol, on_cycle_restart)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, Optional, Set

from loguru import logger


class MarketTradeHandler:
    """
    Detects external (market) positions via BybitPrivateWS position stream,
    adopts them (TP/SL), and triggers cycle restart on close.

    Parameters
    ----------
    private_ws        : BybitPrivateWS instance (already constructed, not started)
    order_manager     : OrderManager instance
    checkpoint        : Checkpoint instance
    adoption_config   : AdoptionConfig (tp/sl %)
    alert_cfg         : NotifierBus (optional)
    on_cycle_restart  : async callable(symbol: str)
    venue             : exchange venue label
    monitor_interval_s: seconds between orphan-order cleanup checks
    symbols           : list of symbols to watch (empty = watch all)
    """

    def __init__(
        self,
        private_ws=None,
        order_manager=None,
        checkpoint=None,
        adoption_config=None,
        alert_cfg=None,
        on_cycle_restart: Optional[Callable[[str], Coroutine]] = None,
        venue: str = "bybit",
        monitor_interval_s: float = 10.0,
        symbols: Optional[list] = None,
        # legacy compat: ignored in v2
        exchange=None,
        poll_interval_s: float = 5.0,
    ) -> None:
        self._private_ws       = private_ws
        self._order_manager    = order_manager
        self._checkpoint       = checkpoint
        self._adoption_config  = adoption_config
        self._alert_cfg        = alert_cfg
        self._on_cycle_restart = on_cycle_restart
        self._venue            = venue
        self._monitor_interval = monitor_interval_s
        self._watch_symbols: Set[str] = set(symbols or [])

        self._bot_symbols: Set[str] = set()
        self._adopted: Dict[str, dict] = {}
        self._ws_position_sizes: Dict[str, float] = {}

        self._running       = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._ws_task:      Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Bot symbol registry
    # ------------------------------------------------------------------

    def register_bot_symbol(self, symbol: str) -> None:
        self._bot_symbols.add(symbol)

    def unregister_bot_symbol(self, symbol: str) -> None:
        self._bot_symbols.discard(symbol)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        logger.info("MarketTradeHandler v2: starting (WS-based, no REST poll)")

        if self._private_ws is not None:
            self._private_ws.on_position(self._on_ws_position)
            self._private_ws.on_execution(self._on_ws_execution)
            self._ws_task = asyncio.create_task(
                self._private_ws.start(), name="market_trade_handler_ws"
            )
            logger.info("MarketTradeHandler: BybitPrivateWS stream started")
        else:
            logger.warning(
                "MarketTradeHandler: no BybitPrivateWS injected — "
                "external trade detection DISABLED (safe in paper/dry mode)"
            )

        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="market_trade_monitor"
        )

        tasks = [t for t in (self._ws_task, self._monitor_task) if t is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._running = False
        if self._private_ws is not None:
            try:
                await self._private_ws.stop()
            except Exception:
                pass
        for task in (self._ws_task, self._monitor_task):
            if task and not task.done():
                task.cancel()
        logger.info("MarketTradeHandler: stopped")

    # ------------------------------------------------------------------
    # WS callbacks
    # ------------------------------------------------------------------

    async def _on_ws_position(self, msg: dict) -> None:
        """
        Called on every 'position' WS event from Bybit private stream.
        Payload: {topic: 'position', data: [{symbol, size, side, ...}]}
        """
        data_list = msg.get("data", [])
        if not isinstance(data_list, list):
            data_list = [data_list]

        for pos_data in data_list:
            symbol = pos_data.get("symbol", "")
            if self._watch_symbols and symbol not in self._watch_symbols:
                continue

            try:
                size = float(pos_data.get("size", 0) or 0)
            except (ValueError, TypeError):
                size = 0.0

            prev_size = self._ws_position_sizes.get(symbol, 0.0)
            self._ws_position_sizes[symbol] = size

            # Position opened externally
            if size > 0 and prev_size == 0.0 and symbol not in self._bot_symbols:
                logger.info(
                    f"MarketTradeHandler: external position detected via WS — "
                    f"{symbol} size={size} side={pos_data.get('side')}"
                )
                await self._adopt_position(symbol, pos_data)

            # Position closed while we have it adopted
            elif size == 0.0 and prev_size > 0 and symbol in self._adopted:
                logger.info(
                    f"MarketTradeHandler: adopted position closed via WS — {symbol}"
                )
                await self._handle_external_close(symbol, reason="ws_position_zero")

    async def _on_ws_execution(self, msg: dict) -> None:
        """
        Called on every 'execution' WS event.
        Detects TP/SL fill → trigger restart.
        """
        data_list = msg.get("data", [])
        if not isinstance(data_list, list):
            data_list = [data_list]

        for exec_data in data_list:
            symbol      = exec_data.get("symbol", "")
            reduce_only = exec_data.get("reduceOnly", False)
            exec_type   = exec_data.get("execType", "")
            if symbol in self._adopted and reduce_only:
                logger.info(
                    f"MarketTradeHandler: reduce-only fill on adopted {symbol} "
                    f"(execType={exec_type}) — triggering restart"
                )
                await self._handle_external_close(
                    symbol, reason=f"reduce_only_fill_{exec_type}"
                )

    # ------------------------------------------------------------------
    # Adoption
    # ------------------------------------------------------------------

    async def _adopt_position(self, symbol: str, pos_data: dict) -> None:
        if symbol in self._adopted:
            return
        if self._adoption_config is None or self._order_manager is None:
            logger.warning(
                f"MarketTradeHandler: cannot adopt {symbol} "
                f"— adoption_config or order_manager missing"
            )
            return
        try:
            from execution.adoption_engine import AdoptionEngine
            engine = AdoptionEngine(
                order_manager=self._order_manager,
                on_cycle_restart=self._on_cycle_restart,
                config=self._adoption_config,
            )
            result = await engine.adopt_and_protect(
                position_dict=pos_data,
                venue=self._venue,
            )
            if result and getattr(result, "adopted", False):
                self._adopted[symbol] = pos_data
                logger.info(
                    f"MarketTradeHandler: {symbol} adopted — "
                    f"tp={getattr(result, 'tp_local_id', '?')} "
                    f"sl={getattr(result, 'sl_local_id', '?')}"
                )
                if self._alert_cfg:
                    try:
                        await self._alert_cfg.send_alert(
                            f"🔄 Adopted external position: {symbol} "
                            f"size={pos_data.get('size')} side={pos_data.get('side')}",
                            level="info",
                        )
                    except Exception:
                        pass
        except Exception as exc:
            logger.error(
                f"MarketTradeHandler: adopt_and_protect failed for {symbol}: {exc}"
            )

    # ------------------------------------------------------------------
    # Close handling
    # ------------------------------------------------------------------

    async def _handle_external_close(
        self, symbol: str, reason: str = "unknown"
    ) -> None:
        self._adopted.pop(symbol, None)
        self._ws_position_sizes[symbol] = 0.0

        if self._order_manager is not None:
            try:
                await self._order_manager.cancel_all_for_symbol(symbol)
            except Exception as exc:
                logger.warning(
                    f"MarketTradeHandler: cancel_all_for_symbol({symbol}) failed: {exc}"
                )

        try:
            from execution.resume_manager import ResumeManager
            await ResumeManager.restart_after_external_close(
                symbol=symbol,
                on_cycle_restart=self._on_cycle_restart,
                cooldown_s=getattr(self._adoption_config, "restart_cooldown_s", 15.0),
                alert_msg=f"Position closed ({reason}): restarting cycle for {symbol}",
            )
        except Exception as exc:
            logger.error(
                f"MarketTradeHandler: restart_after_external_close failed: {exc}"
            )
            if self._on_cycle_restart is not None:
                try:
                    await self._on_cycle_restart(symbol)
                except Exception as e2:
                    logger.error(f"MarketTradeHandler: fallback restart failed: {e2}")

    # ------------------------------------------------------------------
    # Monitor loop (backup for WS-missed closes)
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._monitor_interval)
            if not self._running:
                break
            try:
                await self._monitor_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(f"MarketTradeHandler._monitor_once error: {exc}")

    async def _monitor_once(self) -> None:
        closed = [
            sym for sym in list(self._adopted)
            if self._ws_position_sizes.get(sym, 0.0) == 0.0
        ]
        for symbol in closed:
            logger.warning(
                f"MarketTradeHandler: monitor detected WS-missed close for {symbol}"
            )
            await self._handle_external_close(symbol, reason="monitor_size_zero")

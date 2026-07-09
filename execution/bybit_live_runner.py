"""
QuantLuna — Bybit Live Runner (Sprint 21 + Sprint 28)

Sprint 28 additions:
  - MarketTradeHandler integration: detects external (market) trades,
    places TP/SL via AdoptionEngine, and auto-restarts the cycle
    via start_new_cycle() after the position closes
  - start_new_cycle(symbol): public method — resets IntegrationLoop state
    and re-enables trading for the given symbol after an external trade closes
  - _build_market_trade_handler(): wires all components together
  - BybitLiveRunnerConfig: new fields market_trade_enabled,
    market_trade_poll_s, market_trade_monitor_s, market_trade_cooldown_s,
    market_trade_tp_pct, market_trade_sl_pct

Previous improvements (July 2026):
  - SIGTERM handler: graceful shutdown on SIGTERM (Docker / systemd stop)
  - Periodic stats log every N bars (configurable stats_log_interval)
  - Heartbeat watchdog: if WS feed is dead for > watchdog_dead_s, alert + restart
  - dry_run guard on shutdown alert
  - BybitWsFeed.ws_max_reconnects forwarded from cfg.ws_max_reconnects
  - _build_components passes category from env/cfg to BybitWsFeed

Original docstring preserved below:

Orchestratul principal pentru run real pe Bybit.
Conectează toate componentele S17-S20 într-un singur supervisor asincron:

  BybitWsFeed  (kline WebSocket)
      ↓
  LiveDataBridge.from_ws_bar()
      ↓
  KalmanAdapter.update(price_y, price_x)
      ↓
  SpreadMonitor.update()
      ↓
  RegimeFilter.check()  ←  CircuitBreaker + VolRegimeAdapter + SpreadMonitor
      ↓  gate.allowed
  IntegrationLoop._process_bar()
      ↓
  OrderManager.submit()  →  BybitOrderRouter  (real sau paper)
      ↓
  NotifierBus  →  Slack / Telegram
      ↓
  Checkpoint.save()  (stare + PnL la fiecare bar)
      ↓
  [Sprint 28] MarketTradeHandler polls exchange for external positions
      →  AdoptionEngine.adopt_and_protect()
      →  TP/SL orders placed
      →  on close → start_new_cycle(symbol)
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


@dataclass
class BybitLiveRunnerConfig:
    symbol_y:    str   = "BTCUSDT"
    symbol_x:    str   = "ETHUSDT"
    venue:       str   = "bybit"
    interval:    str   = "5"

    entry_zscore:   float = 2.0
    exit_zscore:    float = 0.5
    base_qty:       float = 0.001
    kalman_window:  int   = 100
    warmup_bars:    int   = 100

    max_consecutive_losses: int   = 3
    max_drawdown_pct:       float = 5.0
    cooldown_seconds:       int   = 3600

    dry_run: bool = True

    ws_reconnect_s:    float = 5.0
    ws_max_reconnects: int   = 20

    checkpoint_path: str = "state/bybit_live_state.json"

    slack_webhook_url:  str = ""
    telegram_bot_token: str = ""
    telegram_chat_id:   str = ""

    # How often to log periodic stats (in bars processed)
    stats_log_interval: int = 100

    # Dead-feed watchdog: seconds without WS message before alert + restart
    watchdog_dead_s: float = 120.0

    # ------------------------------------------------------------------
    # Sprint 28: MarketTradeHandler settings
    # ------------------------------------------------------------------
    # Enable detection and adoption of external (market) trades
    market_trade_enabled: bool = True
    # Seconds between exchange position polls
    market_trade_poll_s: float = 5.0
    # Seconds between checks that adopted positions are still open
    market_trade_monitor_s: float = 10.0
    # Seconds to wait after position close before restarting cycle
    market_trade_cooldown_s: float = 15.0
    # TP target % from entry (e.g. 0.04 = 4%)
    market_trade_tp_pct: float = 0.04
    # SL max loss % from entry (e.g. 0.03 = 3%)
    market_trade_sl_pct: float = 0.03
    # Minimum USD notional to consider a position worth adopting
    market_trade_min_notional: float = 5.0

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        return cls(
            symbol_y              = os.getenv("SYMBOL_Y",              "BTCUSDT"),
            symbol_x              = os.getenv("SYMBOL_X",              "ETHUSDT"),
            venue                 = os.getenv("VENUE",                 "bybit"),
            interval              = os.getenv("INTERVAL",              "5"),
            entry_zscore          = float(os.getenv("ENTRY_ZSCORE",    "2.0")),
            exit_zscore           = float(os.getenv("EXIT_ZSCORE",     "0.5")),
            base_qty              = float(os.getenv("BASE_QTY",        "0.001")),
            kalman_window         = int(os.getenv("KALMAN_WINDOW",     "100")),
            warmup_bars           = int(os.getenv("WARMUP_BARS",       "100")),
            max_consecutive_losses= int(os.getenv("MAX_CONSEC_LOSSES", "3")),
            max_drawdown_pct      = float(os.getenv("MAX_DRAWDOWN_PCT","5.0")),
            cooldown_seconds      = int(os.getenv("COOLDOWN_SECONDS",  "3600")),
            dry_run               = os.getenv("DRY_RUN", "true").lower() != "false",
            ws_reconnect_s        = float(os.getenv("WS_RECONNECT_S",  "5.0")),
            ws_max_reconnects     = int(os.getenv("WS_MAX_RECONNECTS", "20")),
            checkpoint_path       = os.getenv("CHECKPOINT_PATH",       "state/bybit_live_state.json"),
            slack_webhook_url     = os.getenv("SLACK_WEBHOOK_URL",     ""),
            telegram_bot_token    = os.getenv("TELEGRAM_BOT_TOKEN",    ""),
            telegram_chat_id      = os.getenv("TELEGRAM_CHAT_ID",      ""),
            stats_log_interval    = int(os.getenv("STATS_LOG_INTERVAL", "100")),
            watchdog_dead_s       = float(os.getenv("WATCHDOG_DEAD_S",  "120.0")),
            # Sprint 28
            market_trade_enabled  = os.getenv("MARKET_TRADE_ENABLED", "true").lower() != "false",
            market_trade_poll_s   = float(os.getenv("MARKET_TRADE_POLL_S",    "5.0")),
            market_trade_monitor_s= float(os.getenv("MARKET_TRADE_MONITOR_S", "10.0")),
            market_trade_cooldown_s=float(os.getenv("MARKET_TRADE_COOLDOWN_S","15.0")),
            market_trade_tp_pct   = float(os.getenv("MARKET_TRADE_TP_PCT",    "0.04")),
            market_trade_sl_pct   = float(os.getenv("MARKET_TRADE_SL_PCT",    "0.03")),
            market_trade_min_notional=float(os.getenv("MARKET_TRADE_MIN_NOTIONAL", "5.0")),
        )


class BybitLiveRunner:
    """
    Main supervisor for Bybit live/paper trading.

    Sprint 28: now also supervises a MarketTradeHandler that detects
    external positions, protects them with TP/SL, and restarts the
    trading cycle via start_new_cycle() when the position closes.
    """

    def __init__(
        self,
        cfg: Optional[BybitLiveRunnerConfig] = None,
        kalman=None,
        spread_monitor=None,
        regime_filter=None,
        order_manager=None,
        notifier_bus=None,
        checkpoint=None,
        ws_feed=None,
        # Sprint 28: optional ccxt exchange instance for MarketTradeHandler
        exchange=None,
    ) -> None:
        self.cfg             = cfg or BybitLiveRunnerConfig()
        self._running        = False
        self._stop_event     = asyncio.Event()
        self._bar_count      = 0
        self._warmed_up      = False

        self._kalman         = kalman
        self._spread_monitor = spread_monitor
        self._regime_filter  = regime_filter
        self._order_manager  = order_manager
        self._notifier_bus   = notifier_bus
        self._checkpoint     = checkpoint
        self._ws_feed        = ws_feed

        # Sprint 28: ccxt exchange for position polling
        self._exchange       = exchange
        # Sprint 28: MarketTradeHandler instance (built in _build_components)
        self._market_trade_handler = None
        # Sprint 28: IntegrationLoop reference for cycle reset
        self._intloop        = None
        # Sprint 28: guard to avoid double restart
        self._restart_lock   = asyncio.Lock()

        self._loop_task: Optional[asyncio.Task] = None
        self._ws_task:   Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._mth_task:  Optional[asyncio.Task] = None  # Sprint 28

        self._stats = {
            "bars_processed":   0,
            "orders_submitted": 0,
            "gate_blocks":      0,
            "errors":           0,
            "started_at":       0.0,
            # Sprint 28
            "market_trades_adopted": 0,
            "cycle_restarts":        0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info(
            f"BybitLiveRunner: starting "
            f"{self.cfg.symbol_y}/{self.cfg.symbol_x} "
            f"interval={self.cfg.interval}m "
            f"dry_run={self.cfg.dry_run}"
        )
        self._stats["started_at"] = time.time()
        self._running  = True
        self._stop_event.clear()

        # Register SIGTERM for graceful shutdown (Docker / systemd)
        self._register_signal_handlers()

        await self._build_components()
        await self._notify_startup()

        if self._order_manager is not None:
            try:
                await self._order_manager.start()
            except Exception:
                pass

        # Start heartbeat watchdog
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        # Sprint 28: start MarketTradeHandler if enabled and exchange available
        if self._market_trade_handler is not None:
            self._mth_task = asyncio.create_task(
                self._market_trade_handler.start(),
                name="market_trade_handler",
            )
            logger.info("BybitLiveRunner: MarketTradeHandler started")

        try:
            await self._run_main_loop()
        finally:
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
            # Sprint 28: stop MarketTradeHandler
            if self._market_trade_handler is not None:
                await self._market_trade_handler.stop()
            if self._mth_task and not self._mth_task.done():
                self._mth_task.cancel()
            await self._shutdown()

    async def stop(self) -> None:
        logger.info("BybitLiveRunner: stop requested")
        self._running = False
        self._stop_event.set()

    def status(self) -> dict:
        uptime = time.time() - self._stats["started_at"] if self._stats["started_at"] else 0
        return {
            **self._stats,
            "uptime_s":  round(uptime, 1),
            "warmed_up": self._warmed_up,
            "dry_run":   self.cfg.dry_run,
            "symbol_y":  self.cfg.symbol_y,
            "symbol_x":  self.cfg.symbol_x,
        }

    # ------------------------------------------------------------------
    # Sprint 28: start_new_cycle — called by MarketTradeHandler on close
    # ------------------------------------------------------------------

    async def start_new_cycle(self, symbol: str) -> None:
        """
        Called by MarketTradeHandler (via AdoptionEngine / ResumeManager)
        when an adopted external position closes (TP hit, SL hit, or manual close).

        Resets the IntegrationLoop state and re-enables entry for `symbol`.
        If the symbol matches one of our pair legs, both legs are reset.

        This is the single point of truth for cycle restart — pass this
        method as `on_cycle_restart=` when building MarketTradeHandler.

        Parameters
        ----------
        symbol : str  e.g. "BTCUSDT" — the symbol whose position just closed
        """
        async with self._restart_lock:
            self._stats["cycle_restarts"] += 1
            logger.info(
                f"BybitLiveRunner.start_new_cycle: restarting cycle for {symbol} "
                f"(restart #{self._stats['cycle_restarts']})"
            )

            # 1. Reset IntegrationLoop position state
            if self._intloop is not None:
                self._intloop.reset_cycle()
                logger.info("BybitLiveRunner: IntegrationLoop cycle reset")

            # 2. Unregister symbol from bot-owned set so it can be re-adopted
            #    if another external trade opens before the bot takes one
            if self._market_trade_handler is not None:
                self._market_trade_handler.unregister_bot_symbol(symbol)

            # 3. Notify via NotifierBus
            if self._notifier_bus is not None:
                try:
                    await self._notifier_bus.send_alert(
                        f"🔄 QuantLuna: cycle restarted for {symbol} "
                        f"(restart #{self._stats['cycle_restarts']})",
                        level="info",
                    )
                except Exception as exc:
                    logger.warning(f"BybitLiveRunner: notify restart failed: {exc}")

            logger.info(
                f"BybitLiveRunner.start_new_cycle: done. "
                f"IntegrationLoop ready to take new entries."
            )

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        """
        Register SIGTERM + SIGINT for graceful shutdown.
        Useful for Docker (SIGTERM on `docker stop`) and systemd.
        """
        if sys.platform == "win32":
            return  # signal.add_signal_handler not available on Windows
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_signal(s)),
            )

    async def _handle_signal(self, sig: signal.Signals) -> None:
        logger.info(f"BybitLiveRunner: received {sig.name}, initiating graceful shutdown")
        await self.stop()

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        """
        Periodic heartbeat:
          - Logs stats every cfg.stats_log_interval bars
          - Alerts + triggers reconnect if WS feed is dead > cfg.watchdog_dead_s
        """
        last_bars = 0
        while self._running:
            await asyncio.sleep(30)
            if not self._running:
                break

            # Periodic stats log (Sprint 28: include market trade stats)
            current_bars = self._stats["bars_processed"]
            if current_bars - last_bars >= self.cfg.stats_log_interval:
                logger.info(
                    f"[Heartbeat] bars={current_bars} "
                    f"orders={self._stats['orders_submitted']} "
                    f"gate_blocks={self._stats['gate_blocks']} "
                    f"errors={self._stats['errors']} "
                    f"market_adopted={self._stats.get('market_trades_adopted', 0)} "
                    f"cycle_restarts={self._stats.get('cycle_restarts', 0)} "
                    f"uptime={self.status()['uptime_s']:.0f}s"
                )
                last_bars = current_bars

            # Dead-feed watchdog
            if self._ws_feed is not None and hasattr(self._ws_feed, "last_msg_age_s"):
                age = self._ws_feed.last_msg_age_s
                if age > self.cfg.watchdog_dead_s:
                    logger.error(
                        f"[Watchdog] WS feed dead for {age:.0f}s "
                        f"(threshold={self.cfg.watchdog_dead_s}s) — alerting"
                    )
                    if self._notifier_bus:
                        try:
                            await self._notifier_bus.send_alert(
                                f"⚠️ WS feed dead for {age:.0f}s on "
                                f"{self.cfg.symbol_y}/{self.cfg.symbol_x}. "
                                f"Forcing reconnect.",
                                level="error",
                            )
                        except Exception:
                            pass
                    await self.stop()

    # ------------------------------------------------------------------
    # Component builder
    # ------------------------------------------------------------------

    async def _build_components(self) -> None:
        cfg = self.cfg

        if self._kalman is None:
            from core.kalman_adapter import KalmanAdapter
            self._kalman = KalmanAdapter(window=cfg.kalman_window)

        if self._spread_monitor is None:
            from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
            self._spread_monitor = SpreadMonitor(SpreadMonitorConfig(
                min_bars=cfg.warmup_bars,
                zscore_control_limit=4.5,
                max_half_life_hours=120.0,
                stuck_bars_threshold=60,
            ))

        if self._regime_filter is None:
            from strategy.regime_filter import RegimeFilter
            from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
            from core.vol_regime_adapter import VolRegimeAdapter
            cb = CircuitBreaker(CircuitBreakerConfig(
                max_consecutive_losses=cfg.max_consecutive_losses,
                max_drawdown_pct=cfg.max_drawdown_pct,
                cooldown_seconds=cfg.cooldown_seconds,
            ))
            vr = VolRegimeAdapter(ewma_span=20)
            self._regime_filter = RegimeFilter(
                circuit_breaker=cb,
                vol_regime=vr,
                spread_monitor=self._spread_monitor,
            )

        if self._order_manager is None:
            from execution.order_manager import OrderManager, OrderManagerConfig
            self._order_manager = OrderManager(OrderManagerConfig(
                dry_run=cfg.dry_run,
            ))

        if self._notifier_bus is None:
            await self._build_notifier_bus()

        if self._checkpoint is None:
            try:
                from execution.checkpoint import Checkpoint
                os.makedirs(os.path.dirname(cfg.checkpoint_path), exist_ok=True)
                self._checkpoint = Checkpoint(path=cfg.checkpoint_path)
            except Exception as exc:
                logger.warning(f"BybitLiveRunner: checkpoint not available: {exc}")

        # Sprint 28: build MarketTradeHandler if enabled
        if cfg.market_trade_enabled:
            await self._build_market_trade_handler()

    async def _build_market_trade_handler(self) -> None:
        """
        Sprint 28: Build and wire MarketTradeHandler with AdoptionConfig,
        OrderManager, Checkpoint and on_cycle_restart callback.

        If no exchange is injected (self._exchange is None), tries to build
        a CCXT Bybit async instance using env credentials.
        Skips silently if credentials are missing (safe in paper/dry mode).
        """
        cfg = self.cfg

        # Resolve exchange — prefer injected, else build from env
        exchange = self._exchange
        if exchange is None:
            api_key    = os.getenv("BYBIT_API_KEY", "")
            api_secret = os.getenv("BYBIT_API_SECRET", "")
            if not api_key or not api_secret:
                logger.info(
                    "BybitLiveRunner: BYBIT_API_KEY/SECRET not set — "
                    "MarketTradeHandler disabled (no exchange for position polling)"
                )
                return
            try:
                import ccxt.async_support as ccxt
                exchange = ccxt.bybit({
                    "apiKey":    api_key,
                    "secret":    api_secret,
                    "enableRateLimit": True,
                    "options":   {"defaultType": "linear"},
                })
                testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
                if testnet:
                    exchange.set_sandbox_mode(True)
                self._exchange = exchange
                logger.info("BybitLiveRunner: CCXT Bybit async exchange built for MarketTradeHandler")
            except Exception as exc:
                logger.warning(f"BybitLiveRunner: could not build CCXT exchange: {exc} — MarketTradeHandler disabled")
                return

        try:
            from execution.market_trade_handler import MarketTradeHandler
            from execution.adoption_engine import AdoptionConfig

            adoption_cfg = AdoptionConfig(
                tp_target_pct=cfg.market_trade_tp_pct,
                sl_max_loss_pct=cfg.market_trade_sl_pct,
                min_notional_adopt=cfg.market_trade_min_notional,
                restart_cooldown_s=cfg.market_trade_cooldown_s,
            )

            self._market_trade_handler = MarketTradeHandler(
                exchange=exchange,
                order_manager=self._order_manager,
                checkpoint=self._checkpoint,
                adoption_config=adoption_cfg,
                alert_cfg=self._notifier_bus,
                on_cycle_restart=self.start_new_cycle,   # ← wired here
                venue=cfg.venue,
                poll_interval_s=cfg.market_trade_poll_s,
                monitor_interval_s=cfg.market_trade_monitor_s,
                symbols=[cfg.symbol_y, cfg.symbol_x],    # watch only our pair
            )

            # Register bot-owned symbols so we don't adopt our own positions
            self._market_trade_handler.register_bot_symbol(cfg.symbol_y)
            self._market_trade_handler.register_bot_symbol(cfg.symbol_x)

            logger.info(
                f"BybitLiveRunner: MarketTradeHandler built — "
                f"watching {cfg.symbol_y}/{cfg.symbol_x} "
                f"poll={cfg.market_trade_poll_s}s "
                f"tp={cfg.market_trade_tp_pct*100:.1f}% "
                f"sl={cfg.market_trade_sl_pct*100:.1f}%"
            )
        except Exception as exc:
            logger.error(f"BybitLiveRunner: failed to build MarketTradeHandler: {exc}")

    async def _build_notifier_bus(self) -> None:
        cfg = self.cfg
        try:
            from notifications.notifier_bus import NotifierBus
            bus = NotifierBus(fail_silent=True)
            if cfg.slack_webhook_url:
                from notifications.slack_notifier import SlackNotifier, SlackConfig
                bus.register("slack", SlackNotifier(SlackConfig(
                    webhook_url=cfg.slack_webhook_url
                )))
            if cfg.telegram_bot_token and cfg.telegram_chat_id:
                try:
                    from notifications.telegram import TelegramNotifier
                    bus.register("telegram", TelegramNotifier(
                        token=cfg.telegram_bot_token,
                        chat_id=cfg.telegram_chat_id,
                    ))
                except Exception:
                    pass
            self._notifier_bus = bus
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: NotifierBus not available: {exc}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_main_loop(self) -> None:
        if self._ws_feed is not None:
            await self._run_ws_loop()
        else:
            logger.warning(
                "BybitLiveRunner: no ws_feed injected. "
                "Inject BybitWsFeed for production use. Waiting for stop signal."
            )
            await self._stop_event.wait()

    async def _run_ws_loop(self) -> None:
        from execution.integration_loop import IntegrationLoopConfig, IntegrationLoop
        cfg      = self.cfg
        loop_cfg = IntegrationLoopConfig(
            symbol_y=cfg.symbol_y, symbol_x=cfg.symbol_x,
            venue=cfg.venue,
            entry_zscore=cfg.entry_zscore, exit_zscore=cfg.exit_zscore,
            base_qty=cfg.base_qty, dry_run=cfg.dry_run,
            bar_interval_s=0.0,
        )
        self._intloop = IntegrationLoop(
            cfg=loop_cfg,
            kalman=self._kalman,
            spread_monitor=self._spread_monitor,
            regime_filter=self._regime_filter,
            order_manager=self._order_manager,
            notifier_bus=self._notifier_bus,
        )

        reconnects = 0
        while self._running and reconnects < cfg.ws_max_reconnects:
            try:
                async for bar in self._ws_feed.stream_bars():
                    if not self._running or self._stop_event.is_set():
                        return

                    self._bar_count += 1
                    self._stats["bars_processed"] += 1

                    if not self._warmed_up:
                        if self._bar_count >= cfg.warmup_bars:
                            self._warmed_up = True
                            logger.info(
                                f"BybitLiveRunner: warmup complete "
                                f"({cfg.warmup_bars} bars), trading enabled"
                            )
                        else:
                            await self._intloop._process_bar(bar)
                            continue

                    result = await self._intloop._process_bar(bar)

                    if result.order_submitted:
                        self._stats["orders_submitted"] += 1
                        # Sprint 28: register symbol as bot-owned after entry
                        if self._market_trade_handler is not None:
                            self._market_trade_handler.register_bot_symbol(cfg.symbol_y)
                            self._market_trade_handler.register_bot_symbol(cfg.symbol_x)

                    if not result.gate_allowed:
                        self._stats["gate_blocks"] += 1

                    if self._checkpoint is not None:
                        try:
                            self._checkpoint.save({
                                "bar_count":        self._bar_count,
                                "last_zscore":      result.zscore,
                                "gate_allowed":     result.gate_allowed,
                                "spread_healthy":   result.spread_healthy,
                                "size_multiplier":  result.size_multiplier,
                                "orders_submitted": self._stats["orders_submitted"],
                                "timestamp":        time.time(),
                            })
                        except Exception:
                            pass

                    reconnects = 0

            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._stats["errors"] += 1
                reconnects += 1
                logger.error(
                    f"BybitLiveRunner: WS error "
                    f"(reconnect {reconnects}/{cfg.ws_max_reconnects}): {exc}"
                )
                if self._notifier_bus:
                    try:
                        await self._notifier_bus.send_alert(
                            f"WS error: {exc}", level="warning"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(cfg.ws_reconnect_s)

        if reconnects >= cfg.ws_max_reconnects:
            logger.error("BybitLiveRunner: max reconnects reached, stopping")
            await self.stop()

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def _notify_startup(self) -> None:
        if self._notifier_bus is None:
            return
        try:
            mode_label = "DRY" if self.cfg.dry_run else "LIVE"
            mth_label  = " +MarketTradeWatch" if self._market_trade_handler else ""
            await self._notifier_bus.send_alert(
                f"🚀 QuantLuna [{mode_label}{mth_label}] started: "
                f"{self.cfg.symbol_y}/{self.cfg.symbol_x} "
                f"interval={self.cfg.interval}m",
                level="info",
            )
        except Exception:
            pass

    async def _shutdown(self) -> None:
        logger.info(f"BybitLiveRunner: shutdown | stats={self.status()}")
        # Sprint 28: close CCXT exchange if we built it
        if self._exchange is not None:
            try:
                await self._exchange.close()
                logger.debug("BybitLiveRunner: CCXT exchange closed")
            except Exception:
                pass
        if self._order_manager is not None:
            try:
                await self._order_manager.stop()
            except Exception:
                pass
        if self._notifier_bus is not None:
            if not self.cfg.dry_run:
                try:
                    await self._notifier_bus.send_alert(
                        f"🛑 QuantLuna stopped. "
                        f"bars={self._stats['bars_processed']} "
                        f"orders={self._stats['orders_submitted']} "
                        f"market_adopted={self._stats.get('market_trades_adopted', 0)} "
                        f"cycle_restarts={self._stats.get('cycle_restarts', 0)} "
                        f"errors={self._stats['errors']}",
                        level="info",
                    )
                except Exception:
                    pass

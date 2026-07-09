"""
QuantLuna — Bybit Live Runner (Sprint 21 + Sprint 28 rev-3)

Sprint 28 rev-3 refactor:
  - _build_exchange_via_factory()  → delegates to ExchangeFactory class
  - _start_health_server()         → delegates to HealthCheck.start_http_server()
                                     (falls back to inline aiohttp if not available)
  - _watchdog_loop()               → delegates to WsWatchdog class
  - All 6 Sprint 28 subsystems remain wired (#1-#6)

All previous Sprint 28 rev-2 behaviour is preserved.
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

    stats_log_interval: int   = 100
    watchdog_dead_s:    float = 120.0

    # Sprint 28 #1: MarketTradeHandler
    market_trade_enabled:      bool  = True
    market_trade_monitor_s:    float = 10.0
    market_trade_cooldown_s:   float = 15.0
    market_trade_tp_pct:       float = 0.04
    market_trade_sl_pct:       float = 0.03
    market_trade_min_notional: float = 5.0

    # Sprint 28 #2: PnL reconciler
    pnl_reconciler_enabled:     bool  = True
    pnl_reconciler_interval_s:  float = 30.0
    pnl_drift_alert_usd:        float = 10.0
    pnl_cb_loss_threshold_usd:  float = -5.0

    # Sprint 28 #3: HealthCheck HTTP
    health_http_enabled: bool = True
    health_http_port:    int  = 8080

    # Sprint 28 #5: FundingMonitor gate
    funding_gate_enabled:      bool  = True
    funding_gate_max_net_ann:  float = 0.5
    funding_poll_interval_s:   float = 60.0

    # Extra: HealthCheck pre-flight thresholds
    min_capital_usdt: float = 100.0

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        return cls(
            symbol_y                  = os.getenv("SYMBOL_Y",                     "BTCUSDT"),
            symbol_x                  = os.getenv("SYMBOL_X",                     "ETHUSDT"),
            venue                     = os.getenv("VENUE",                        "bybit"),
            interval                  = os.getenv("INTERVAL",                     "5"),
            entry_zscore              = float(os.getenv("ENTRY_ZSCORE",           "2.0")),
            exit_zscore               = float(os.getenv("EXIT_ZSCORE",            "0.5")),
            base_qty                  = float(os.getenv("BASE_QTY",               "0.001")),
            kalman_window             = int(os.getenv("KALMAN_WINDOW",            "100")),
            warmup_bars               = int(os.getenv("WARMUP_BARS",              "100")),
            max_consecutive_losses    = int(os.getenv("MAX_CONSEC_LOSSES",        "3")),
            max_drawdown_pct          = float(os.getenv("MAX_DRAWDOWN_PCT",       "5.0")),
            cooldown_seconds          = int(os.getenv("COOLDOWN_SECONDS",         "3600")),
            dry_run                   = os.getenv("DRY_RUN", "true").lower() != "false",
            ws_reconnect_s            = float(os.getenv("WS_RECONNECT_S",         "5.0")),
            ws_max_reconnects         = int(os.getenv("WS_MAX_RECONNECTS",        "20")),
            checkpoint_path           = os.getenv("CHECKPOINT_PATH",              "state/bybit_live_state.json"),
            slack_webhook_url         = os.getenv("SLACK_WEBHOOK_URL",            ""),
            telegram_bot_token        = os.getenv("TELEGRAM_BOT_TOKEN",           ""),
            telegram_chat_id          = os.getenv("TELEGRAM_CHAT_ID",             ""),
            stats_log_interval        = int(os.getenv("STATS_LOG_INTERVAL",       "100")),
            watchdog_dead_s           = float(os.getenv("WATCHDOG_DEAD_S",        "120.0")),
            market_trade_enabled      = os.getenv("MARKET_TRADE_ENABLED",         "true").lower() != "false",
            market_trade_monitor_s    = float(os.getenv("MARKET_TRADE_MONITOR_S", "10.0")),
            market_trade_cooldown_s   = float(os.getenv("MARKET_TRADE_COOLDOWN_S","15.0")),
            market_trade_tp_pct       = float(os.getenv("MARKET_TRADE_TP_PCT",    "0.04")),
            market_trade_sl_pct       = float(os.getenv("MARKET_TRADE_SL_PCT",    "0.03")),
            market_trade_min_notional = float(os.getenv("MARKET_TRADE_MIN_NOTIONAL","5.0")),
            pnl_reconciler_enabled    = os.getenv("PNL_RECONCILER_ENABLED",       "true").lower() != "false",
            pnl_reconciler_interval_s = float(os.getenv("PNL_RECONCILER_INTERVAL_S","30.0")),
            pnl_drift_alert_usd       = float(os.getenv("PNL_DRIFT_ALERT_USD",    "10.0")),
            pnl_cb_loss_threshold_usd = float(os.getenv("PNL_CB_LOSS_THRESHOLD_USD","-5.0")),
            health_http_enabled       = os.getenv("HEALTH_HTTP_ENABLED",          "true").lower() != "false",
            health_http_port          = int(os.getenv("HEALTH_PORT",              "8080")),
            funding_gate_enabled      = os.getenv("FUNDING_GATE_ENABLED",         "true").lower() != "false",
            funding_gate_max_net_ann  = float(os.getenv("FUNDING_GATE_MAX_NET_ANN","0.5")),
            funding_poll_interval_s   = float(os.getenv("FUNDING_POLL_INTERVAL_S", "60.0")),
            min_capital_usdt          = float(os.getenv("MIN_CAPITAL_USDT",        "100.0")),
        )


class BybitLiveRunner:
    """
    Main supervisor for Bybit live/paper trading.
    Sprint 28 rev-3: delegates exchange/health/watchdog to dedicated modules.
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
        exchange=None,
        private_ws=None,
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
        self._exchange       = exchange
        self._private_ws     = private_ws

        self._market_trade_handler = None
        self._intloop              = None
        self._restart_lock         = asyncio.Lock()
        self._funding_monitor      = None
        self._pnl_reconciler       = None
        self._circuit_breaker      = None
        self._health_server        = None
        self._ws_watchdog          = None

        self._watchdog_task = None
        self._mth_task      = None
        self._funding_task  = None
        self._pnl_task      = None

        self._stats = {
            "bars_processed":        0,
            "orders_submitted":      0,
            "gate_blocks":           0,
            "errors":                0,
            "started_at":            0.0,
            "market_trades_adopted": 0,
            "cycle_restarts":        0,
            "pnl_drift_alerts":      0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info(
            f"BybitLiveRunner: starting "
            f"{self.cfg.symbol_y}/{self.cfg.symbol_x} "
            f"interval={self.cfg.interval}m dry_run={self.cfg.dry_run}"
        )
        self._stats["started_at"] = time.time()
        self._running  = True
        self._stop_event.clear()
        self._register_signal_handlers()

        await self._build_components()
        await self._notify_startup()

        if self._order_manager is not None:
            try:
                await self._order_manager.start()
            except Exception:
                pass

        # #3 Health HTTP server
        if self.cfg.health_http_enabled:
            await self._start_health_server()

        # #5 FundingMonitor
        if self._funding_monitor is not None:
            self._funding_task = asyncio.create_task(
                self._funding_monitor.run(), name="funding_monitor"
            )
            logger.info("BybitLiveRunner: FundingMonitor started")

        # #2 PnL reconciler
        if self._pnl_reconciler is not None:
            self._pnl_task = asyncio.create_task(
                self._run_pnl_reconciler(), name="pnl_reconciler"
            )
            logger.info("BybitLiveRunner: PnLReconciler started")

        # Watchdog (rev-3: WsWatchdog class)
        self._watchdog_task = asyncio.create_task(
            self._run_watchdog(), name="ws_watchdog"
        )

        # #1 MarketTradeHandler
        if self._market_trade_handler is not None:
            self._mth_task = asyncio.create_task(
                self._market_trade_handler.start(), name="market_trade_handler"
            )
            logger.info("BybitLiveRunner: MarketTradeHandler (WS) started")

        try:
            await self._run_main_loop()
        finally:
            await self._cleanup_tasks()
            await self._shutdown()

    async def stop(self) -> None:
        logger.info("BybitLiveRunner: stop requested")
        self._running = False
        self._stop_event.set()
        if self._ws_watchdog is not None:
            try:
                await self._ws_watchdog.stop()
            except Exception:
                pass

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
    # start_new_cycle
    # ------------------------------------------------------------------

    async def start_new_cycle(self, symbol: str) -> None:
        async with self._restart_lock:
            self._stats["cycle_restarts"] += 1
            logger.info(
                f"BybitLiveRunner.start_new_cycle: {symbol} "
                f"(restart #{self._stats['cycle_restarts']})"
            )
            if self._intloop is not None:
                self._intloop.reset_cycle()
            if self._market_trade_handler is not None:
                self._market_trade_handler.unregister_bot_symbol(symbol)
            if self._notifier_bus is not None:
                try:
                    await self._notifier_bus.send_alert(
                        f"\U0001f504 Cycle restarted for {symbol} "
                        f"(#{self._stats['cycle_restarts']})",
                        level="info",
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # PnL reconciler bridge
    # ------------------------------------------------------------------

    async def _run_pnl_reconciler(self) -> None:
        if self._pnl_reconciler is None:
            return
        cfg = self.cfg

        class _BusBridge:
            def __init__(self, runner):
                self._r    = runner
                self._snap = type("S", (), {"open_pnl_usd": 0.0})()

            def snapshot(self):
                return self._snap

            def update(self, data: dict):
                if data.get("pnl_drift_alert"):
                    self._r._stats["pnl_drift_alerts"] += 1
                    logger.warning(
                        f"PnLReconciler: drift alert "
                        f"drift=${data.get('pnl_drift_usd', 0):.2f}"
                    )
                pnl = data.get("reconciled_open_pnl", 0.0)
                if (
                    pnl < cfg.pnl_cb_loss_threshold_usd
                    and self._r._circuit_breaker is not None
                ):
                    try:
                        self._r._circuit_breaker.record_pnl(pnl)
                        logger.warning(
                            f"PnLReconciler→CB: open_pnl={pnl:.2f} USD "
                            f"< threshold {cfg.pnl_cb_loss_threshold_usd}"
                        )
                    except Exception as exc:
                        logger.debug(f"CB.record_pnl error: {exc}")

        self._pnl_reconciler.bus = _BusBridge(self)
        await self._pnl_reconciler.run()

    # ------------------------------------------------------------------
    # rev-3: Health HTTP via HealthCheck module
    # ------------------------------------------------------------------

    async def _start_health_server(self) -> None:
        """
        Starts HTTP health server.
        Tries HealthCheck module first; falls back to inline aiohttp.
        """
        try:
            from execution.health_check import HealthCheck, HealthConfig
            if hasattr(HealthCheck, "start_http_server"):
                hc = HealthCheck(HealthConfig(
                    exchange=self.cfg.venue,
                    sym_y=self.cfg.symbol_y,
                    sym_x=self.cfg.symbol_x,
                ))
                await hc.start_http_server(
                    port=self.cfg.health_http_port,
                    status_callback=self.status,
                    ready_callback=lambda: self._running and self._warmed_up,
                )
                self._health_server = hc
                logger.info(
                    f"BybitLiveRunner: Health HTTP (HealthCheck module) "
                    f"on :{self.cfg.health_http_port}"
                )
                return
        except Exception:
            pass

        # Fallback: inline aiohttp
        await self._start_health_server_inline()

    async def _start_health_server_inline(self) -> None:
        try:
            from aiohttp import web
            runner_ref = self

            async def health(request):
                return web.json_response({
                    "status":    "ok" if runner_ref._running else "stopping",
                    "warmed_up": runner_ref._warmed_up,
                    "uptime_s":  runner_ref.status()["uptime_s"],
                })

            async def metrics(request):
                return web.json_response(runner_ref.status())

            async def ready(request):
                ok = runner_ref._running and runner_ref._warmed_up
                return web.json_response({"ready": ok}, status=200 if ok else 503)

            app = web.Application()
            app.router.add_get("/health",  health)
            app.router.add_get("/metrics", metrics)
            app.router.add_get("/ready",   ready)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", self.cfg.health_http_port)
            await site.start()
            self._health_server = runner
            logger.info(
                f"BybitLiveRunner: Health HTTP (inline) on :{self.cfg.health_http_port}"
            )
        except ImportError:
            logger.warning("BybitLiveRunner: aiohttp not installed — health HTTP disabled")
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: health server failed: {exc}")

    # ------------------------------------------------------------------
    # rev-3: Watchdog via WsWatchdog class
    # ------------------------------------------------------------------

    async def _run_watchdog(self) -> None:
        """
        Tries WsWatchdog class first; falls back to inline loop.
        """
        try:
            from execution.ws_watchdog import WsWatchdog, WsWatchdogConfig
            cfg = WsWatchdogConfig(
                dead_s=self.cfg.watchdog_dead_s,
                stats_log_interval=self.cfg.stats_log_interval,
            )
            self._ws_watchdog = WsWatchdog(
                cfg=cfg,
                ws_feed=self._ws_feed,
                status_callback=self.status,
                on_dead=self.stop,
                notifier_bus=self._notifier_bus,
            )
            await self._ws_watchdog.run()
        except Exception:
            await self._watchdog_loop_inline()

    async def _watchdog_loop_inline(self) -> None:
        last_bars = 0
        while self._running:
            await asyncio.sleep(30)
            if not self._running:
                break
            current_bars = self._stats["bars_processed"]
            if current_bars - last_bars >= self.cfg.stats_log_interval:
                logger.info(
                    f"[Heartbeat] bars={current_bars} "
                    f"orders={self._stats['orders_submitted']} "
                    f"blocks={self._stats['gate_blocks']} "
                    f"adopted={self._stats['market_trades_adopted']} "
                    f"restarts={self._stats['cycle_restarts']} "
                    f"pnl_alerts={self._stats['pnl_drift_alerts']} "
                    f"errors={self._stats['errors']} "
                    f"uptime={self.status()['uptime_s']:.0f}s"
                )
                last_bars = current_bars
            if self._ws_feed is not None and hasattr(self._ws_feed, "last_msg_age_s"):
                age = self._ws_feed.last_msg_age_s
                if age > self.cfg.watchdog_dead_s:
                    logger.error(f"[Watchdog] WS dead {age:.0f}s — stopping")
                    if self._notifier_bus:
                        try:
                            await self._notifier_bus.send_alert(
                                f"\u26a0\ufe0f WS dead {age:.0f}s", level="error"
                            )
                        except Exception:
                            pass
                    await self.stop()

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        if sys.platform == "win32":
            return
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_signal(s)),
            )

    async def _handle_signal(self, sig) -> None:
        logger.info(f"BybitLiveRunner: {sig.name} → graceful shutdown")
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
            self._circuit_breaker = cb
            vr = VolRegimeAdapter(ewma_span=20)
            self._regime_filter = RegimeFilter(
                circuit_breaker=cb,
                vol_regime=vr,
                spread_monitor=self._spread_monitor,
            )

        if self._order_manager is None:
            from execution.order_manager import OrderManager, OrderManagerConfig
            self._order_manager = OrderManager(OrderManagerConfig(dry_run=cfg.dry_run))

        if self._notifier_bus is None:
            await self._build_notifier_bus()

        if self._checkpoint is None:
            try:
                from execution.checkpoint import Checkpoint
                os.makedirs(os.path.dirname(cfg.checkpoint_path), exist_ok=True)
                self._checkpoint = Checkpoint(path=cfg.checkpoint_path)
            except Exception as exc:
                logger.warning(f"Checkpoint unavailable: {exc}")

        # rev-3: ExchangeFactory class
        if self._exchange is None:
            self._exchange = self._build_exchange_via_factory()

        if cfg.funding_gate_enabled and self._exchange is not None:
            await self._build_funding_monitor()

        if cfg.pnl_reconciler_enabled and self._exchange is not None:
            await self._build_pnl_reconciler()

        if cfg.market_trade_enabled:
            await self._build_market_trade_handler()

    # ------------------------------------------------------------------
    # rev-3: ExchangeFactory delegates to module
    # ------------------------------------------------------------------

    def _build_exchange_via_factory(self):
        api_key    = os.getenv("BYBIT_API_KEY",    "")
        api_secret = os.getenv("BYBIT_API_SECRET", "")
        if not api_key or not api_secret:
            logger.info(
                "BybitLiveRunner: no API creds — exchange not built (dry mode OK)"
            )
            return None
        try:
            from execution.exchange_factory import ExchangeFactory
            return ExchangeFactory.build(
                venue=self.cfg.venue,
                api_key=api_key,
                api_secret=api_secret,
                testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
            )
        except Exception:
            pass
        # Fallback: inline build
        try:
            import ccxt.async_support as ccxt
            testnet  = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
            exchange = ccxt.bybit({
                "apiKey":          api_key,
                "secret":          api_secret,
                "enableRateLimit": True,
                "options":         {"defaultType": "linear"},
            })
            if testnet:
                exchange.set_sandbox_mode(True)
            logger.info("BybitLiveRunner: CCXT Bybit built (inline fallback)")
            return exchange
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: exchange build failed: {exc}")
            return None

    async def _build_funding_monitor(self) -> None:
        cfg = self.cfg
        try:
            from execution.funding_monitor import FundingMonitor, FundingConfig

            def _sym(s: str) -> str:
                s = s.upper()
                return f"{s[:-4]}/USDT:USDT" if "/" not in s and s.endswith("USDT") else s

            fm_cfg = FundingConfig(
                sym_y=_sym(cfg.symbol_y),
                sym_x=_sym(cfg.symbol_x),
                poll_interval_s=cfg.funding_poll_interval_s,
            )
            self._funding_monitor = FundingMonitor(
                cfg=fm_cfg, exchange=self._exchange, bus=None,
            )
            logger.info(
                f"BybitLiveRunner: FundingMonitor ({fm_cfg.sym_y}/{fm_cfg.sym_x} "
                f"max_net={cfg.funding_gate_max_net_ann:.1%})"
            )
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: FundingMonitor build failed: {exc}")

    async def _build_pnl_reconciler(self) -> None:
        cfg = self.cfg
        try:
            from execution.pnl_reconciler import PnLReconciler, ReconcilerConfig

            def _sym(s: str) -> str:
                s = s.upper()
                return f"{s[:-4]}/USDT:USDT" if "/" not in s and s.endswith("USDT") else s

            rec_cfg = ReconcilerConfig(
                sym_y=_sym(cfg.symbol_y),
                sym_x=_sym(cfg.symbol_x),
                poll_interval_s=cfg.pnl_reconciler_interval_s,
                drift_alert_usd=cfg.pnl_drift_alert_usd,
            )
            self._pnl_reconciler = PnLReconciler(
                cfg=rec_cfg, exchange=self._exchange, bus=None,
            )
            logger.info(
                f"BybitLiveRunner: PnLReconciler "
                f"(drift_alert=${cfg.pnl_drift_alert_usd} "
                f"cb_threshold=${cfg.pnl_cb_loss_threshold_usd})"
            )
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: PnLReconciler build failed: {exc}")

    async def _build_market_trade_handler(self) -> None:
        cfg        = self.cfg
        api_key    = os.getenv("BYBIT_API_KEY",    "")
        api_secret = os.getenv("BYBIT_API_SECRET", "")

        private_ws = self._private_ws
        if private_ws is None and api_key and api_secret:
            try:
                from execution.bybit_private_ws import BybitPrivateWS
                testnet    = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
                private_ws = BybitPrivateWS(
                    api_key=api_key, api_secret=api_secret, testnet=testnet,
                )
                logger.info(
                    f"BybitLiveRunner: BybitPrivateWS built "
                    f"({'testnet' if testnet else 'mainnet'})"
                )
            except Exception as exc:
                logger.warning(f"BybitLiveRunner: BybitPrivateWS build failed: {exc}")

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
                private_ws=private_ws,
                order_manager=self._order_manager,
                checkpoint=self._checkpoint,
                adoption_config=adoption_cfg,
                alert_cfg=self._notifier_bus,
                on_cycle_restart=self.start_new_cycle,
                venue=cfg.venue,
                monitor_interval_s=cfg.market_trade_monitor_s,
                symbols=[cfg.symbol_y, cfg.symbol_x],
            )
            self._market_trade_handler.register_bot_symbol(cfg.symbol_y)
            self._market_trade_handler.register_bot_symbol(cfg.symbol_x)
            logger.info(
                f"BybitLiveRunner: MarketTradeHandler (WS) "
                f"tp={cfg.market_trade_tp_pct:.1%} sl={cfg.market_trade_sl_pct:.1%}"
            )
        except Exception as exc:
            logger.error(f"BybitLiveRunner: MarketTradeHandler build failed: {exc}")

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
            logger.warning(f"NotifierBus unavailable: {exc}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_main_loop(self) -> None:
        if self._ws_feed is not None:
            await self._run_ws_loop()
        else:
            logger.warning("BybitLiveRunner: no ws_feed — waiting for stop.")
            await self._stop_event.wait()

    async def _run_ws_loop(self) -> None:
        from execution.integration_loop import IntegrationLoopConfig, IntegrationLoop
        cfg      = self.cfg
        loop_cfg = IntegrationLoopConfig(
            symbol_y=cfg.symbol_y,
            symbol_x=cfg.symbol_x,
            venue=cfg.venue,
            entry_zscore=cfg.entry_zscore,
            exit_zscore=cfg.exit_zscore,
            base_qty=cfg.base_qty,
            dry_run=cfg.dry_run,
            bar_interval_s=0.0,
            funding_gate_max_net_ann=(
                cfg.funding_gate_max_net_ann if cfg.funding_gate_enabled else None
            ),
        )
        self._intloop = IntegrationLoop(
            cfg=loop_cfg,
            kalman=self._kalman,
            spread_monitor=self._spread_monitor,
            regime_filter=self._regime_filter,
            order_manager=self._order_manager,
            notifier_bus=self._notifier_bus,
            funding_monitor=self._funding_monitor,
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
                            logger.info(f"Warmup complete ({cfg.warmup_bars} bars)")
                        else:
                            await self._intloop._process_bar(bar)
                            continue

                    result = await self._intloop._process_bar(bar)

                    if result.order_submitted:
                        self._stats["orders_submitted"] += 1
                        if self._market_trade_handler:
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
                                "funding_blocked":  result.funding_blocked,
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
                    f"WS error (reconnect {reconnects}/{cfg.ws_max_reconnects}): {exc}"
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
            logger.error("Max reconnects reached, stopping")
            await self.stop()

    # ------------------------------------------------------------------
    # Cleanup + shutdown
    # ------------------------------------------------------------------

    async def _cleanup_tasks(self) -> None:
        for task in (
            self._watchdog_task,
            self._funding_task,
            self._pnl_task,
            self._mth_task,
        ):
            if task and not task.done():
                task.cancel()
        if self._market_trade_handler is not None:
            await self._market_trade_handler.stop()
        if self._health_server is not None:
            try:
                if hasattr(self._health_server, "cleanup"):
                    await self._health_server.cleanup()
                elif hasattr(self._health_server, "stop"):
                    await self._health_server.stop()
            except Exception:
                pass

    async def _notify_startup(self) -> None:
        if self._notifier_bus is None:
            return
        try:
            mode  = "DRY" if self.cfg.dry_run else "LIVE"
            extra = " +WS +PnLRec +Health +Funding" if not self.cfg.dry_run else ""
            await self._notifier_bus.send_alert(
                f"\U0001f680 QuantLuna [{mode}{extra}] "
                f"{self.cfg.symbol_y}/{self.cfg.symbol_x} "
                f"interval={self.cfg.interval}m",
                level="info",
            )
        except Exception:
            pass

    async def _shutdown(self) -> None:
        logger.info(f"BybitLiveRunner: shutdown | {self.status()}")
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception:
                pass
        if self._order_manager is not None:
            try:
                await self._order_manager.stop()
            except Exception:
                pass
        if self._notifier_bus is not None and not self.cfg.dry_run:
            try:
                await self._notifier_bus.send_alert(
                    f"\U0001f6d1 QuantLuna stopped. "
                    f"bars={self._stats['bars_processed']} "
                    f"orders={self._stats['orders_submitted']} "
                    f"pnl_alerts={self._stats['pnl_drift_alerts']} "
                    f"restarts={self._stats['cycle_restarts']} "
                    f"errors={self._stats['errors']}",
                    level="info",
                )
            except Exception:
                pass

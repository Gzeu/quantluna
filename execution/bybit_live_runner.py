"""
QuantLuna — Bybit Live Runner (Sprint 21 + Sprint 28 rev-3)

Sprint 28 rev-3 refactor:
  - _build_exchange_via_factory()  -> delegates to ExchangeFactory class
  - _start_health_server()         -> delegates to HealthCheck.start_http_server()
                                     (falls back to inline aiohttp if not available)
  - _watchdog_loop()               -> delegates to WsWatchdog class
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
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

if TYPE_CHECKING:
    from execution.bybit_order_router import BybitOrderRouter
    from execution.bybit_ws_feed import BybitWsFeed

from core.spread_monitor import SpreadMonitor
from execution.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from execution.exchange_factory import ExchangeFactory, get_order_router, get_ws_feed
from execution.health_check import HealthCheck, HealthCheckConfig, HealthStatus
from execution.order_manager import OrderManager, OrderManagerConfig
from execution.watchdog import WsWatchdog, WsWatchdogConfig

from notifications.notifier_bus import NotifierBus


# =============================================================================
# Config
# =============================================================================

@dataclass
class BybitLiveRunnerConfig:
    """Complete runtime configuration from env vars + defaults."""

    # Symbol pair
    symbol_y: str = os.getenv("SYMBOL_Y", "BTCUSDT")
    symbol_x: str = os.getenv("SYMBOL_X", "ETHUSDT")
    interval: int = int(os.getenv("INTERVAL", "5"))

    # Dry-run / paper trading
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # API Keys (testnet vs mainnet)
    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    testnet: bool = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    # Entry / Exit
    entry_zscore: float = float(os.getenv("ENTRY_ZSCORE", "2.0"))
    exit_zscore: float = float(os.getenv("EXIT_ZSCORE", "0.5"))
    base_qty: float = float(os.getenv("BASE_QTY", "0.01"))

    # Model parameters
    warmup_bars: int = int(os.getenv("WARMUP_BARS", "100"))
    kalman_window: int = int(os.getenv("KALMAN_WINDOW", "200"))
    half_life_h: float = float(os.getenv("HALF_LIFE_H", "24.0"))

    # Risk management
    max_consec_losses: int = int(os.getenv("MAX_CONSEC_LOSSES", "3"))
    max_drawdown_pct: float = float(os.getenv("MAX_DRAWDOWN_PCT", "10.0"))
    cooldown_seconds: int = int(os.getenv("COOLDOWN_SECONDS", "300"))

    # Notifications
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")

    # Health server
    health_port: int = int(os.getenv("HEALTH_PORT", "8081"))

    # Subsystem toggles (Sprint 28)
    funding_gate_enabled: bool = os.getenv("FUNDING_GATE_ENABLED", "true").lower() == "true"
    pnl_reconciler_enabled: bool = os.getenv("PNL_RECONCILER_ENABLED", "true").lower() == "true"
    market_trade_enabled: bool = os.getenv("MARKET_TRADE_ENABLED", "true").lower() == "true"

    # Checkpoint
    checkpoint_path: str = os.getenv("CHECKPOINT_PATH", "position_checkpoint.db")

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        return cls()


# =============================================================================
# Runner
# =============================================================================

@dataclass
class RunnerContext:
    """Shared context object passed between phases."""
    should_halt: bool = False
    halt_reason: str = ""
    order_router: Optional[Any] = None
    ws_feed: Optional[Any] = None
    spread_monitor: Optional[SpreadMonitor] = None
    circuit_breaker: Optional[CircuitBreaker] = None
    order_manager: Optional[OrderManager] = None
    watchdog: Optional[WsWatchdog] = None
    notifier_bus: Optional[NotifierBus] = None


class BybitLiveRunner:
    """
    Main live trading loop.

    Sprint 28 refactor: all heavy logic has been delegated to dedicated classes.
    This class remains the orchestrator: it builds, wires, and runs the pipeline.
    """

    def __init__(self, cfg: BybitLiveRunnerConfig) -> None:
        self.cfg = cfg
        self._stop_event: asyncio.Event = asyncio.Event()
        self._state: dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg: BybitLiveRunnerConfig) -> "BybitLiveRunner":
        return cls(cfg)

    # -------------------------------------------------------------------------
    # Phase 1: Build exchange clients
    # -------------------------------------------------------------------------

    async def _build_exchange_via_factory(self) -> tuple[Any, Any]:
        """
        Build BybitOrderRouter + BybitWsFeed via ExchangeFactory.
        Falls back to direct instantiation if factory is unavailable.
        """
        try:
            order_router = get_order_router(
                api_key=self.cfg.api_key,
                api_secret=self.cfg.api_secret,
                testnet=self.cfg.testnet,
                dry_run=self.cfg.dry_run,
            )
            ws_feed = get_ws_feed(
                symbol=self.cfg.symbol_y,
                interval=self.cfg.interval,
                testnet=self.cfg.testnet,
            )
            logger.info("BybitLiveRunner: Exchange clients built via ExchangeFactory")
            return order_router, ws_feed
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: ExchangeFactory failed ({exc}), falling back")
            # Fallback: direct instantiation (legacy code path)
            from execution.bybit_order_router import BybitOrderRouter, BybitOrderRouterConfig
            from execution.bybit_ws_feed import BybitWsFeed, BybitWsFeedConfig
            router_cfg = BybitOrderRouterConfig(
                api_key=self.cfg.api_key,
                api_secret=self.cfg.api_secret,
                testnet=self.cfg.testnet,
                dry_run=self.cfg.dry_run,
            )
            order_router = BybitOrderRouter(router_cfg)
            feed_cfg = BybitWsFeedConfig(
                symbol=self.cfg.symbol_y,
                interval=self.cfg.interval,
                testnet=self.cfg.testnet,
            )
            ws_feed = BybitWsFeed.from_config(feed_cfg)
            return order_router, ws_feed

    # -------------------------------------------------------------------------
    # Phase 2: Build shared components
    # -------------------------------------------------------------------------

    async def _build_components(
        self, order_router: Any, ws_feed: Any
    ) -> tuple[SpreadMonitor, CircuitBreaker, OrderManager, WsWatchdog, NotifierBus]:
        """Build SpreadMonitor, CircuitBreaker, OrderManager, Watchdog, NotifierBus."""
        # SpreadMonitor
        spread_monitor = SpreadMonitor(
            symbol_y=self.cfg.symbol_y,
            symbol_x=self.cfg.symbol_x,
            window=self.cfg.kalman_window,
            half_life_h=self.cfg.half_life_h,
            warmup_bars=self.cfg.warmup_bars,
        )

        # CircuitBreaker
        cb_cfg = CircuitBreakerConfig(
            max_consec_losses=self.cfg.max_consec_losses,
            max_drawdown_pct=self.cfg.max_drawdown_pct,
            cooldown_seconds=self.cfg.cooldown_seconds,
        )
        circuit_breaker = CircuitBreaker(cb_cfg)

        # OrderManager
        om_cfg = OrderManagerConfig(
            base_qty=self.cfg.base_qty,
            entry_zscore=self.cfg.entry_zscore,
            exit_zscore=self.cfg.exit_zscore,
            dry_run=self.cfg.dry_run,
        )
        order_manager = OrderManager(om_cfg)

        # Watchdog
        wd_cfg = WsWatchdogConfig(
            interval_seconds=30,
            max_missed_pings=3,
            reconnect_delay=5.0,
        )
        watchdog = WsWatchdog(ws_feed, wd_cfg)

        # NotifierBus
        notifier_bus = NotifierBus(fail_silent=True)
        if self.cfg.telegram_bot_token and self.cfg.telegram_chat_id:
            try:
                from notifications.telegram import TelegramNotifier
                notifier_bus.register("telegram", TelegramNotifier(
                    token=self.cfg.telegram_bot_token,
                    chat_id=self.cfg.telegram_chat_id,
                ))
            except Exception as exc:
                logger.warning(f"NotifierBus: Telegram setup failed: {exc}")
        if self.cfg.slack_webhook_url:
            try:
                from notifications.slack_notifier import SlackNotifier, SlackConfig
                notifier_bus.register("slack", SlackNotifier(
                    SlackConfig(webhook_url=self.cfg.slack_webhook_url)
                ))
            except Exception as exc:
                logger.warning(f"NotifierBus: Slack setup failed: {exc}")

        return spread_monitor, circuit_breaker, order_manager, watchdog, notifier_bus

    # -------------------------------------------------------------------------
    # Phase 3: Start health server
    # -------------------------------------------------------------------------

    async def _start_health_server(
        self, components: dict[str, Any]
    ) -> HealthCheck:
        """
        Start HTTP health server via HealthCheck class.
        Falls back to inline aiohttp server if HealthCheck.start_http_server fails.
        """
        hc_cfg = HealthCheckConfig(
            port=self.cfg.health_port,
            check_interval=10.0,
        )
        health = HealthCheck.from_components(components, hc_cfg)

        try:
            await health.start_http_server()
            logger.info(
                f"BybitLiveRunner: Health server started on port {self.cfg.health_port}"
            )
        except Exception as exc:
            logger.warning(
                f"BybitLiveRunner: HealthCheck.start_http_server failed ({exc}) "
                f"— falling back to inline aiohttp"
            )
            # Fallback: inline aiohttp health server (Sprint 21 code)
            try:
                from aiohttp import web
                app = web.Application()
                async def handle_health(request):
                    status = HealthStatus.HEALTHY
                    for name, checker in components.items():
                        try:
                            if not checker.is_healthy():
                                status = HealthStatus.DEGRADED
                                break
                        except Exception:
                            status = HealthStatus.UNHEALTHY
                            break
                    status_code = 200 if status == HealthStatus.HEALTHY else 503
                    return web.json_response(
                        {
                            "status": status.value,
                            "timestamp": datetime.utcnow().isoformat(),
                            "details": {name: checker.status() for name, checker in components.items()},
                        },
                        status=status_code,
                    )
                app.router.add_get("/api/health", handle_health)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, port=self.cfg.health_port)
                await site.start()
                logger.info(f"BybitLiveRunner: Fallback health server on port {self.cfg.health_port}")
            except Exception as exc2:
                logger.error(f"BybitLiveRunner: Health server failed completely: {exc2}")

        return health

    # -------------------------------------------------------------------------
    # Phase 4: Main trading loop
    # -------------------------------------------------------------------------

    async def _run_loop(
        self,
        order_router: Any,
        ws_feed: Any,
        spread_monitor: SpreadMonitor,
        circuit_breaker: CircuitBreaker,
        order_manager: OrderManager,
        watchdog: WsWatchdog,
        health: HealthCheck,
        notifier_bus: NotifierBus,
    ) -> None:
        """
        Main trading loop: listen to WS feed, compute z-score, execute trades.
        """
        logger.info("BybitLiveRunner: Starting main trading loop...")

        # Wire watchdog
        watchdog.set_health_checker(health)

        # Start watchdog
        watchdog_task = asyncio.create_task(watchdog.start())

        # Main loop
        first_bar = True
        while not self._stop_event.is_set():
            try:
                # Read bar from feed
                bar = await ws_feed.get_bar()
                if bar is None:
                    await asyncio.sleep(0.1)
                    continue

                # Process bar through SpreadMonitor
                spread_monitor.update(bar.price_y, bar.price_x)
                zscore = spread_monitor.zscore
                spread = spread_monitor.spread

                if first_bar:
                    logger.info(
                        f"BybitLiveRunner: First bar received | "
                        f"spread={spread:.6f} | zscore={zscore:.4f}"
                    )
                    first_bar = False

                # Circuit breaker check
                if circuit_breaker.is_open():
                    logger.warning(
                        f"BybitLiveRunner: Circuit breaker OPEN | "
                        f"remaining={circuit_breaker.remaining_cooldown:.1f}s"
                    )
                    await asyncio.sleep(1.0)
                    continue

                # Check funding rate gate (Sprint 28 feature)
                if self.cfg.funding_gate_enabled:
                    funding_ok = self._check_funding_gate(ws_feed)
                    if not funding_ok:
                        logger.info("BybitLiveRunner: Funding gate CLOSED — skipping trade")
                        continue

                # Decision matrix
                action = self._decide(
                    zscore=zscore,
                    spread=spread,
                    circuit_breaker=circuit_breaker,
                    order_manager=order_manager,
                    market_trade_enabled=self.cfg.market_trade_enabled,
                )

                if action:
                    await self._execute_action(
                        action=action,
                        order_router=order_router,
                        order_manager=order_manager,
                        notifier_bus=notifier_bus,
                        bar=bar,
                    )

            except asyncio.CancelledError:
                logger.info("BybitLiveRunner: Loop cancelled")
                break
            except KeyboardInterrupt:
                logger.info("BybitLiveRunner: Keyboard interrupt")
                break
            except Exception as exc:
                logger.error(f"BybitLiveRunner: Loop error: {exc}")
                await asyncio.sleep(1.0)

        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass

    def _check_funding_gate(self, ws_feed: Any) -> bool:
        """Check if funding rates allow trading (Sprint 28 feature)."""
        try:
            from execution.funding_monitor import FundingMonitor
            fm = FundingMonitor(ws_feed)
            y_funding = fm.get_funding_rate(self.cfg.symbol_y)
            x_funding = fm.get_funding_rate(self.cfg.symbol_x)
            if y_funding is not None and x_funding is not None:
                # Simple gate: skip if either funding is strongly negative
                if y_funding < -0.01 or x_funding < -0.01:
                    logger.debug(
                        f"BybitLiveRunner: Funding gate | {self.cfg.symbol_y}={y_funding:.4f} | "
                        f"{self.cfg.symbol_x}={x_funding:.4f}"
                    )
                    return False
            return True
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: Funding gate check failed: {exc}")
            return True  # Fail open

    def _decide(
        self,
        zscore: float,
        spread: float,
        circuit_breaker: CircuitBreaker,
        order_manager: OrderManager,
        market_trade_enabled: bool,
    ) -> Optional[str]:
        """
        Simple decision matrix based on z-score.
        Returns: 'entry_long', 'entry_short', 'exit', or None.
        """
        if not market_trade_enabled:
            return None

        if circuit_breaker.is_open():
            return None

        # Entry logic
        if abs(zscore) >= self.cfg.entry_zscore:
            if zscore > 0:
                return "entry_short"  # z > 0 means Y is expensive relative to X -> short Y, long X
            else:
                return "entry_long"   # z < 0 means Y is cheap relative to X -> long Y, short X

        # Exit logic
        if abs(zscore) <= self.cfg.exit_zscore:
            if order_manager.has_position():
                return "exit"

        return None

    async def _execute_action(
        self,
        action: str,
        order_router: Any,
        order_manager: OrderManager,
        notifier_bus: NotifierBus,
        bar: Any,
    ) -> None:
        """Execute a trading action: entry_long, entry_short, or exit."""
        from execution.bybit_order_router import OrderRequest, OrderSide, OrderType

        if self.cfg.dry_run:
            logger.info(
                f"BybitLiveRunner: [DRY RUN] {action.upper()} | "
                f"zscore={bar.zscore:.4f} | spread={bar.spread:.6f}"
            )
            return

        try:
            if action == "entry_long":
                # Long Y, Short X
                req_y = OrderRequest(
                    symbol=self.cfg.symbol_y,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    qty=self.cfg.base_qty,
                    price=0.0,
                )
                req_x = OrderRequest(
                    symbol=self.cfg.symbol_x,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    qty=self.cfg.base_qty * bar.price_y / bar.price_x,
                    price=0.0,
                )
                await order_router.create_order(req_y)
                await order_router.create_order(req_x)
                order_manager.record_entry_long(self.cfg.base_qty, bar.price_y, bar.price_x)
                logger.info(
                    f"BybitLiveRunner: ENTRY LONG executed | "
                    f"Long {self.cfg.symbol_y} @ {bar.price_y:.2f} | "
                    f"Short {self.cfg.symbol_x} @ {bar.price_x:.2f}"
                )
                if notifier_bus:
                    await notifier_bus.send_alert(
                        f"\u2705 ENTRY LONG: {self.cfg.symbol_y}/{self.cfg.symbol_x} | "
                        f"z={bar.zscore:.4f}",
                        level="success",
                    )

            elif action == "entry_short":
                # Short Y, Long X
                req_y = OrderRequest(
                    symbol=self.cfg.symbol_y,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    qty=self.cfg.base_qty,
                    price=0.0,
                )
                req_x = OrderRequest(
                    symbol=self.cfg.symbol_x,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    qty=self.cfg.base_qty * bar.price_y / bar.price_x,
                    price=0.0,
                )
                await order_router.create_order(req_y)
                await order_router.create_order(req_x)
                order_manager.record_entry_short(self.cfg.base_qty, bar.price_y, bar.price_x)
                logger.info(
                    f"BybitLiveRunner: ENTRY SHORT executed | "
                    f"Short {self.cfg.symbol_y} @ {bar.price_y:.2f} | "
                    f"Long {self.cfg.symbol_x} @ {bar.price_x:.2f}"
                )
                if notifier_bus:
                    await notifier_bus.send_alert(
                        f"\u2705 ENTRY SHORT: {self.cfg.symbol_y}/{self.cfg.symbol_x} | "
                        f"z={bar.zscore:.4f}",
                        level="success",
                    )

            elif action == "exit":
                pos = order_manager.current_position
                if pos:
                    # Close both legs
                    req_y = OrderRequest(
                        symbol=self.cfg.symbol_y,
                        side=OrderSide.SELL if pos.y_side == "long" else OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        qty=abs(pos.y_qty),
                        price=0.0,
                    )
                    req_x = OrderRequest(
                        symbol=self.cfg.symbol_x,
                        side=OrderSide.BUY if pos.x_side == "short" else OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        qty=abs(pos.x_qty),
                        price=0.0,
                    )
                    await order_router.create_order(req_y)
                    await order_router.create_order(req_x)
                    order_manager.record_exit(bar.price_y, bar.price_x)
                    logger.info(
                        f"BybitLiveRunner: EXIT executed | "
                        f"PnL={order_manager.current_pnl:.4f}"
                    )
                    if notifier_bus:
                        await notifier_bus.send_alert(
                            f"\u2705 EXIT: PnL={order_manager.current_pnl:.4f}",
                            level="success",
                        )

            # Update circuit breaker
            CircuitBreaker.update_from_trade(
                order_manager.current_pnl,
                order_manager.consec_losses,
            )

        except Exception as exc:
            logger.error(f"BybitLiveRunner: Execute action '{action}' failed: {exc}")
            CircuitBreaker.record_failure()
            if notifier_bus:
                try:
                    await notifier_bus.send_alert(
                        f"\u274c ACTION FAILED: {action} | Error: {exc}",
                        level="error",
                    )
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def run(self) -> int:
        """Full run: build, start, trade."""
        logger.info("BybitLiveRunner: ========== Starting Live Runner ==========")
        logger.info(
            f"BybitLiveRunner: Config | "
            f"symbol={self.cfg.symbol_y}/{self.cfg.symbol_x} | "
            f"interval={self.cfg.interval}m | "
            f"dry_run={self.cfg.dry_run}"
        )

        # Phase 1: Build exchange clients
        order_router, ws_feed = await self._build_exchange_via_factory()

        # Phase 2: Build components
        (
            spread_monitor,
            circuit_breaker,
            order_manager,
            watchdog,
            notifier_bus,
        ) = await self._build_components(order_router, ws_feed)

        # Register with notifier bus
        if notifier_bus:
            await notifier_bus.send_alert(
                f"\u26a1 QuantLuna Started | {self.cfg.symbol_y}/{self.cfg.symbol_x} | "
                f"dry_run={self.cfg.dry_run}",
                level="info",
            )

        # Phase 3: Start health server
        components_for_health = {
            "spread_monitor": spread_monitor,
            "circuit_breaker": circuit_breaker,
            "order_manager": order_manager,
            "ws_feed": ws_feed,
            "watchdog": watchdog,
        }
        health = await self._start_health_server(components_for_health)

        # Phase 4: Run main loop
        await self._run_loop(
            order_router=order_router,
            ws_feed=ws_feed,
            spread_monitor=spread_monitor,
            circuit_breaker=circuit_breaker,
            order_manager=order_manager,
            watchdog=watchdog,
            health=health,
            notifier_bus=notifier_bus,
        )

        logger.info("BybitLiveRunner: Runner stopped")
        return 0

    def stop(self) -> None:
        """Signal stop."""
        self._stop_event.set()

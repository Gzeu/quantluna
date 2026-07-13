"""
execution/bybit_live_runner.py  —  BybitLiveRunner (thin orchestrator)

Sprint 28 SRP refactor — this file is now a lean orchestrator (~140 LOC).
All extracted modules:

  execution/runner_config.py    —  BybitLiveRunnerConfig
  execution/runner_context.py   —  RunnerContext
  execution/funding_gate.py     —  FundingGate
  execution/decision_engine.py  —  DecisionEngine
  execution/action_executor.py  —  ActionExecutor

Backward-compatible re-exports so existing callers don't need changes::

    from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

# Re-export for backward compatibility
from execution.runner_config  import BybitLiveRunnerConfig  # noqa: F401
from execution.runner_context import RunnerContext           # noqa: F401

from core.spread_monitor       import SpreadMonitor
from execution.action_executor  import ActionExecutor
from execution.circuit_breaker  import CircuitBreaker, CircuitBreakerConfig
from execution.decision_engine  import DecisionEngine
from execution.exchange_factory import get_order_router, get_ws_feed
from execution.funding_gate     import FundingGate
from execution.health_check     import HealthCheck, HealthCheckConfig
from execution.order_manager    import OrderManager, OrderManagerConfig
from execution.ws_watchdog       import WsWatchdog, WsWatchdogConfig
from notifications.notifier_bus import NotifierBus


class BybitLiveRunner:
    """
    Main live-trading orchestrator.

    Builds all subsystems, wires them together, then drives the event loop.
    Heavy logic lives in the extracted modules listed in the module docstring.
    """

    def __init__(
        self,
        cfg: BybitLiveRunnerConfig,
        exchange=None,
        private_ws=None,
        ws_feed=None,
        notifier_bus=None,
    ) -> None:
        self.cfg          = cfg
        self._exchange    = exchange
        self._private_ws  = private_ws
        self._ws_feed_ext = ws_feed
        self._bus_ext     = notifier_bus
        self._stop_event  = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> int:
        """Build, wire and run.  Blocks until stop() is called."""
        logger.info("BybitLiveRunner: === START === {}/{} dry={}",
                    self.cfg.symbol_y, self.cfg.symbol_x, self.cfg.dry_run)

        order_router, ws_feed = await self._build_exchange()
        (
            spread_monitor, circuit_breaker,
            order_manager, watchdog, notifier_bus,
        ) = await self._build_components(order_router, ws_feed)

        if notifier_bus:
            await notifier_bus.send_alert(
                f"\u26a1 QuantLuna Start | {self.cfg.symbol_y}/{self.cfg.symbol_x} "
                f"| dry={self.cfg.dry_run}",
                level="info",
            )

        health = await self._start_health_server({
            "spread_monitor":  spread_monitor,
            "circuit_breaker": circuit_breaker,
            "order_manager":   order_manager,
            "ws_feed":         ws_feed,
            "watchdog":        watchdog,
        })

        funding_gate    = FundingGate(sym_y=self.cfg.symbol_y, sym_x=self.cfg.symbol_x)
        decision_engine = DecisionEngine(
            entry_zscore=self.cfg.entry_zscore,
            exit_zscore=self.cfg.exit_zscore,
            market_trade_enabled=self.cfg.market_trade_enabled,
        )
        executor = ActionExecutor(
            sym_y=self.cfg.symbol_y,
            sym_x=self.cfg.symbol_x,
            base_qty=self.cfg.base_qty,
            dry_run=self.cfg.dry_run,
        )

        await self._run_loop(
            order_router, ws_feed,
            spread_monitor, circuit_breaker, order_manager, watchdog,
            health, notifier_bus,
            funding_gate, decision_engine, executor,
        )
        logger.info("BybitLiveRunner: stopped")
        return 0

    def stop(self) -> None:
        """Signal the run loop to exit cleanly."""
        self._stop_event.set()

    async def run(self) -> int:
        """Alias for start() — kept for backward compatibility."""
        return await self.start()

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    async def _build_exchange(self):
        """Build (or reuse injected) order-router and WS feed."""
        if self._exchange is not None and self._ws_feed_ext is not None:
            return self._exchange, self._ws_feed_ext
        try:
            return (
                get_order_router(
                    api_key=self.cfg.api_key,
                    api_secret=self.cfg.api_secret,
                    testnet=self.cfg.testnet,
                    dry_run=self.cfg.dry_run,
                ),
                self._ws_feed_ext or get_ws_feed(
                    symbol=self.cfg.symbol_y,
                    interval=self.cfg.interval,
                    testnet=self.cfg.testnet,
                ),
            )
        except Exception as exc:
            logger.warning("ExchangeFactory failed: {} — direct fallback", exc)
            from execution.bybit_order_router import BybitOrderRouter, BybitOrderRouterConfig
            from execution.bybit_ws_feed      import BybitWsFeed, BybitWsFeedConfig
            return (
                BybitOrderRouter(BybitOrderRouterConfig(
                    api_key=self.cfg.api_key, api_secret=self.cfg.api_secret,
                    testnet=self.cfg.testnet,  dry_run=self.cfg.dry_run,
                )),
                BybitWsFeed.from_config(BybitWsFeedConfig(
                    symbol=self.cfg.symbol_y, interval=self.cfg.interval,
                    testnet=self.cfg.testnet,
                )),
            )

    async def _build_components(self, order_router, ws_feed):
        """Instantiate SpreadMonitor, CircuitBreaker, OrderManager, Watchdog, NotifierBus."""
        from core.spread_monitor import SpreadMonitorConfig
        spread_monitor  = SpreadMonitor(SpreadMonitorConfig(
            min_bars=self.cfg.warmup_bars,
            zscore_control_limit=4.5,
            max_half_life_hours=120.0,
            stuck_bars_threshold=60,
        ))
        circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout_s=300.0,
            half_open_max_calls=1,
            name="trading_circuit",
        ))
        order_manager   = OrderManager(OrderManagerConfig(
            dry_run=self.cfg.dry_run,
        ))
        watchdog        = WsWatchdog(WsWatchdogConfig(
            stale_warn_s=10.0,
            stale_critical_s=30.0,
            check_interval_s=2.0,
        ), bus=None)

        notifier_bus = self._bus_ext or NotifierBus(fail_silent=True)
        if not self._bus_ext:
            if self.cfg.telegram_bot_token and self.cfg.telegram_chat_id:
                try:
                    from notifications.telegram import TelegramNotifier
                    notifier_bus.register("telegram", TelegramNotifier(
                        token=self.cfg.telegram_bot_token,
                        chat_id=self.cfg.telegram_chat_id,
                    ))
                except Exception as exc:
                    logger.warning("Telegram setup failed: {}", exc)
            if self.cfg.slack_webhook_url:
                try:
                    from notifications.slack_notifier import SlackNotifier, SlackConfig
                    notifier_bus.register("slack", SlackNotifier(
                        SlackConfig(webhook_url=self.cfg.slack_webhook_url)
                    ))
                except Exception as exc:
                    logger.warning("Slack setup failed: {}", exc)

        return spread_monitor, circuit_breaker, order_manager, watchdog, notifier_bus

    async def _start_health_server(self, components: dict) -> HealthCheck:
        """Start HealthCheck HTTP server; fall back to inline aiohttp on failure."""
        hc = HealthCheck(HealthCheckConfig(
            exchange=self.cfg.venue,
            sym_y=self.cfg.symbol_y,
            sym_x=self.cfg.symbol_x,
        ))
        try:
            await hc.start_http_server()
            logger.info("BybitLiveRunner: health server on :{}", self.cfg.health_port)
        except Exception as exc:
            logger.warning("HealthCheck server failed: {} — inline fallback", exc)
            try:
                from aiohttp import web
                app = web.Application()
                async def _h(_): return web.json_response({"status": "ok"})
                app.router.add_get("/api/health", _h)
                runner_ = web.AppRunner(app)
                await runner_.setup()
                await web.TCPSite(runner_, port=self.cfg.health_port).start()
                logger.info("BybitLiveRunner: fallback health on :{}", self.cfg.health_port)
            except Exception as exc2:
                logger.error("Health server failed completely: {}", exc2)
        return hc

    # ------------------------------------------------------------------
    # Main trading loop
    # ------------------------------------------------------------------

    async def _run_loop(
        self,
        order_router, ws_feed,
        spread_monitor, circuit_breaker, order_manager,
        watchdog, health, notifier_bus,
        funding_gate: FundingGate,
        decision_engine: DecisionEngine,
        executor: ActionExecutor,
    ) -> None:
        watchdog_task = asyncio.create_task(watchdog.run())
        first_bar = True

        while not self._stop_event.is_set():
            try:
                bar = await ws_feed.get_bar()
                if bar is None:
                    await asyncio.sleep(0.1)
                    continue

                # Calculate spread manually for new SpreadMonitor API
                spread = bar.price_y / bar.price_x
                zscore = 0.0  # Would need Kalman for real zscore
                half_life = 24.0  # Default half-life
                report = spread_monitor.update(spread, zscore, half_life)
                zscore = report.zscore

                if first_bar:
                    logger.info(
                        "BybitLiveRunner: first bar | spread={:.6f} z={:.4f}",
                        spread, zscore,
                    )
                    first_bar = False

                if not circuit_breaker.is_available():
                    logger.warning(
                        "BybitLiveRunner: circuit OPEN — blocking trades"
                    )
                    await asyncio.sleep(1.0)
                    continue

                if self.cfg.funding_gate_enabled and not funding_gate.is_open(ws_feed):
                    logger.info("BybitLiveRunner: funding gate CLOSED")
                    continue

                action = decision_engine.decide(zscore, circuit_breaker, order_manager)
                if action:
                    await executor.execute(
                        action, order_router, order_manager, notifier_bus, bar
                    )

            except asyncio.CancelledError:
                logger.info("BybitLiveRunner: loop cancelled")
                break
            except Exception as exc:
                logger.error("BybitLiveRunner: loop error: {}", exc)
                await asyncio.sleep(1.0)

        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass

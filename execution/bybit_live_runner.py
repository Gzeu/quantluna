"""
execution/bybit_live_runner.py — QuantLuna Bybit Live Runner v3.0
Sprint S20 — 2026-07-11

Changelog:
  - _run_loop(): state_bus.publish("bar", {...}) după fiecare bar procesat
  - state_bus.publish("warmup_status", {...}) la fiecare 10 bare în warm-up
  - Metrici Prometheus actualizate în timp real (zscore, pnl, drawdown, circuit)
  - Alias start = run pentru compatibilitate cu WorkflowOrchestrator
  - vol_regime expus în payload (fallback "UNKNOWN" dacă modulul lipsește)
  - active_strategy expus în payload (fallback "kalman")

Toate comportamentele Sprint 21 + 28 rev-3 sunt păstrate.
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

try:
    from core.state_bus import bus as _state_bus
except ImportError:
    try:
        from state_bus import bus as _state_bus  # legacy shim
    except ImportError:
        _state_bus = None

try:
    from core.metrics import (
        spread_zscore,
        pnl_usdt as _pnl_metric,
        drawdown_pct as _drawdown_metric,
    )
    from core.metrics import registry as _metrics_registry
    _zscore_pair = _metrics_registry.gauge(
        "quantluna_zscore_pair", "Z-score per trading pair"
    )
    _circuit_open = _metrics_registry.gauge(
        "quantluna_circuit_breaker_open", "Circuit breaker open (1) or closed (0)"
    )
    _warmup_bars_done = _metrics_registry.gauge(
        "quantluna_warmup_bars_done", "Warm-up bars completed"
    )
    _HAS_METRICS = True
except Exception:
    _HAS_METRICS = False


# =============================================================================
# Config
# =============================================================================

@dataclass
class BybitLiveRunnerConfig:
    """Complete runtime configuration from env vars + defaults."""

    symbol_y: str = os.getenv("SYMBOL_Y", "BTCUSDT")
    symbol_x: str = os.getenv("SYMBOL_X", "ETHUSDT")
    interval: int = int(os.getenv("INTERVAL", "5"))

    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    testnet: bool = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    entry_zscore: float = float(os.getenv("ENTRY_ZSCORE", "2.0"))
    exit_zscore: float = float(os.getenv("EXIT_ZSCORE", "0.5"))
    base_qty: float = float(os.getenv("BASE_QTY", "0.01"))

    warmup_bars: int = int(os.getenv("WARMUP_BARS", "100"))
    kalman_window: int = int(os.getenv("KALMAN_WINDOW", "200"))
    half_life_h: float = float(os.getenv("HALF_LIFE_H", "24.0"))

    max_consec_losses: int = int(os.getenv("MAX_CONSEC_LOSSES", "3"))
    max_drawdown_pct: float = float(os.getenv("MAX_DRAWDOWN_PCT", "10.0"))
    cooldown_seconds: int = int(os.getenv("COOLDOWN_SECONDS", "300"))

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")

    health_port: int = int(os.getenv("HEALTH_PORT", "8081"))

    funding_gate_enabled: bool = os.getenv("FUNDING_GATE_ENABLED", "true").lower() == "true"
    pnl_reconciler_enabled: bool = os.getenv("PNL_RECONCILER_ENABLED", "true").lower() == "true"
    market_trade_enabled: bool = os.getenv("MARKET_TRADE_ENABLED", "true").lower() == "true"

    checkpoint_path: str = os.getenv("CHECKPOINT_PATH", "position_checkpoint.db")

    best_params_path: str = os.getenv("BEST_PARAMS_PATH", "best_params.json")
    state_bus_publish_interval: int = int(os.getenv("STATE_BUS_PUBLISH_INTERVAL", "1"))

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

    S20: Publică state în state_bus după fiecare bar pentru dashboard real-time.
    S28: Toate subsistemele delegate la clase dedicate.
    """

    def __init__(self, cfg: BybitLiveRunnerConfig) -> None:
        self.cfg = cfg
        self._stop_event: asyncio.Event = asyncio.Event()
        self._state: dict[str, Any] = {}
        self._bar_count: int = 0
        self._active_strategy: str = "kalman"

    @classmethod
    def from_config(cls, cfg: BybitLiveRunnerConfig) -> "BybitLiveRunner":
        return cls(cfg)

    # -------------------------------------------------------------------------
    # state_bus helpers
    # -------------------------------------------------------------------------

    def _publish_bar(
        self,
        bar: Any,
        spread: float,
        zscore: float,
        spread_monitor: SpreadMonitor,
        circuit_breaker: CircuitBreaker,
        order_manager: OrderManager,
    ) -> None:
        """Publică payload complet în state_bus după fiecare bar procesat."""
        if _state_bus is None:
            return

        warmup_pct = 0.0
        try:
            warmup_pct = getattr(spread_monitor, "warmup_progress", 0.0)
            if warmup_pct is None:
                bars_done = getattr(spread_monitor, "bars_count", 0)
                warmup_pct = min(1.0, bars_done / max(self.cfg.warmup_bars, 1))
        except Exception:
            pass

        vol_regime = "UNKNOWN"
        try:
            vr = getattr(spread_monitor, "vol_regime", None)
            if vr is not None:
                vol_regime = str(vr.value if hasattr(vr, "value") else vr)
        except Exception:
            pass

        current_pnl = 0.0
        try:
            current_pnl = float(order_manager.current_pnl or 0.0)
        except Exception:
            pass

        payload = {
            "ts": getattr(bar, "timestamp", int(time.time() * 1000)),
            "symbol_y": self.cfg.symbol_y,
            "symbol_x": self.cfg.symbol_x,
            "price_y": getattr(bar, "price_y", 0.0),
            "price_x": getattr(bar, "price_x", 0.0),
            "spread": spread,
            "zscore": zscore,
            "zscore_abs": abs(zscore),
            "vol_regime": vol_regime,
            "warmup_pct": warmup_pct,
            "warmup_done": warmup_pct >= 1.0,
            "circuit_open": circuit_breaker.is_open(),
            "active_strategy": self._active_strategy,
            "pnl": current_pnl,
            "dry_run": self.cfg.dry_run,
            "bar_count": self._bar_count,
        }

        try:
            _state_bus.publish("bar", payload)
        except Exception as exc:
            logger.debug(f"state_bus.publish bar failed: {exc}")

        # Actualizare metrici Prometheus
        if _HAS_METRICS:
            try:
                spread_zscore.set(zscore)
                _zscore_pair.set(abs(zscore))
                _pnl_metric.set(current_pnl)
                _circuit_open.set(1.0 if circuit_breaker.is_open() else 0.0)
                _warmup_bars_done.set(self._bar_count)
            except Exception:
                pass

    def _publish_warmup_status(
        self,
        spread_monitor: SpreadMonitor,
        coint_pvalue: float = 1.0,
    ) -> None:
        """Publică status warm-up în state_bus la fiecare 10 bare."""
        if _state_bus is None:
            return

        bars_done = getattr(spread_monitor, "bars_count", self._bar_count)
        bars_required = self.cfg.warmup_bars
        pct = min(1.0, bars_done / max(bars_required, 1))

        half_life_h = self.cfg.half_life_h
        try:
            hl = getattr(spread_monitor, "half_life", None)
            if hl is not None:
                half_life_h = float(hl)
        except Exception:
            pass

        vol_regime = "UNKNOWN"
        try:
            vr = getattr(spread_monitor, "vol_regime", None)
            if vr is not None:
                vol_regime = str(vr.value if hasattr(vr, "value") else vr)
        except Exception:
            pass

        try:
            _state_bus.publish("warmup_status", {
                "bars_done": bars_done,
                "bars_required": bars_required,
                "pct": round(pct, 4),
                "coint_pvalue": round(coint_pvalue, 6),
                "half_life_h": round(half_life_h, 2),
                "regime": vol_regime,
                "ready": pct >= 1.0,
                "ts": int(time.time() * 1000),
            })
        except Exception as exc:
            logger.debug(f"state_bus.publish warmup_status failed: {exc}")

    # -------------------------------------------------------------------------
    # Phase 1: Build exchange clients
    # -------------------------------------------------------------------------

    async def _build_exchange_via_factory(self) -> tuple[Any, Any]:
        """Build BybitOrderRouter + BybitWsFeed via ExchangeFactory."""
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
        spread_monitor = SpreadMonitor(
            symbol_y=self.cfg.symbol_y,
            symbol_x=self.cfg.symbol_x,
            window=self.cfg.kalman_window,
            half_life_h=self.cfg.half_life_h,
            warmup_bars=self.cfg.warmup_bars,
        )

        cb_cfg = CircuitBreakerConfig(
            max_consec_losses=self.cfg.max_consec_losses,
            max_drawdown_pct=self.cfg.max_drawdown_pct,
            cooldown_seconds=self.cfg.cooldown_seconds,
        )
        circuit_breaker = CircuitBreaker(cb_cfg)

        om_cfg = OrderManagerConfig(
            base_qty=self.cfg.base_qty,
            entry_zscore=self.cfg.entry_zscore,
            exit_zscore=self.cfg.exit_zscore,
            dry_run=self.cfg.dry_run,
        )
        order_manager = OrderManager(om_cfg)

        wd_cfg = WsWatchdogConfig(
            interval_seconds=30,
            max_missed_pings=3,
            reconnect_delay=5.0,
        )
        watchdog = WsWatchdog(ws_feed, wd_cfg)

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

    async def _start_health_server(self, components: dict[str, Any]) -> HealthCheck:
        """Start HTTP health server via HealthCheck class."""
        hc_cfg = HealthCheckConfig(
            port=self.cfg.health_port,
            check_interval=10.0,
        )
        health = HealthCheck.from_components(components, hc_cfg)

        try:
            await health.start_http_server()
            logger.info(f"BybitLiveRunner: Health server started on port {self.cfg.health_port}")
        except Exception as exc:
            logger.warning(
                f"BybitLiveRunner: HealthCheck.start_http_server failed ({exc}) — falling back"
            )
            try:
                from aiohttp import web
                aio_app = web.Application()

                async def handle_health(request):
                    status = HealthStatus.HEALTHY
                    for _, checker in components.items():
                        try:
                            if not checker.is_healthy():
                                status = HealthStatus.DEGRADED
                                break
                        except Exception:
                            status = HealthStatus.UNHEALTHY
                            break
                    sc = 200 if status == HealthStatus.HEALTHY else 503
                    return web.json_response(
                        {"status": status.value, "timestamp": datetime.utcnow().isoformat()},
                        status=sc,
                    )

                aio_app.router.add_get("/api/health", handle_health)
                runner = web.AppRunner(aio_app)
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
        """Main trading loop: ascultă WS feed, publică în state_bus, execută trades."""
        logger.info("BybitLiveRunner: Starting main trading loop...")

        watchdog.set_health_checker(health)
        watchdog_task = asyncio.create_task(watchdog.start())

        first_bar = True
        publish_counter = 0

        while not self._stop_event.is_set():
            try:
                bar = await ws_feed.get_bar()
                if bar is None:
                    await asyncio.sleep(0.1)
                    continue

                self._bar_count += 1

                spread_monitor.update(bar.price_y, bar.price_x)
                zscore = spread_monitor.zscore
                spread = spread_monitor.spread

                if first_bar:
                    logger.info(
                        f"BybitLiveRunner: First bar | spread={spread:.6f} | zscore={zscore:.4f}"
                    )
                    first_bar = False

                # Warm-up barrier — publică progres la fiecare 10 bare
                is_warmed_up = getattr(spread_monitor, "is_warmed_up", True)
                if not is_warmed_up:
                    if self._bar_count % 10 == 0:
                        self._publish_warmup_status(spread_monitor)
                        logger.info(
                            f"[Warm-up] {self._bar_count}/{self.cfg.warmup_bars} bare "
                            f"({100 * self._bar_count / self.cfg.warmup_bars:.0f}%)"
                        )
                    # Publică bar chiar și în warm-up (pentru grafice)
                    self._publish_bar(bar, spread, zscore, spread_monitor, circuit_breaker, order_manager)
                    continue

                # Publică warm-up complet la prima bara ready
                if self._bar_count == self.cfg.warmup_bars:
                    self._publish_warmup_status(spread_monitor, coint_pvalue=0.0)
                    logger.info("[Warm-up] COMPLETE — trading enabled")
                    if notifier_bus:
                        try:
                            await notifier_bus.send_alert(
                                f"✅ Warm-up complet ({self.cfg.warmup_bars} bare) — trading activ",
                                level="info",
                            )
                        except Exception:
                            pass

                # Circuit breaker check
                if circuit_breaker.is_open():
                    logger.warning(
                        f"BybitLiveRunner: Circuit breaker OPEN | "
                        f"remaining={circuit_breaker.remaining_cooldown:.1f}s"
                    )
                    self._publish_bar(bar, spread, zscore, spread_monitor, circuit_breaker, order_manager)
                    await asyncio.sleep(1.0)
                    continue

                # Funding gate
                if self.cfg.funding_gate_enabled:
                    if not self._check_funding_gate(ws_feed):
                        logger.info("BybitLiveRunner: Funding gate CLOSED")
                        self._publish_bar(bar, spread, zscore, spread_monitor, circuit_breaker, order_manager)
                        continue

                # Decision
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

                # ── S20: Publică bar în state_bus după fiecare bar procesat ──
                publish_counter += 1
                if publish_counter >= self.cfg.state_bus_publish_interval:
                    self._publish_bar(
                        bar, spread, zscore, spread_monitor, circuit_breaker, order_manager
                    )
                    publish_counter = 0

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
        """Check funding rates (Sprint 28 feature)."""
        try:
            from execution.funding_monitor import FundingMonitor
            fm = FundingMonitor(ws_feed)
            y_funding = fm.get_funding_rate(self.cfg.symbol_y)
            x_funding = fm.get_funding_rate(self.cfg.symbol_x)
            if y_funding is not None and x_funding is not None:
                if y_funding < -0.01 or x_funding < -0.01:
                    return False
            return True
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: Funding gate check failed: {exc}")
            return True

    def _decide(
        self,
        zscore: float,
        spread: float,
        circuit_breaker: CircuitBreaker,
        order_manager: OrderManager,
        market_trade_enabled: bool,
    ) -> Optional[str]:
        """Decision matrix based on z-score."""
        if not market_trade_enabled:
            return None
        if circuit_breaker.is_open():
            return None
        if abs(zscore) >= self.cfg.entry_zscore:
            return "entry_short" if zscore > 0 else "entry_long"
        if abs(zscore) <= self.cfg.exit_zscore and order_manager.has_position():
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
        """Execute trade action."""
        from execution.bybit_order_router import OrderRequest, OrderSide, OrderType

        if self.cfg.dry_run:
            logger.info(f"BybitLiveRunner: [DRY RUN] {action.upper()}")
            return

        try:
            if action == "entry_long":
                req_y = OrderRequest(symbol=self.cfg.symbol_y, side=OrderSide.BUY,
                    order_type=OrderType.MARKET, qty=self.cfg.base_qty, price=0.0)
                req_x = OrderRequest(symbol=self.cfg.symbol_x, side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    qty=self.cfg.base_qty * bar.price_y / bar.price_x, price=0.0)
                await order_router.create_order(req_y)
                await order_router.create_order(req_x)
                order_manager.record_entry_long(self.cfg.base_qty, bar.price_y, bar.price_x)
                logger.info(f"BybitLiveRunner: ENTRY LONG | {self.cfg.symbol_y}@{bar.price_y:.2f}")
                if notifier_bus:
                    await notifier_bus.send_alert(
                        f"✅ ENTRY LONG: {self.cfg.symbol_y}/{self.cfg.symbol_x}", level="success")

            elif action == "entry_short":
                req_y = OrderRequest(symbol=self.cfg.symbol_y, side=OrderSide.SELL,
                    order_type=OrderType.MARKET, qty=self.cfg.base_qty, price=0.0)
                req_x = OrderRequest(symbol=self.cfg.symbol_x, side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    qty=self.cfg.base_qty * bar.price_y / bar.price_x, price=0.0)
                await order_router.create_order(req_y)
                await order_router.create_order(req_x)
                order_manager.record_entry_short(self.cfg.base_qty, bar.price_y, bar.price_x)
                logger.info(f"BybitLiveRunner: ENTRY SHORT | {self.cfg.symbol_y}@{bar.price_y:.2f}")
                if notifier_bus:
                    await notifier_bus.send_alert(
                        f"✅ ENTRY SHORT: {self.cfg.symbol_y}/{self.cfg.symbol_x}", level="success")

            elif action == "exit":
                pos = order_manager.current_position
                if pos:
                    req_y = OrderRequest(symbol=self.cfg.symbol_y,
                        side=OrderSide.SELL if pos.y_side == "long" else OrderSide.BUY,
                        order_type=OrderType.MARKET, qty=abs(pos.y_qty), price=0.0)
                    req_x = OrderRequest(symbol=self.cfg.symbol_x,
                        side=OrderSide.BUY if pos.x_side == "short" else OrderSide.SELL,
                        order_type=OrderType.MARKET, qty=abs(pos.x_qty), price=0.0)
                    await order_router.create_order(req_y)
                    await order_router.create_order(req_x)
                    order_manager.record_exit(bar.price_y, bar.price_x)
                    logger.info(f"BybitLiveRunner: EXIT | PnL={order_manager.current_pnl:.4f}")
                    if notifier_bus:
                        await notifier_bus.send_alert(
                            f"✅ EXIT: PnL={order_manager.current_pnl:.4f}", level="success")

            CircuitBreaker.update_from_trade(
                order_manager.current_pnl, order_manager.consec_losses)

        except Exception as exc:
            logger.error(f"BybitLiveRunner: Execute action '{action}' failed: {exc}")
            CircuitBreaker.record_failure()
            if notifier_bus:
                try:
                    await notifier_bus.send_alert(f"❌ ACTION FAILED: {action} | {exc}", level="error")
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def run(self) -> int:
        """Full run: build, start, trade."""
        logger.info("BybitLiveRunner: ========== Starting Live Runner ==========")
        logger.info(
            f"BybitLiveRunner: {self.cfg.symbol_y}/{self.cfg.symbol_x} | "
            f"interval={self.cfg.interval}m | dry_run={self.cfg.dry_run}"
        )

        order_router, ws_feed = await self._build_exchange_via_factory()

        (
            spread_monitor, circuit_breaker, order_manager, watchdog, notifier_bus,
        ) = await self._build_components(order_router, ws_feed)

        if notifier_bus:
            await notifier_bus.send_alert(
                f"⚡ QuantLuna Started | {self.cfg.symbol_y}/{self.cfg.symbol_x} | "
                f"dry_run={self.cfg.dry_run}",
                level="info",
            )

        components_for_health = {
            "spread_monitor": spread_monitor,
            "circuit_breaker": circuit_breaker,
            "order_manager": order_manager,
            "ws_feed": ws_feed,
            "watchdog": watchdog,
        }
        health = await self._start_health_server(components_for_health)

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

    # Alias pentru WorkflowOrchestrator care apelează .start()
    start = run

    def stop(self) -> None:
        """Signal graceful stop."""
        self._stop_event.set()

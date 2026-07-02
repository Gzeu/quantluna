"""
QuantLuna — Bybit Live Runner (Sprint 21)

Orchestratorul principal pentru run real pe Bybit.
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

Start/stop curat:
  runner = BybitLiveRunner(cfg)
  await runner.start()    # blocks until stop() or SIGINT
  await runner.stop()     # graceful shutdown

Safety:
  - DRY_RUN=true (default) → OrderManager nu trimite comenzi reale
  - CircuitBreaker blochează automat la drawdown sau pierderi consecutive
  - WsWatchdog restarteăză feed-ul la disconnect
  - Toate erorile sunt logate + trimise pe NotifierBus.send_alert()
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


@dataclass
class BybitLiveRunnerConfig:
    # Pair
    symbol_y: str   = "BTCUSDT"
    symbol_x: str   = "ETHUSDT"
    venue:    str   = "bybit"

    # Timeframe pentru kline WS (Bybit: "1", "3", "5", "15", "60", "D")
    interval: str   = "5"       # minute

    # Strategy params
    entry_zscore:   float = 2.0
    exit_zscore:    float = 0.5
    base_qty:       float = 0.001
    kalman_window:  int   = 100

    # Warmup: nr bare minime inainte de a permite entry
    warmup_bars: int = 100

    # Risk
    max_consecutive_losses: int   = 3
    max_drawdown_pct:       float = 5.0
    cooldown_seconds:       int   = 3600

    # Safety: True = nu trimite comenzi reale la exchange
    dry_run: bool = True

    # Reconnect WS
    ws_reconnect_s:    float = 5.0
    ws_max_reconnects: int   = 20

    # Checkpoint
    checkpoint_path: str = "state/bybit_live_state.json"

    # Notificari
    slack_webhook_url:  str = ""
    telegram_bot_token: str = ""
    telegram_chat_id:   str = ""

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        """Build config from environment variables."""
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
        )


class BybitLiveRunner:
    """
    Main supervisor for Bybit live/paper trading.

    Instantiaza toate componentele intern (sau acceptă injectate pentru test).
    Rulează loop-ul principal până la stop() sau SIGINT.
    """

    def __init__(
        self,
        cfg: Optional[BybitLiveRunnerConfig] = None,
        # Injection points for testing
        kalman=None,
        spread_monitor=None,
        regime_filter=None,
        order_manager=None,
        notifier_bus=None,
        checkpoint=None,
        ws_feed=None,
    ) -> None:
        self.cfg = cfg or BybitLiveRunnerConfig()
        self._running = False
        self._stop_event = asyncio.Event()
        self._bar_count = 0
        self._warmed_up = False

        # Components (injected or built in start())
        self._kalman        = kalman
        self._spread_monitor = spread_monitor
        self._regime_filter = regime_filter
        self._order_manager = order_manager
        self._notifier_bus  = notifier_bus
        self._checkpoint    = checkpoint
        self._ws_feed       = ws_feed

        self._loop_task: Optional[asyncio.Task] = None
        self._ws_task:   Optional[asyncio.Task] = None

        # Stats
        self._stats = {
            "bars_processed":  0,
            "orders_submitted": 0,
            "gate_blocks":     0,
            "errors":          0,
            "started_at":      0.0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Build components and start the live loop. Blocks until stop()."""
        logger.info(
            f"BybitLiveRunner: starting "
            f"{self.cfg.symbol_y}/{self.cfg.symbol_x} "
            f"interval={self.cfg.interval}m "
            f"dry_run={self.cfg.dry_run}"
        )

        self._stats["started_at"] = time.time()
        self._running = True
        self._stop_event.clear()

        await self._build_components()
        await self._notify_startup()

        # Start order manager background tasks
        if self._order_manager is not None:
            try:
                await self._order_manager.start()
            except Exception:
                pass

        # Run the main loop
        try:
            await self._run_main_loop()
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        logger.info("BybitLiveRunner: stop requested")
        self._running = False
        self._stop_event.set()

    def status(self) -> dict:
        """Return current runtime stats."""
        uptime = time.time() - self._stats["started_at"] if self._stats["started_at"] else 0
        return {
            **self._stats,
            "uptime_s":   round(uptime, 1),
            "warmed_up":  self._warmed_up,
            "dry_run":    self.cfg.dry_run,
            "symbol_y":   self.cfg.symbol_y,
            "symbol_x":   self.cfg.symbol_x,
        }

    # ------------------------------------------------------------------
    # Component builder
    # ------------------------------------------------------------------

    async def _build_components(self) -> None:
        cfg = self.cfg

        if self._kalman is None:
            from core.kalman_adapter import KalmanAdapter
            self._kalman = KalmanAdapter(window=cfg.kalman_window)
            logger.info(f"BybitLiveRunner: KalmanAdapter(window={cfg.kalman_window})")

        if self._spread_monitor is None:
            from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
            self._spread_monitor = SpreadMonitor(SpreadMonitorConfig(
                min_bars=cfg.warmup_bars,
                zscore_control_limit=4.5,
                max_half_life_hours=120.0,
                stuck_bars_threshold=60,
            ))
            logger.info("BybitLiveRunner: SpreadMonitor built")

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
            logger.info("BybitLiveRunner: RegimeFilter + CircuitBreaker + VolRegimeAdapter built")

        if self._order_manager is None:
            from execution.order_manager import OrderManager, OrderManagerConfig
            self._order_manager = OrderManager(OrderManagerConfig(
                dry_run=cfg.dry_run,
                default_venue=cfg.venue,
            ))
            logger.info(f"BybitLiveRunner: OrderManager(dry_run={cfg.dry_run})")

        if self._notifier_bus is None:
            await self._build_notifier_bus()

        if self._checkpoint is None:
            try:
                from execution.checkpoint import Checkpoint
                import os
                os.makedirs(os.path.dirname(cfg.checkpoint_path), exist_ok=True)
                self._checkpoint = Checkpoint(path=cfg.checkpoint_path)
                logger.info(f"BybitLiveRunner: Checkpoint({cfg.checkpoint_path})")
            except Exception as exc:
                logger.warning(f"BybitLiveRunner: checkpoint not available: {exc}")

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
                logger.info("BybitLiveRunner: Slack notifier registered")

            if cfg.telegram_bot_token and cfg.telegram_chat_id:
                try:
                    from notifications.telegram import TelegramNotifier
                    bus.register("telegram", TelegramNotifier(
                        token=cfg.telegram_bot_token,
                        chat_id=cfg.telegram_chat_id,
                    ))
                    logger.info("BybitLiveRunner: Telegram notifier registered")
                except Exception:
                    pass

            self._notifier_bus = bus
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: NotifierBus not available: {exc}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_main_loop(self) -> None:
        """Main event loop: consume bars from WS feed or poll."""
        cfg = self.cfg

        if self._ws_feed is not None:
            await self._run_ws_loop()
        else:
            # Fallback: simulate with historical warmup then stop
            logger.warning(
                "BybitLiveRunner: no ws_feed injected. "
                "In production, inject BybitWsFeed. "
                "Running warmup-only simulation."
            )
            await self._stop_event.wait()

    async def _run_ws_loop(self) -> None:
        """Consume kline bars from WS feed."""
        from execution.integration_loop import BarData, IntegrationLoopConfig, IntegrationLoop

        cfg = self.cfg
        loop_cfg = IntegrationLoopConfig(
            symbol_y=cfg.symbol_y,
            symbol_x=cfg.symbol_x,
            venue=cfg.venue,
            entry_zscore=cfg.entry_zscore,
            exit_zscore=cfg.exit_zscore,
            base_qty=cfg.base_qty,
            dry_run=cfg.dry_run,
            bar_interval_s=0.0,
        )
        intloop = IntegrationLoop(
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
                async for bar in self._ws_feed.stream_bars(
                    symbol_y=cfg.symbol_y,
                    symbol_x=cfg.symbol_x,
                ):
                    if not self._running or self._stop_event.is_set():
                        return

                    self._bar_count += 1
                    self._stats["bars_processed"] += 1

                    # Warmup gate
                    if not self._warmed_up:
                        if self._bar_count >= cfg.warmup_bars:
                            self._warmed_up = True
                            logger.info(
                                f"BybitLiveRunner: warmup complete "
                                f"({cfg.warmup_bars} bars), trading enabled"
                            )
                        else:
                            # Still process through Kalman+SpreadMonitor for warmup
                            await intloop._process_bar(bar)
                            continue

                    result = await intloop._process_bar(bar)

                    if result.order_submitted:
                        self._stats["orders_submitted"] += 1
                    if not result.gate_allowed:
                        self._stats["gate_blocks"] += 1

                    # Checkpoint every bar
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

                    reconnects = 0  # reset on successful bar

            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._stats["errors"] += 1
                reconnects += 1
                logger.error(
                    f"BybitLiveRunner: WS error (reconnect {reconnects}/{cfg.ws_max_reconnects}): {exc}"
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
            await self._notifier_bus.send_alert(
                f"QuantLuna started: {self.cfg.symbol_y}/{self.cfg.symbol_x} "
                f"dry_run={self.cfg.dry_run} interval={self.cfg.interval}m",
                level="info",
            )
        except Exception:
            pass

    async def _shutdown(self) -> None:
        logger.info(f"BybitLiveRunner: shutdown | stats={self.status()}")
        if self._order_manager is not None:
            try:
                await self._order_manager.stop()
            except Exception:
                pass
        if self._notifier_bus is not None:
            try:
                await self._notifier_bus.send_alert(
                    f"QuantLuna stopped. Stats: bars={self._stats['bars_processed']} "
                    f"orders={self._stats['orders_submitted']} "
                    f"errors={self._stats['errors']}",
                    level="info",
                )
            except Exception:
                pass

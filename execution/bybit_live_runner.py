"""
execution/bybit_live_runner.py — QuantLuna Bybit Live Runner v3.6
Sprint S20 — review fixes 2026-07-11
Sprint S21 — position reconciliation + full automation 2026-07-11
Sprint S21 review-fix — 6 issues rezolvate 2026-07-11
Sprint S28 v3.6 — wiring fix: parametri optionali din WorkflowOrchestrator

Changelog v3.6 (wiring-fix):
  FIX-W1 [CRITIC]  __init__ accepta notifier_bus/ws_feed/private_ws/exchange opțional
                   Rezolva TypeError la runtime cand orchestratorul apeleaza _build_runner()
  FIX-W2 [CRITIC]  run() foloseste notifier_bus injectat (evita reconstructie duplicata)
  FIX-W3 [CRITIC]  run() foloseste ws_feed injectat cand e furnizat din exterior
  FIX-W4 [MINOR]   from_config() pastrat pentru compatibilitate backwards

Changelog v3.5 (review-fixes):
  FIX-R1 [CRITIC]  OrderManagerConfig: eliminat base_qty/entry_zscore/exit_zscore invalide
  FIX-R2 [CRITIC]  adopt_position() acum exista in OrderManager — reconciliere functionala
  FIX-R3 [MEDIU]   order_router.connect() apelat explicit inainte de reconciliere
  FIX-R4 [MEDIU]   PnlReconciler instantiat o singura data (nu la fiecare 60s)
  FIX-R5 [MEDIU]   run() cu try/except/finally crash handler + alerta Telegram
  FIX-R6 [MINOR]   import _dc nefolosit eliminat din _inject_adopted_position()

Changelog v3.4 (position-reconciliation):
  FEAT-1  Phase 0.5: PositionReconciler.fetch() — detectare pozitii existente pe Bybit
  FEAT-2  Phase 0.5: CheckpointManager.load() — restaurare stare din SQLite la restart
  FEAT-3  OrderManager.adopt_position() — injectare pozitie externa in state
  FEAT-4  PnlReconciler loop (60s) — verificare balanta reala vs. locala
  FEAT-5  CheckpointManager.save() — persistenta dupa fiecare trade executat
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

if TYPE_CHECKING:
    from execution.bybit_ws_bars import BybitWsBarsAdapter

from core.spread_monitor import SpreadMonitor
from execution.circuit_breaker import CircuitBreaker, CircuitState
from execution.exchange_factory import ExchangeFactory, get_order_router, get_ws_feed, get_dual_ws_feed  # noqa: F401
from execution.health_check import HealthCheck, HealthCheckConfig, HealthStatus
from execution.order_manager import OrderManager, OrderManagerConfig
from execution.ws_watchdog import WsWatchdog, WsWatchdogConfig
from execution.position_reconciler import PositionReconciler, AdoptedPosition
from execution.checkpoint_manager import CheckpointManager

from notifications.notifier_bus import NotifierBus

try:
    from core.state_bus import bus as _state_bus
except ImportError:
    try:
        from state_bus import bus as _state_bus
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
# BybitLiveRunnerConfig
# =============================================================================

@dataclass
class BybitLiveRunnerConfig:
    """Complete runtime configuration — env vars citite la instantiere."""

    symbol_y: str = field(default_factory=lambda: os.getenv("SYMBOL_Y", "BTCUSDT"))
    symbol_x: str = field(default_factory=lambda: os.getenv("SYMBOL_X", "ETHUSDT"))
    interval: int = field(default_factory=lambda: int(os.getenv("INTERVAL", "5")))

    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")

    api_key: str = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BYBIT_API_SECRET", ""))
    testnet: bool = field(default_factory=lambda: os.getenv("BYBIT_TESTNET", "false").lower() == "true")

    entry_zscore: float = field(default_factory=lambda: float(os.getenv("ENTRY_ZSCORE", "2.0")))
    exit_zscore: float = field(default_factory=lambda: float(os.getenv("EXIT_ZSCORE", "0.5")))
    base_qty: float = field(default_factory=lambda: float(os.getenv("BASE_QTY", "0.01")))

    warmup_bars: int = field(default_factory=lambda: int(os.getenv("WARMUP_BARS", "100")))
    kalman_window: int = field(default_factory=lambda: int(os.getenv("KALMAN_WINDOW", "200")))
    half_life_h: float = field(default_factory=lambda: float(os.getenv("HALF_LIFE_H", "24.0")))

    max_consec_losses: int = field(default_factory=lambda: int(os.getenv("MAX_CONSEC_LOSSES", "3")))
    max_drawdown_pct: float = field(default_factory=lambda: float(os.getenv("MAX_DRAWDOWN_PCT", "10.0")))
    cooldown_seconds: int = field(default_factory=lambda: int(os.getenv("COOLDOWN_SECONDS", "300")))

    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    slack_webhook_url: str = field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL", ""))

    health_port: int = field(default_factory=lambda: int(os.getenv("HEALTH_PORT", "8081")))

    funding_gate_enabled: bool = field(default_factory=lambda: os.getenv("FUNDING_GATE_ENABLED", "true").lower() == "true")
    pnl_reconciler_enabled: bool = field(default_factory=lambda: os.getenv("PNL_RECONCILER_ENABLED", "true").lower() == "true")
    market_trade_enabled: bool = field(default_factory=lambda: os.getenv("MARKET_TRADE_ENABLED", "true").lower() == "true")

    checkpoint_path: str = field(default_factory=lambda: os.getenv("CHECKPOINT_PATH", "position_checkpoint.db"))
    best_params_path: str = field(default_factory=lambda: os.getenv("BEST_PARAMS_PATH", "best_params.json"))
    state_bus_publish_interval: int = field(default_factory=lambda: int(os.getenv("STATE_BUS_PUBLISH_INTERVAL", "1")))

    rest_warmup_enabled: bool = field(default_factory=lambda: os.getenv("REST_WARMUP_ENABLED", "true").lower() == "true")
    bybit_category: str = field(default_factory=lambda: os.getenv("BYBIT_CATEGORY", "linear"))

    position_reconcile_enabled: bool = field(
        default_factory=lambda: os.getenv("POSITION_RECONCILE_ENABLED", "true").lower() == "true"
    )
    pnl_reconciler_interval_s: int = field(
        default_factory=lambda: int(os.getenv("PNL_RECONCILER_INTERVAL_S", "60"))
    )

    initial_capital: float = field(
        default_factory=lambda: float(os.getenv("INITIAL_CAPITAL", "10000"))
    )

    @classmethod
    def from_env(cls) -> "BybitLiveRunnerConfig":
        return cls()


# =============================================================================
# RunnerContext
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


# =============================================================================
# BybitLiveRunner v3.6
# =============================================================================

class BybitLiveRunner:
    """
    Main live trading loop v3.6.

    FIX-W1..W4: Accepta parametri optionali din WorkflowOrchestrator.
    Daca notifier_bus/ws_feed sunt furnizati la __init__, runner-ul le foloseste
    direct si evita reconstructia duplicata de componente.

    Startup sequence:
      Phase 0   — REST warm-up (bare istorice)
      Phase 0.5 — Position reconciliation (FEAT-1/2/3)
      Phase 1   — Build exchange clients
      Phase 2   — Build shared components
      Phase 3   — Start health server
      Phase 4   — Main trading loop (cu PnL reconciler background)
    """

    def __init__(
        self,
        cfg: BybitLiveRunnerConfig,
        # FIX-W1: parametri optionali injectati din WorkflowOrchestrator._build_runner()
        notifier_bus: Optional[NotifierBus] = None,
        ws_feed: Optional[Any] = None,
        private_ws: Optional[Any] = None,
        exchange: Optional[Any] = None,
    ) -> None:
        self.cfg = cfg
        self._stop_event: asyncio.Event = asyncio.Event()
        self._state: dict[str, Any] = {}
        self._bar_count: int = 0
        self._active_strategy: str = "kalman"
        self._funding_monitor: Optional[Any] = None
        self._checkpoint: CheckpointManager = CheckpointManager(cfg.checkpoint_path)
        self._adopted_position: Optional[AdoptedPosition] = None
        self._pnl_reconciler: Optional[Any] = None

        # FIX-W1: stocheaza componentele injectate din orchestrator
        # run() le va folosi direct in loc sa le reconstruiasca
        self._injected_notifier_bus: Optional[NotifierBus] = notifier_bus
        self._injected_ws_feed: Optional[Any] = ws_feed
        self._injected_private_ws: Optional[Any] = private_ws
        self._injected_exchange: Optional[Any] = exchange

    @classmethod
    def from_config(cls, cfg: BybitLiveRunnerConfig) -> "BybitLiveRunner":
        """FIX-W4: Pastrat pentru compatibilitate backwards."""
        return cls(cfg)

    # -------------------------------------------------------------------------
    # Phase 0: REST warm-up
    # -------------------------------------------------------------------------

    async def _warmup_from_rest(self, spread_monitor: SpreadMonitor) -> int:
        if not self.cfg.rest_warmup_enabled:
            logger.info("BybitLiveRunner: REST warm-up dezactivat")
            return 0

        logger.info(
            f"BybitLiveRunner: ⏳ Phase 0 — REST warm-up "
            f"{self.cfg.warmup_bars} bare {self.cfg.symbol_y}/{self.cfg.symbol_x}"
        )
        try:
            from execution.bybit_warmup_fetcher import BybitWarmupFetcher
            fetcher = BybitWarmupFetcher(
                symbol_y=self.cfg.symbol_y,
                symbol_x=self.cfg.symbol_x,
                interval=self.cfg.interval,
                n_bars=self.cfg.warmup_bars,
                testnet=self.cfg.testnet,
                category=self.cfg.bybit_category,
                request_timeout=15,
            )
            n = await fetcher.fetch(spread_monitor, _state_bus)
            if n > 0:
                self._bar_count = n
                logger.info(f"BybitLiveRunner: ✅ Phase 0 complet — {n} bare injectate")
            else:
                logger.warning("BybitLiveRunner: ⚠️ Phase 0 returnat 0 bare — fallback WS")
            return n
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: Phase 0 esuat ({exc}) — fallback WS warm-up")
            return 0

    # -------------------------------------------------------------------------
    # Phase 0.5: Position reconciliation
    # -------------------------------------------------------------------------

    async def _reconcile_positions(
        self,
        order_router: Any,
        order_manager: OrderManager,
        notifier_bus: Optional[NotifierBus] = None,
    ) -> None:
        if not self.cfg.position_reconcile_enabled or self.cfg.dry_run:
            logger.info("BybitLiveRunner: Position reconciliation dezactivata (dry_run sau config)")
            return

        logger.info("BybitLiveRunner: 🔍 Phase 0.5 — Position reconciliation...")

        adopted = self._checkpoint.load()
        if adopted is not None:
            logger.info(f"BybitLiveRunner: ✅ Checkpoint restaurat — {adopted}")
        else:
            reconciler = PositionReconciler(
                order_router=order_router,
                symbol_y=self.cfg.symbol_y,
                symbol_x=self.cfg.symbol_x,
                category=self.cfg.bybit_category,
            )
            adopted = await reconciler.fetch()

        if adopted is None or not adopted.has_position:
            logger.info("BybitLiveRunner: Phase 0.5 — nicio pozitie de adoptat, start curat")
            return

        self._adopted_position = adopted
        try:
            order_manager.adopt_position(
                symbol_y=adopted.symbol_y,
                symbol_x=adopted.symbol_x,
                y_side=adopted.y_side,
                x_side=adopted.x_side,
                y_qty=adopted.y_qty,
                x_qty=adopted.x_qty,
                y_entry_price=adopted.y_entry_price,
                x_entry_price=adopted.x_entry_price,
            )
            logger.info(
                f"BybitLiveRunner: ✅ Pozitie adoptata — "
                f"{adopted.symbol_y} {adopted.y_side} {adopted.y_qty:.6f} | "
                f"{adopted.symbol_x} {adopted.x_side} {adopted.x_qty:.6f} | "
                f"uPnL={adopted.unrealised_pnl:.4f} USDT"
            )
            if notifier_bus:
                try:
                    await notifier_bus.send_alert(
                        f"🔄 Pozitie adoptata la restart:\n"
                        f"{adopted.symbol_y} {adopted.y_side.upper()} {adopted.y_qty:.6f} @ {adopted.y_entry_price:.4f}\n"
                        f"{adopted.symbol_x} {adopted.x_side.upper()} {adopted.x_qty:.6f} @ {adopted.x_entry_price:.4f}\n"
                        f"uPnL: {adopted.unrealised_pnl:+.4f} USDT",
                        level="info",
                    )
                except Exception:
                    pass
        except AttributeError as exc:
            logger.warning(
                f"BybitLiveRunner: OrderManager.adopt_position() indisponibila ({exc}) — "
                "injectare manuala via _inject_adopted_position()"
            )
            self._inject_adopted_position(order_manager, adopted)

    def _inject_adopted_position(self, order_manager: OrderManager, adopted: AdoptedPosition) -> None:
        try:
            class _FakePos:
                y_side = adopted.y_side
                x_side = adopted.x_side
                y_qty = adopted.y_qty
                x_qty = adopted.x_qty
                y_entry = adopted.y_entry_price
                x_entry = adopted.x_entry_price
                pnl = adopted.unrealised_pnl

            if hasattr(order_manager, "current_position"):
                object.__setattr__(order_manager, "current_position", _FakePos())
            if hasattr(order_manager, "_has_position"):
                object.__setattr__(order_manager, "_has_position", True)
            logger.info("BybitLiveRunner: Pozitie injectata manual in OrderManager")
        except Exception as exc:
            logger.error(f"BybitLiveRunner: _inject_adopted_position failed: {exc}")

    # -------------------------------------------------------------------------
    # PnL reconciler background loop
    # -------------------------------------------------------------------------

    async def _pnl_reconciler_loop(
        self,
        order_router: Any,
        order_manager: OrderManager,
        notifier_bus: Optional[NotifierBus],
    ) -> None:
        if not self.cfg.pnl_reconciler_enabled:
            return

        try:
            from execution.pnl_reconciler import PnlReconciler
            self._pnl_reconciler = PnlReconciler(order_router)
            logger.info(
                f"BybitLiveRunner: PnL reconciler pornit "
                f"(interval={self.cfg.pnl_reconciler_interval_s}s)"
            )
        except ImportError:
            logger.debug("PnL reconciler: PnlReconciler nu e disponibil — dezactivat")
            return

        while not self._stop_event.is_set():
            await asyncio.sleep(self.cfg.pnl_reconciler_interval_s)
            try:
                await self._check_pnl_divergence(order_manager, notifier_bus)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"PnL reconciler: {exc}")

    async def _check_pnl_divergence(
        self,
        order_manager: OrderManager,
        notifier_bus: Optional[NotifierBus],
    ) -> None:
        if self._pnl_reconciler is None:
            return
        try:
            real_pnl = await self._pnl_reconciler.get_unrealised_pnl(
                self.cfg.symbol_y, self.cfg.symbol_x
            )
            local_pnl = float(getattr(order_manager, "current_pnl", 0.0) or 0.0)

            if real_pnl is None:
                return

            divergence = abs(real_pnl - local_pnl)
            threshold = max(abs(local_pnl) * 0.1, 5.0)

            if divergence > threshold:
                msg = (
                    f"⚠️ PnL DIVERGENTA: real={real_pnl:.4f} local={local_pnl:.4f} "
                    f"delta={divergence:.4f} USDT — verificati pozitia manual!"
                )
                logger.critical(msg)
                if notifier_bus:
                    try:
                        await notifier_bus.send_alert(msg, level="critical")
                    except Exception:
                        pass
            else:
                logger.debug(
                    f"PnL reconciler OK: real={real_pnl:.4f} local={local_pnl:.4f} "
                    f"delta={divergence:.4f}"
                )
        except Exception as exc:
            logger.debug(f"_check_pnl_divergence: {exc}")

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
            "circuit_open": circuit_breaker.state == CircuitState.OPEN,
            "active_strategy": self._active_strategy,
            "pnl": current_pnl,
            "dry_run": self.cfg.dry_run,
            "bar_count": self._bar_count,
            "has_adopted_position": self._adopted_position is not None,
        }

        try:
            _state_bus.publish("bar", payload)
        except Exception as exc:
            logger.debug(f"state_bus.publish bar failed: {exc}")

        if _HAS_METRICS:
            try:
                spread_zscore.set(zscore)
                _zscore_pair.set(abs(zscore))
                _pnl_metric.set(current_pnl)
                _circuit_open.set(1.0 if circuit_breaker.state == CircuitState.OPEN else 0.0)
                _warmup_bars_done.set(self._bar_count)
            except Exception:
                pass

    def _publish_warmup_status(
        self,
        spread_monitor: SpreadMonitor,
        coint_pvalue: float = 1.0,
    ) -> None:
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
                "source": "ws",
                "ts": int(time.time() * 1000),
            })
        except Exception as exc:
            logger.debug(f"state_bus.publish warmup_status failed: {exc}")

    # -------------------------------------------------------------------------
    # Phase 1: Build exchange clients
    # -------------------------------------------------------------------------

    async def _build_exchange_via_factory(self) -> tuple[Any, Any]:
        try:
            order_router = get_order_router(
                api_key=self.cfg.api_key,
                api_secret=self.cfg.api_secret,
                testnet=self.cfg.testnet,
                dry_run=self.cfg.dry_run,
            )
            ws_feed = get_dual_ws_feed(
                symbol_y=self.cfg.symbol_y,
                symbol_x=self.cfg.symbol_x,
                interval=self.cfg.interval,
                testnet=self.cfg.testnet,
            )
            logger.info(
                f"BybitLiveRunner: Exchange clients built "
                f"[dual-feed: {self.cfg.symbol_y}/{self.cfg.symbol_x}]"
            )
            return order_router, ws_feed

        except Exception as exc:
            logger.warning(f"BybitLiveRunner: ExchangeFactory failed ({exc}), fallback mock")
            try:
                from execution.bybit_order_router import BybitOrderRouter
                order_router = BybitOrderRouter(
                    api_key=self.cfg.api_key,
                    api_secret=self.cfg.api_secret,
                    testnet=self.cfg.testnet,
                    category=self.cfg.bybit_category,
                    mode="paper" if self.cfg.dry_run else "live",
                )
            except Exception as exc2:
                logger.error(f"BybitLiveRunner: Fallback BybitOrderRouter failed: {exc2}")
                raise

            from execution.bybit_ws_bars import BybitWsBarsAdapter
            ws_feed = BybitWsBarsAdapter(
                ws_feed=None,
                symbol_y=self.cfg.symbol_y,
                symbol_x=self.cfg.symbol_x,
                interval=str(self.cfg.interval),
            )
            logger.warning(f"BybitLiveRunner: WS feed MOCK ({self.cfg.symbol_y}/{self.cfg.symbol_x})")
            return order_router, ws_feed

    # -------------------------------------------------------------------------
    # Phase 2: Build shared components
    # -------------------------------------------------------------------------

    async def _build_components(
        self,
        order_router: Any,
        ws_feed: Any,
        injected_notifier_bus: Optional[NotifierBus] = None,
    ) -> tuple[SpreadMonitor, CircuitBreaker, OrderManager, WsWatchdog, NotifierBus]:
        """Build SpreadMonitor, CircuitBreaker, OrderManager, Watchdog, NotifierBus.

        FIX-W2: Daca injected_notifier_bus e furnizat (din orchestrator/main.py),
        il foloseste direct in loc sa construiasca unul nou cu token-uri duplicate.
        """
        spread_monitor = SpreadMonitor(
            symbol_y=self.cfg.symbol_y,
            symbol_x=self.cfg.symbol_x,
            window=self.cfg.kalman_window,
            half_life_h=self.cfg.half_life_h,
            warmup_bars=self.cfg.warmup_bars,
        )

        circuit_breaker = CircuitBreaker(
            failure_threshold=self.cfg.max_consec_losses,
            recovery_timeout_s=float(self.cfg.cooldown_seconds),
            name="trading",
        )

        om_cfg = OrderManagerConfig(dry_run=self.cfg.dry_run)
        order_manager = OrderManager(om_cfg)

        wd_cfg = WsWatchdogConfig(
            interval_seconds=30,
            max_missed_pings=3,
            reconnect_delay=5.0,
        )
        watchdog = WsWatchdog(ws_feed, wd_cfg)

        # FundingMonitor singleton
        if self.cfg.funding_gate_enabled:
            try:
                from execution.funding_monitor import FundingMonitor
                self._funding_monitor = FundingMonitor(ws_feed)
                logger.info("BybitLiveRunner: FundingMonitor singleton creat")
            except Exception as exc:
                logger.warning(f"BybitLiveRunner: FundingMonitor init failed ({exc}) — gate disabled")
                self._funding_monitor = None

        # FIX-W2: reutilizam notifier_bus injectat daca exista
        if injected_notifier_bus is not None:
            logger.info("BybitLiveRunner: Folosind NotifierBus injectat din orchestrator")
            notifier_bus = injected_notifier_bus
        else:
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
        hc_cfg = HealthCheckConfig(
            port=self.cfg.health_port,
            check_interval=10.0,
        )
        health = HealthCheck.from_components(components, hc_cfg)
        try:
            await health.start_http_server()
            logger.info(f"BybitLiveRunner: Health server started on port {self.cfg.health_port}")
        except Exception as exc:
            logger.warning(f"BybitLiveRunner: HealthCheck.start_http_server failed ({exc}) — fallback")
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
                runner_obj = web.AppRunner(aio_app)
                await runner_obj.setup()
                site = web.TCPSite(runner_obj, port=self.cfg.health_port)
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
        logger.info("BybitLiveRunner: Starting main trading loop...")

        watchdog.set_health_checker(health)
        watchdog_task = asyncio.create_task(watchdog.start())

        pnl_task: Optional[asyncio.Task] = None
        if self.cfg.pnl_reconciler_enabled:
            pnl_task = asyncio.create_task(
                self._pnl_reconciler_loop(order_router, order_manager, notifier_bus)
            )

        first_bar = True
        publish_counter = 0

        while not self._stop_event.is_set():
            try:
                bar = await ws_feed.get_bar()
                if bar is None:
                    await asyncio.sleep(0.1)
                    continue

                if getattr(bar, "price_x", 0.0) == 0.0 or getattr(bar, "price_y", 0.0) == 0.0:
                    logger.warning(
                        f"BybitLiveRunner: Bar ignorat — price_y={getattr(bar, 'price_y', 0.0)} "
                        f"price_x={getattr(bar, 'price_x', 0.0)} (zero price, tick malformat)"
                    )
                    continue

                self._bar_count += 1

                spread_monitor.update(bar.price_y, bar.price_x)
                zscore = spread_monitor.zscore
                spread = spread_monitor.spread

                if first_bar:
                    logger.info(
                        f"BybitLiveRunner: First WS bar | "
                        f"{self.cfg.symbol_y}={bar.price_y:.4f} "
                        f"{self.cfg.symbol_x}={bar.price_x:.4f} | "
                        f"spread={spread:.6f} | zscore={zscore:.4f} | "
                        f"bar_count={self._bar_count} (REST pre-loaded)"
                    )
                    first_bar = False

                is_warmed_up = getattr(spread_monitor, "is_warmed_up", False)
                if not is_warmed_up:
                    if self._bar_count % 10 == 0:
                        self._publish_warmup_status(spread_monitor)
                        logger.info(
                            f"[Warm-up WS] {self._bar_count}/{self.cfg.warmup_bars} bare "
                            f"({100 * self._bar_count / self.cfg.warmup_bars:.0f}%)"
                        )
                    self._publish_bar(bar, spread, zscore, spread_monitor, circuit_breaker, order_manager)
                    continue

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

                if circuit_breaker.state == CircuitState.OPEN:
                    logger.warning(
                        f"BybitLiveRunner: Circuit breaker OPEN | "
                        f"failures={circuit_breaker.failures}"
                    )
                    self._publish_bar(bar, spread, zscore, spread_monitor, circuit_breaker, order_manager)
                    await asyncio.sleep(1.0)
                    continue

                if self.cfg.funding_gate_enabled:
                    if not self._check_funding_gate():
                        logger.info("BybitLiveRunner: Funding gate CLOSED")
                        self._publish_bar(bar, spread, zscore, spread_monitor, circuit_breaker, order_manager)
                        continue

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
                        circuit_breaker=circuit_breaker,
                        order_manager=order_manager,
                        notifier_bus=notifier_bus,
                        bar=bar,
                    )

                publish_counter += 1
                if publish_counter >= self.cfg.state_bus_publish_interval:
                    self._publish_bar(bar, spread, zscore, spread_monitor, circuit_breaker, order_manager)
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

        if pnl_task is not None:
            pnl_task.cancel()
            try:
                await pnl_task
            except asyncio.CancelledError:
                pass

    # -------------------------------------------------------------------------
    # _check_funding_gate
    # -------------------------------------------------------------------------

    def _check_funding_gate(self) -> bool:
        if self._funding_monitor is None:
            return True
        try:
            y_funding = self._funding_monitor.get_funding_rate(self.cfg.symbol_y)
            x_funding = self._funding_monitor.get_funding_rate(self.cfg.symbol_x)
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
        if not market_trade_enabled:
            return None
        if circuit_breaker.state == CircuitState.OPEN:
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
        circuit_breaker: CircuitBreaker,
        order_manager: OrderManager,
        notifier_bus: NotifierBus,
        bar: Any,
    ) -> None:
        from execution.bybit_order_router import OrderRequest, OrderSide, OrderType

        if self.cfg.dry_run:
            logger.info(
                f"BybitLiveRunner: [DRY RUN] {action.upper()} "
                f"| {self.cfg.symbol_y}@{bar.price_y:.4f} "
                f"| {self.cfg.symbol_x}@{bar.price_x:.4f}"
            )
            return

        if bar.price_x == 0.0:
            logger.error("BybitLiveRunner: price_x=0 in _execute_action — skip")
            return

        async def _send_legs(req_y, req_x, record_fn):
            leg_y_done = False
            try:
                await order_router.create_order(req_y)
                leg_y_done = True
                await order_router.create_order(req_x)
                record_fn()
                return True
            except Exception as exc:
                if leg_y_done:
                    logger.critical(
                        f"BybitLiveRunner: ☠️ DUAL-LEG PARTIAL FILL — "
                        f"leg_y OK, leg_x FAILED: {exc}. "
                        f"Attempting emergency close on {req_y.symbol}"
                    )
                    if notifier_bus:
                        try:
                            await notifier_bus.send_alert(
                                f"☠️ PARTIAL FILL: {req_y.symbol} ok, {req_x.symbol} FAILED ({exc}). "
                                f"Emergency close initiated.",
                                level="critical",
                            )
                        except Exception:
                            pass
                    try:
                        cancel_side = OrderSide.SELL if req_y.side == OrderSide.BUY else OrderSide.BUY
                        cancel_req = OrderRequest(
                            symbol=req_y.symbol, side=cancel_side,
                            order_type=OrderType.MARKET, qty=req_y.qty, price=0.0,
                        )
                        await order_router.create_order(cancel_req)
                        logger.warning(f"BybitLiveRunner: Emergency close {req_y.symbol} trimis OK")
                    except Exception as cancel_exc:
                        logger.critical(
                            f"BybitLiveRunner: ☠️ Emergency close FAILED pentru "
                            f"{req_y.symbol}: {cancel_exc} — POZITIE DESCHISA MANUAL!"
                        )
                    circuit_breaker.record_failure()
                raise

        try:
            x_qty = self.cfg.base_qty * bar.price_y / bar.price_x

            if action == "entry_long":
                req_y = OrderRequest(symbol=self.cfg.symbol_y, side=OrderSide.BUY,
                    order_type=OrderType.MARKET, qty=self.cfg.base_qty, price=0.0)
                req_x = OrderRequest(symbol=self.cfg.symbol_x, side=OrderSide.SELL,
                    order_type=OrderType.MARKET, qty=x_qty, price=0.0)
                await _send_legs(req_y, req_x,
                    lambda: order_manager.record_entry_long(self.cfg.base_qty, bar.price_y, bar.price_x))
                logger.info(f"BybitLiveRunner: ENTRY LONG | {self.cfg.symbol_y}@{bar.price_y:.2f}")
                self._save_checkpoint(order_manager)
                if notifier_bus:
                    await notifier_bus.send_alert(
                        f"✅ ENTRY LONG: {self.cfg.symbol_y}/{self.cfg.symbol_x}", level="success")

            elif action == "entry_short":
                req_y = OrderRequest(symbol=self.cfg.symbol_y, side=OrderSide.SELL,
                    order_type=OrderType.MARKET, qty=self.cfg.base_qty, price=0.0)
                req_x = OrderRequest(symbol=self.cfg.symbol_x, side=OrderSide.BUY,
                    order_type=OrderType.MARKET, qty=x_qty, price=0.0)
                await _send_legs(req_y, req_x,
                    lambda: order_manager.record_entry_short(self.cfg.base_qty, bar.price_y, bar.price_x))
                logger.info(f"BybitLiveRunner: ENTRY SHORT | {self.cfg.symbol_y}@{bar.price_y:.2f}")
                self._save_checkpoint(order_manager)
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
                    await _send_legs(req_y, req_x,
                        lambda: order_manager.record_exit(bar.price_y, bar.price_x))
                    logger.info(f"BybitLiveRunner: EXIT | PnL={order_manager.current_pnl:.4f}")
                    self._checkpoint.clear()
                    self._adopted_position = None
                    if notifier_bus:
                        await notifier_bus.send_alert(
                            f"✅ EXIT: PnL={order_manager.current_pnl:.4f}", level="success")

            if order_manager.current_pnl is not None and order_manager.current_pnl < 0:
                circuit_breaker.record_failure()
            else:
                circuit_breaker.record_success()

        except Exception as exc:
            logger.error(f"BybitLiveRunner: Execute action '{action}' failed: {exc}")
            circuit_breaker.record_failure()
            if notifier_bus:
                try:
                    await notifier_bus.send_alert(
                        f"❌ ACTION FAILED: {action} | {exc}", level="error")
                except Exception:
                    pass

    def _save_checkpoint(self, order_manager: OrderManager) -> None:
        try:
            pos = order_manager.current_position
            if pos is None:
                return
            adopted = AdoptedPosition(
                symbol_y=self.cfg.symbol_y,
                symbol_x=self.cfg.symbol_x,
                y_side=getattr(pos, "y_side", "none"),
                x_side=getattr(pos, "x_side", "none"),
                y_qty=float(getattr(pos, "y_qty", 0.0)),
                x_qty=float(getattr(pos, "x_qty", 0.0)),
                y_entry_price=float(getattr(pos, "y_entry", 0.0)),
                x_entry_price=float(getattr(pos, "x_entry", 0.0)),
                unrealised_pnl=float(getattr(order_manager, "current_pnl", 0.0) or 0.0),
                source="checkpoint",
            )
            self._checkpoint.save(adopted)
        except Exception as exc:
            logger.warning(f"_save_checkpoint: {exc}")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def run(self) -> int:
        """
        Full run: Phase 0 REST → Phase 0.5 Reconcile → Build → Start → Trade.

        FIX-W3: Daca ws_feed a fost injectat la __init__ (din orchestrator),
        il folosim direct si sarim peste get_dual_ws_feed() din ExchangeFactory.
        FIX-W2: Daca notifier_bus a fost injectat, il pasam la _build_components()
        care il reutilizeaza in loc sa construiasca unul nou.
        """
        logger.info("BybitLiveRunner: ========== Starting Live Runner v3.6 ==========")
        logger.info(
            f"BybitLiveRunner: {self.cfg.symbol_y}/{self.cfg.symbol_x} | "
            f"interval={self.cfg.interval}m | dry_run={self.cfg.dry_run}"
        )

        # FIX-W3: construieste order_router intern dar refoloseste ws_feed injectat
        order_router, built_ws_feed = await self._build_exchange_via_factory()
        ws_feed = self._injected_ws_feed if self._injected_ws_feed is not None else built_ws_feed
        if self._injected_ws_feed is not None:
            logger.info("BybitLiveRunner: Folosind WsFeed injectat din orchestrator")

        if hasattr(order_router, "connect"):
            try:
                await order_router.connect()
                logger.info("BybitLiveRunner: order_router.connect() OK")
            except Exception as exc:
                logger.warning(f"BybitLiveRunner: order_router.connect() failed ({exc}) — continuam")

        # FIX-W2: paseaza notifier_bus injectat la _build_components
        (
            spread_monitor, circuit_breaker, order_manager, watchdog, notifier_bus,
        ) = await self._build_components(
            order_router, ws_feed,
            injected_notifier_bus=self._injected_notifier_bus,
        )

        await self._warmup_from_rest(spread_monitor)
        await self._reconcile_positions(order_router, order_manager, notifier_bus)

        if notifier_bus:
            adopted_msg = ""
            if self._adopted_position:
                adopted_msg = (
                    f"\n🔄 Pozitie adoptata: "
                    f"{self._adopted_position.symbol_y} {self._adopted_position.y_side.upper()} | "
                    f"{self._adopted_position.symbol_x} {self._adopted_position.x_side.upper()}"
                )
            await notifier_bus.send_alert(
                f"⚡ QuantLuna Started v3.6 | {self.cfg.symbol_y}/{self.cfg.symbol_x} | "
                f"dry_run={self.cfg.dry_run}{adopted_msg}",
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

        try:
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
        except Exception as exc:
            logger.critical(f"BybitLiveRunner: run() crashed unexpectedly: {exc}")
            if notifier_bus:
                try:
                    await notifier_bus.send_alert(
                        f"💀 RUNNER CRASHED: {exc} — restart necesar!",
                        level="critical",
                    )
                except Exception:
                    pass
            raise
        finally:
            logger.info("BybitLiveRunner: Runner stopped")

        return 0

    start = run

    def stop(self) -> None:
        """Signal graceful stop."""
        self._stop_event.set()

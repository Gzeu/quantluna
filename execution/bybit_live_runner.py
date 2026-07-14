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
import os
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

        # S48: Kalman, signal generator, profit guard (lazy init in start())
        self._kalman: Optional[object] = None
        self._signal_generator: Optional[object] = None
        self._profit_guard: Optional[object] = None
        self._spread_engine: Optional[object] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> int:
        """Build, wire and run.  Blocks until stop() is called."""
        logger.info("BybitLiveRunner: === START === {}/{} dry={} ml={}",
                    self.cfg.symbol_y, self.cfg.symbol_x, self.cfg.dry_run,
                    self.cfg.ml_enabled)

        order_router, ws_feed = await self._build_exchange()
        (
            spread_monitor, circuit_breaker,
            order_manager, watchdog, notifier_bus,
        ) = await self._build_components(order_router, ws_feed)

        # \u2500\u2500 ML pipeline (S47) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        ml_engine, signal_fusion = self._build_ml_pipeline()

        # \u2500\u2500 S48: Kalman filter + SignalGenerator v4 \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
        self._build_signal_pipeline(notifier_bus)

        if notifier_bus:
            await notifier_bus.send_alert(
                f"\u26a1 QuantLuna Start | {self.cfg.symbol_y}/{self.cfg.symbol_x} "
                f"| dry={self.cfg.dry_run} | ml={self.cfg.ml_enabled}",
                level="info",
            )

        health = await self._start_health_server({
            "spread_monitor":  spread_monitor,
            "circuit_breaker": circuit_breaker,
            "order_manager":   order_manager,
            "ws_feed":         ws_feed,
            "watchdog":        watchdog,
            "ml_engine":       ml_engine,
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
            ml_engine, signal_fusion,
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
            api_key=self.cfg.api_key,
            api_secret=self.cfg.api_secret,
            health_port=self.cfg.health_port,
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
    # ML pipeline (S47)
    # ------------------------------------------------------------------

    def _build_ml_pipeline(self):
        """Build ML inference engine + signal fusion (or None if disabled)."""
        if not self.cfg.ml_enabled:
            logger.info("BybitLiveRunner: ML pipeline disabled")
            return None, None

        try:
            from strategy.ml.config import MLConfig
            from strategy.ml.features import FeatureStore
            from strategy.ml.models import (
                MLInferenceEngine, ModelRegistry,
                NumpyLinearRegression, NumpyLogisticRegression,
            )
            from strategy.ml.signal_fusion import SignalFusion

            ml_cfg = MLConfig.from_env()
            ml_cfg.enabled = True

            fs = FeatureStore(maxlen=ml_cfg.feature_lookback)
            reg = ModelRegistry(ml_cfg)
            # Register default models (30 features each)
            reg.register_direction(
                "lr_default",
                NumpyLogisticRegression(
                    n_features=30,
                    lr=ml_cfg.lr_learning_rate,
                    l2_reg=ml_cfg.lr_l2_reg,
                ),
            )
            reg.register_confidence(
                "lin_default",
                NumpyLinearRegression(
                    n_features=30,
                    lr=ml_cfg.linear_learning_rate,
                    l2_reg=ml_cfg.linear_l2_reg,
                ),
            )

            # Try to load saved checkpoints
            try:
                loaded = reg.load(ml_cfg.model_checkpoint_dir)
                if loaded > 0:
                    logger.info("BybitLiveRunner: loaded {} ML model(s) from {}",
                                loaded, ml_cfg.model_checkpoint_dir)
            except Exception:
                pass

            engine = MLInferenceEngine(ml_cfg, reg, fs)
            fusion = SignalFusion(ml_cfg)

            logger.info(
                "BybitLiveRunner: ML pipeline ready ({} features, warmup={} bars, "
                "fusion: trending={:.0%} ranging={:.0%})",
                fs.N_FEATURES, ml_cfg.model_warmup_bars,
                ml_cfg.trending_ml_weight, ml_cfg.ranging_ml_weight,
            )
            return engine, fusion

        except ImportError as exc:
            logger.warning("BybitLiveRunner: ML imports failed — disabling: {}", exc)
            return None, None
        except Exception as exc:
            logger.error("BybitLiveRunner: ML pipeline build failed: {}", exc)
            return None, None

    # ------------------------------------------------------------------
    # Signal pipeline (S48 — Kalman + SignalGenerator v4 + ProfitGuard)
    # ------------------------------------------------------------------

    def _build_signal_pipeline(self, notifier_bus=None) -> None:
        """Build Kalman filter, SpreadEngine, SignalGenerator v4, and ProfitGuard."""
        kalman_enabled = getattr(
            self.cfg, "kalman_enabled",
            os.environ.get("KALMAN_ENABLED", "true").lower() == "true",
        )

        if not kalman_enabled:
            logger.info("BybitLiveRunner: Kalman/SignalGenerator pipeline disabled")
            return

        try:
            # 1. Kalman filter
            from core.kalman_filter import KalmanHedgeRatio
            delta = getattr(self.cfg, "delta", float(
                os.environ.get("KALMAN_DELTA", "1e-4")
            ))
            self._kalman = KalmanHedgeRatio(delta=delta)
            logger.info("BybitLiveRunner: KalmanHedgeRatio ready (delta={})", delta)

            # 2. SpreadEngine
            from core.spread import SpreadEngine
            zscore_window = getattr(self.cfg, "kalman_window", 200)
            bar_freq = getattr(self.cfg, "interval", 5)
            bar_freq_hours = max(bar_freq / 60.0, 0.016)  # minutes → hours
            self._spread_engine = SpreadEngine(
                kalman=self._kalman,
                zscore_window=zscore_window,
                bar_freq_hours=bar_freq_hours,
            )

            # 3. SignalGenerator v4
            from config.settings import SignalConfig
            from strategy.signal import SignalGenerator
            sig_cfg = SignalConfig()
            self._signal_generator = SignalGenerator(
                spread_engine=self._spread_engine,
                config=sig_cfg,
                cooldown_bars=getattr(self.cfg, "cooldown_seconds", 300) // max(
                    self.cfg.interval * 60, 60
                ),
            )
            logger.info(
                "BybitLiveRunner: SignalGenerator v4 ready "
                "(entry_z={:.1f}, exit_z={:.1f}, stop_z={:.1f})",
                sig_cfg.zscore_entry, sig_cfg.zscore_exit, sig_cfg.zscore_stop,
            )

            # 4. ProfitGuard (S48)
            pg_enabled = getattr(
                self.cfg, "profit_guard_enabled",
                os.environ.get("PROFIT_GUARD_ENABLED", "true").lower() == "true",
            )
            if pg_enabled:
                try:
                    from execution.profit_guard import ProfitGuard, ProfitGuardConfig
                    pg_cfg = ProfitGuardConfig()
                    self._profit_guard = ProfitGuard(pg_cfg, notifier_bus=notifier_bus)
                    logger.info(
                        "BybitLiveRunner: ProfitGuard ready "
                        "(tp_improvement={:.1f}z, ladder={}, trailing={})",
                        pg_cfg.tp_zscore_improvement,
                        pg_cfg.ladder_enabled,
                        pg_cfg.trailing_enabled,
                    )
                except ImportError:
                    logger.warning("BybitLiveRunner: ProfitGuard not available")
                    self._profit_guard = None

        except ImportError as exc:
            logger.warning("BybitLiveRunner: Signal pipeline imports failed: {}", exc)
        except Exception as exc:
            logger.warning("BybitLiveRunner: Signal pipeline build failed: {}", exc)

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
        ml_engine=None,
        signal_fusion=None,
    ) -> None:
        watchdog_task = asyncio.create_task(watchdog.run())
        first_bar = True
        bar_count = 0

        while not self._stop_event.is_set():
            try:
                bar = await ws_feed.get_bar()
                if bar is None:
                    await asyncio.sleep(0.1)
                    continue

                bar_count += 1

                # ── S48: Compute REAL z-score via Kalman filter ────────
                spread = bar.price_y / bar.price_x if bar.price_x > 0 else 1.0

                if self._kalman is not None and self._spread_engine is not None:
                    try:
                        # Incremental Kalman update
                        ss = self._spread_engine.update_one(
                            y=bar.price_y, x=bar.price_x,
                            ts=getattr(bar, "timestamp", bar_count),
                        )
                        zscore = float(ss.get("zscore", 0.0))
                        half_life = float(ss.get("half_life_hours", 24.0))
                        beta = float(ss.get("beta", 1.0))
                        uncertainty = float(ss.get("uncertainty", 0.0))
                        kalman_warm = bool(ss.get("is_warm", False))
                    except Exception as exc:
                        logger.debug("Kalman update failed: {} — using fallback", exc)
                        zscore = 0.0
                        half_life = 24.0
                        beta = 1.0
                        uncertainty = 0.0
                        kalman_warm = False
                else:
                    zscore = 0.0
                    half_life = 24.0
                    beta = 1.0
                    uncertainty = 0.0
                    kalman_warm = False

                report = spread_monitor.update(spread, zscore, half_life)
                zscore = getattr(report, "zscore", zscore)

                if first_bar:
                    logger.info(
                        "BybitLiveRunner: first bar | spread={:.6f} z={:.4f} "
                        "hl={:.1f}h kalman_warm={}",
                        spread, zscore, half_life, kalman_warm,
                    )
                    first_bar = False

                if not circuit_breaker.is_available():
                    logger.warning("BybitLiveRunner: circuit OPEN — blocking trades")
                    await asyncio.sleep(1.0)
                    continue

                if self.cfg.funding_gate_enabled and not funding_gate.is_open(ws_feed):
                    logger.info("BybitLiveRunner: funding gate CLOSED")
                    continue

                # ── S48: SignalGenerator v4 check ──────────────────────
                v4_action = None
                if self._signal_generator is not None and kalman_warm:
                    try:
                        trade_signal = self._signal_generator.generate_live(
                            y=bar.price_y, x=bar.price_x,
                            ts=getattr(bar, "timestamp", None),
                            funding_annual=0.0,
                            regime_multiplier=1.0,
                            coint_valid=True,
                        )
                        if trade_signal.signal is not None:
                            sig_val = int(trade_signal.signal)
                            if sig_val == 2:  # PARTIAL_EXIT
                                v4_action = "partial_exit"
                                logger.info(
                                    "SignalGen v4: PARTIAL_EXIT z={:.3f} "
                                    "close={:.0%}",
                                    zscore, getattr(trade_signal, "partial_close_pct", 0.5),
                                )
                            elif sig_val == 0:  # EXIT (v4 reasons: hard_stop, time_stop, etc.)
                                v4_action = "exit"
                                reason = getattr(trade_signal, "reason", "v4_exit")
                                logger.info(
                                    "SignalGen v4: EXIT z={:.3f} reason={}",
                                    zscore, reason,
                                )
                    except Exception as exc:
                        logger.debug("SignalGen v4 error: {}", exc)

                # ── ML inference step (S47) ───────────────────────────
                if ml_engine is not None:
                    try:
                        bar_dict = {
                            "price_y": bar.price_y,
                            "price_x": bar.price_x,
                            "volume": getattr(bar, "volume", 0.0),
                            "high": getattr(bar, "high", bar.price_y),
                            "low": getattr(bar, "low", bar.price_y),
                        }
                        spread_state = {
                            "spread": spread, "zscore": zscore, "beta": beta,
                            "uncertainty": uncertainty,
                            "half_life_hours": half_life,
                            "regime": getattr(report, "regime", "unknown"),
                            "vol_regime": getattr(report, "vol_regime", "NORMAL"),
                        }
                        ml_dir, ml_conf = ml_engine.update(bar_dict, spread_state)
                        if signal_fusion is not None and ml_engine.is_warm:
                            fused = signal_fusion.fuse(
                                ml_direction=ml_dir, ml_confidence=ml_conf,
                                zscore=zscore,
                                zscore_threshold=self.cfg.entry_zscore,
                                regime=getattr(report, "regime", "unknown"),
                            )
                            if fused.should_trade and abs(ml_dir) > 0.2:
                                effective_zscore = zscore * (1.0 - fused.ml_contribution) + ml_dir * 3.5 * fused.ml_contribution
                            else:
                                effective_zscore = zscore
                        else:
                            effective_zscore = zscore
                    except Exception as exc:
                        logger.debug("BybitLiveRunner: ML step error: {}", exc)
                        effective_zscore = zscore
                else:
                    effective_zscore = zscore

                # ── S48: ProfitGuard check ─────────────────────────────
                if self._profit_guard is not None and order_manager.has_position():
                    try:
                        guard_action = self._profit_guard.update(
                            pair=f"{self.cfg.symbol_y}/{self.cfg.symbol_x}",
                            zscore=zscore,
                            spread=spread,
                            prices=(bar.price_y, bar.price_x),
                            order_manager=order_manager,
                        )
                        if guard_action.action == "FULL_CLOSE":
                            v4_action = "exit"
                            logger.info(
                                "ProfitGuard: {} | z={:.3f}",
                                guard_action.reason, zscore,
                            )
                        elif guard_action.action == "PARTIAL_CLOSE":
                            v4_action = "partial_exit"
                            logger.info(
                                "ProfitGuard: PARTIAL_CLOSE {:.0%} | {}",
                                guard_action.close_ratio, guard_action.reason,
                            )
                    except Exception as exc:
                        logger.debug("ProfitGuard error: {}", exc)

                # ── Decision & execution ───────────────────────────────
                # SignalGenerator v4 / ProfitGuard override takes precedence
                if v4_action == "exit":
                    action = "exit"
                elif v4_action == "partial_exit":
                    # Use partial exit handler if available
                    try:
                        from execution.partial_exit_handler import handle_partial_exit
                        await handle_partial_exit(
                            order_manager=order_manager,
                            order_router=order_router,
                            partial_close_pct=0.5,
                        )
                    except ImportError:
                        action = "exit"  # fallback to full exit
                    except Exception as exc:
                        logger.warning("Partial exit failed: {} — falling back", exc)
                        action = "exit"
                    else:
                        action = None  # partial exit handled externally
                else:
                    action = decision_engine.decide(
                        effective_zscore, circuit_breaker, order_manager,
                    )

                if action:
                    await executor.execute(
                        action, order_router, order_manager, notifier_bus, bar,
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

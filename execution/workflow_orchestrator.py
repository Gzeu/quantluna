"""
execution/workflow_orchestrator.py  -  QuantLuna Startup Workflow Orchestrator v2

Sprint 28 rev-4 (fix: loguru, docstrings, wiring):
  Orchestratorul complet care leaga TOATE modulele existente:

  FAZA 0: Pre-flight HealthCheck      <- health_check.HealthCheck
  FAZA 1: Scan pozitii exchange        <- position_scanner.PositionScanner
  FAZA 2: Reconciliere checkpoint      <- resume_manager.ResumeManager
  FAZA 3: Adoptie pozitii orfane       <- adoption_engine.AdoptionEngine
  FAZA 4: Initializare ProfitOptimizer <- profit_optimizer.ProfitOptimizer
  FAZA 5: Pornire BybitLiveRunner      <- bybit_live_runner.BybitLiveRunner

  Subsisteme integrate:
  - ExchangeFactory  : instanta CCXT shared pentru toate fazele
  - WsWatchdog       : injectat in runner
  - EmergencyStop    : apelat la halt (FAZA 0 sau FAZA 2)
  - NotifierBus      : inlocuieste live_trader._send_alert
  - StateBus         : bot publica RiskDashboardEngine -> API il citeste

Usage (din main.py)::

    orch = WorkflowOrchestrator.from_runner_cfg(cfg, notifier_bus)
    ctx  = await orch.run_startup_workflow()
    if ctx.should_halt:
        sys.exit(1)
    await orch.start_runner(ctx)  # blocks until stop
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from loguru import logger


@dataclass
class StartupContext:
    """Carries state produced by each startup phase."""

    scan_report = None
    reconcile_result = None
    adoption_results: list = field(default_factory=list)
    optimizer = None
    health_report = None

    should_halt: bool = False
    halt_reason: str = ""

    @property
    def has_adopted_positions(self) -> bool:
        try:
            from execution.adoption_engine import AdoptionDecision
            return any(
                r.decision in (AdoptionDecision.ADOPT, AdoptionDecision.MONITOR_ONLY)
                for r in self.adoption_results
            )
        except Exception:
            return bool(self.adoption_results)

    @property
    def adopted_count(self) -> int:
        try:
            from execution.adoption_engine import AdoptionDecision
            return sum(
                1 for r in self.adoption_results
                if r.decision in (AdoptionDecision.ADOPT, AdoptionDecision.MONITOR_ONLY)
            )
        except Exception:
            return len(self.adoption_results)

    @property
    def closed_count(self) -> int:
        try:
            from execution.adoption_engine import AdoptionDecision
            return sum(
                1 for r in self.adoption_results
                if r.decision.name == "CLOSE_NOW"
            )
        except Exception:
            return 0


class WorkflowOrchestrator:
    """
    Orchestrator complet Sprint 28.

    Preferred constructor: ``WorkflowOrchestrator.from_runner_cfg(cfg, bus)``
    """

    def __init__(self, exchange=None, checkpoint_path="state/position_checkpoint.db",
                 notifier_bus=None, adoption_config=None, min_notional=1.0,
                 runner=None, runner_cfg=None, private_ws=None, ws_feed=None,
                 skip_health_check=False, position_store=None) -> None:
        self._exchange = exchange
        self._checkpoint_path = checkpoint_path
        self._bus = notifier_bus
        self._adoption_cfg = adoption_config
        self._min_notional = min_notional
        self._runner = runner
        self._runner_cfg = runner_cfg
        self._private_ws = private_ws
        self._ws_feed = ws_feed
        self._skip_health = skip_health_check
        self._position_store = position_store

    @classmethod
    def from_runner_cfg(cls, cfg, notifier_bus=None, ws_feed=None,
                        private_ws=None, skip_health_check=False,
                        position_store=None):
        return cls(
            exchange=None, checkpoint_path=cfg.checkpoint_path,
            notifier_bus=notifier_bus, runner_cfg=cfg, ws_feed=ws_feed,
            private_ws=private_ws, skip_health_check=skip_health_check,
            position_store=position_store,
        )

    @classmethod
    def from_env(cls, dispatcher=None):
        """
        Construiește orchestratorul din variabile de mediu — folosit de API server.
        """
        # Build a minimal runner config from env
        try:
            from execution.bybit_live_runner import BybitLiveRunnerConfig
            cfg = BybitLiveRunnerConfig.from_env()
        except Exception:
            # Fallback: build config manually from env vars
            from execution.bybit_live_runner import BybitLiveRunnerConfig
            cfg = BybitLiveRunnerConfig()
            cfg.symbol_y        = os.getenv("SYMBOL_Y", "BTCUSDT")
            cfg.symbol_x        = os.getenv("SYMBOL_X", "ETHUSDT")
            cfg.interval        = os.getenv("INTERVAL", "5")
            cfg.dry_run         = os.getenv("DRY_RUN", "false").lower() != "true"
            cfg.checkpoint_path = os.getenv("CHECKPOINT_PATH", "state/position_checkpoint.db")

        orch = cls(
            exchange=None,
            checkpoint_path=getattr(cfg, "checkpoint_path", "state/position_checkpoint.db"),
            notifier_bus=dispatcher,
            runner_cfg=cfg,
            skip_health_check=True,  # API server skips health check on its own startup
        )
        # Store additional state the API router needs
        orch.pairs        = [f"{cfg.symbol_y}/{cfg.symbol_x}"]
        orch.reoptimizer  = None
        orch.watchdog     = None
        orch.context       = None
        return orch

    async def build_context(self) -> None:
        """Construiește contextul runtime — inițializează watchdog, reoptimizer, etc."""
        from dataclasses import dataclass

        @dataclass
        class _ApiContext:
            sizing_engine: object = None
            decision_engine: object = None
            should_halt: bool = False
            halt_reason: str = ""

        self.context = _ApiContext()

        # Try to build the exchange connection
        if self._exchange is None:
            self._exchange = await self._build_shared_exchange()

        # Try to wire up the watchdog
        try:
            from core.monitoring_watchdog import MonitoringWatchdog
            self.watchdog = MonitoringWatchdog.from_env(
                pairs=self.pairs,
                metrics_provider=None,
                dispatcher=self._bus,
            )
            logger.info("[Orchestrator] MonitoringWatchdog initializat din env")
        except Exception as exc:
            logger.warning("[Orchestrator] MonitoringWatchdog init failed: {}", exc)
            self.watchdog = None

        # Try to wire up the reoptimizer
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
            self.reoptimizer = AutoReoptimizer.from_env()
            logger.info("[Orchestrator] AutoReoptimizer initializat din env")
        except Exception as exc:
            logger.warning("[Orchestrator] AutoReoptimizer init failed: {}", exc)
            self.reoptimizer = None

        # Try to build sizing engine
        try:
            from risk.bybit_position_sizer import BybitPositionSizer
            from risk.sizing_engine import SizingEngine
            sizer = BybitPositionSizer(
                capital_usdt=float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
                max_leverage=float(os.getenv("MAX_LEVERAGE", "3.0")),
                kelly_fraction=os.getenv("KELLY_FRACTION", "half"),
                max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.25")),
            )
            self.context.sizing_engine = SizingEngine(sizer=sizer)
            logger.info("[Orchestrator] SizingEngine initializat")
        except Exception as exc:
            logger.warning("[Orchestrator] SizingEngine init failed: {}", exc)

        logger.info("[Orchestrator] build_context complete")

    async def stop_runner(self) -> None:
        """Oprește runner-ul și curăță resursele."""
        runner = self._runner
        if runner is not None and hasattr(runner, "stop"):
            try:
                await runner.stop()
                logger.info("[Orchestrator] Runner stopped")
            except Exception as exc:
                logger.warning("[Orchestrator] Runner stop error: {}", exc)

        if self.watchdog is not None and hasattr(self.watchdog, "stop"):
            try:
                await self.watchdog.stop()
                logger.info("[Orchestrator] Watchdog stopped")
            except Exception as exc:
                logger.warning("[Orchestrator] Watchdog stop error: {}", exc)

    async def run_startup_workflow(self) -> StartupContext:
        ctx = StartupContext()
        sep = "=" * 60
        logger.info(sep)
        logger.info("[Orchestrator] QuantLuna startup workflow START")

        if not self._skip_health:
            logger.info("[Orchestrator] FAZA 0: HealthCheck pre-flight")
            ctx.health_report = await self._run_health_check()
            if ctx.health_report is not None and not ctx.health_report.all_passed:
                failures = [c.name for c in ctx.health_report.critical_failures]
                ctx.should_halt = True
                ctx.halt_reason = f"HealthCheck critical failures: {failures}"
                logger.error("[Orchestrator] HALT: {}", ctx.halt_reason)
                await self._alert(f"Startup HALT (HealthCheck):\n{ctx.halt_reason}")
                await self._emergency_stop(ctx.halt_reason)
                return ctx
            logger.info("[Orchestrator] FAZA 0: HealthCheck OK")
        else:
            logger.info("[Orchestrator] FAZA 0: HealthCheck SKIPPED")

        if self._exchange is None:
            self._exchange = await self._build_shared_exchange()

        logger.info("[Orchestrator] FAZA 1: Scan pozitii exchange")
        try:
            from execution.position_scanner import PositionScanner
            from execution.checkpoint import PositionCheckpoint
            cp = PositionCheckpoint(self._checkpoint_path)
            scanner = PositionScanner(self._exchange, cp, self._min_notional)
            ctx.scan_report = await scanner.scan()
            logger.info("[Orchestrator] {}", ctx.scan_report.summary())

            # Salvează pozițiile scanate în PositionStore pentru persistență
            # (folosit de BybitOrderRouter în paper mode la get_open_positions)
            if self._position_store is not None:
                raw_positions = await self._exchange.fetch_positions() if hasattr(self._exchange, 'fetch_positions') else []
                self._position_store.save_bybit_positions(raw_positions)
                logger.info("Orchestrator] Salvat {} poziții în PositionStore", len(raw_positions))
        except Exception as exc:
            logger.error("[Orchestrator] Scan failed: {} — skip", exc)

        logger.info("[Orchestrator] FAZA 2: Reconciliere checkpoint")
        try:
            from execution.resume_manager import ResumeManager
            from execution.checkpoint import PositionCheckpoint
            cp = PositionCheckpoint(self._checkpoint_path)
            resume = ResumeManager(cp, self._exchange, self._bus)
            ctx.reconcile_result = await resume.reconcile_on_startup()
            if getattr(ctx.reconcile_result, "should_halt", False):
                ctx.should_halt = True
                ctx.halt_reason = getattr(ctx.reconcile_result, "message", "reconcile halt")
                logger.error("[Orchestrator] HALT (ResumeManager): {}", ctx.halt_reason)
                await self._alert(f"Startup HALT (reconciliere):\n{ctx.halt_reason}")
                await self._emergency_stop(ctx.halt_reason)
                return ctx
        except Exception as exc:
            logger.error("[Orchestrator] Reconciliere failed: {} — continuam", exc)

        has_orphans = (ctx.scan_report is not None and getattr(ctx.scan_report, "has_orphans", False))
        if has_orphans:
            orphan_count = len(ctx.scan_report.orphans)
            logger.info("[Orchestrator] FAZA 3: Adoptie {} pozitii orfane", orphan_count)
            await self._alert(f"{orphan_count} pozitii orfane detectate — procesare...")
            try:
                from execution.adoption_engine import AdoptionEngine
                from execution.checkpoint import PositionCheckpoint
                from execution.position_scanner import ScanReport
                cp = PositionCheckpoint(self._checkpoint_path)
                adoption = AdoptionEngine(self._exchange, cp, order_manager=None, config=self._adoption_cfg)
                temp_report = ScanReport(orphans=ctx.scan_report.orphans)
                ctx.adoption_results = await adoption.process_report(temp_report)
                logger.info("[Orchestrator] Adoptie: {} adoptate, {} inchise", ctx.adopted_count, ctx.closed_count)

                # Persistă pozițiile adoptate în PositionStore
                if self._position_store is not None:
                    # Re-fetch current positions to get latest state
                    try:
                        raw = await self._exchange.fetch_positions()
                        self._position_store.save_bybit_positions(raw)
                        logger.info("[Orchestrator] PositionStore actualizat după adopție")
                    except Exception as e:
                        logger.warning("[Orchestrator] PositionStore update după adopție failed: {}", e)
            except Exception as exc:
                logger.error("[Orchestrator] Adoptie failed: {}", exc)
        else:
            logger.info("[Orchestrator] FAZA 3: Nicio pozitie orfana — skip")

        if ctx.has_adopted_positions:
            logger.info("[Orchestrator] FAZA 4: ProfitOptimizer ({} pozitii)", ctx.adopted_count)
            try:
                from execution.profit_optimizer import ProfitOptimizer
                from execution.adoption_engine import AdoptionDecision
                optimizer = ProfitOptimizer(self._exchange)
                for result in ctx.adoption_results:
                    if result.decision in (AdoptionDecision.ADOPT, AdoptionDecision.MONITOR_ONLY):
                        optimizer.register(result, current_price=getattr(result.position, "mark_price", 0.0))
                ctx.optimizer = optimizer
                logger.info("[Orchestrator] ProfitOptimizer: {} pozitii active", optimizer.active_count)
            except Exception as exc:
                logger.error("[Orchestrator] ProfitOptimizer init failed: {}", exc)
        else:
            logger.info("[Orchestrator] FAZA 4: Nicio pozitie adoptata — optimizer idle")

        logger.info("[Orchestrator] Startup workflow COMPLET — runner poate porni")
        logger.info(sep)
        return ctx

    async def start_runner(self, ctx=None, price_feed_callback=None) -> None:
        if ctx is not None and ctx.should_halt:
            logger.error("[Orchestrator] start_runner: context HALT — nu pornesc runner")
            return
        logger.info("[Orchestrator] FAZA 5: Pornire BybitLiveRunner")
        runner = self._runner or self._build_runner()
        tasks = [asyncio.create_task(runner.start(), name="bybit_live_runner")]
        if ctx is not None and ctx.has_adopted_positions and ctx.optimizer:
            cb = price_feed_callback or self._make_price_callback(
                [getattr(r, "symbol", "") for r in ctx.adoption_results]
            )
            tasks.append(asyncio.create_task(self.run_optimizer_loop(ctx, cb), name="optimizer_loop"))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("[Orchestrator] Tasks cancelled — stopping runner")
            await runner.stop()
        except Exception as exc:
            logger.error("[Orchestrator] Runner error: {}", exc)
            await self._alert(f"Runner error: {exc}")
            raise

    async def run_optimizer_loop(self, ctx, price_feed_callback, poll_interval_s=1.0) -> None:
        if not ctx.optimizer or ctx.optimizer.active_count == 0:
            return
        logger.info("[Orchestrator] Optimizer loop pornit: {} pozitii active", ctx.optimizer.active_count)
        while ctx.optimizer.active_count > 0:
            try:
                prices = await price_feed_callback()
                actions = await ctx.optimizer.tick(prices)
                for action in actions:
                    logger.info("[OLoop] {} {} qty={:.4f} reason={} PnL={:+.2f}",
                                action.symbol, action.action_type.value,
                                action.close_qty, action.reason, action.current_pnl)
            except asyncio.CancelledError:
                logger.info("[Orchestrator] Optimizer loop cancelled")
                return
            except Exception as exc:
                logger.warning("[Orchestrator] Optimizer loop error: {}", exc)
            await asyncio.sleep(poll_interval_s)
        logger.info("[Orchestrator] Optimizer loop: toate pozitiile inchise")

    async def _run_health_check(self):
        cfg = self._runner_cfg
        if cfg is None:
            return None
        try:
            from execution.health_check import HealthCheck, HealthConfig
            hc = HealthCheck(HealthConfig(
                exchange=getattr(cfg, "venue", "bybit"),
                sym_y=cfg.symbol_y, sym_x=cfg.symbol_x,
                api_key=os.getenv("BYBIT_API_KEY", ""),
                api_secret=os.getenv("BYBIT_API_SECRET", ""),
                testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
                min_capital_usdt=getattr(cfg, "min_capital_usdt", 100.0),
            ))
            report = await hc.run()
            report.print_report()
            return report
        except Exception as exc:
            logger.warning("[Orchestrator] HealthCheck error: {}", exc)
            return None

    async def _build_shared_exchange(self):
        try:
            from execution.exchange_factory import get_order_router
            cfg = self._runner_cfg
            is_live = cfg is not None and not cfg.dry_run and bool(cfg.api_key and cfg.api_secret)
            mode = "live" if is_live else "paper"
            logger.info("[Orchestrator] Construiesc exchange: mode={}", mode)
            router = get_order_router(
                exchange=getattr(cfg, "venue", "bybit") if cfg else "bybit",
                mode=mode,
                api_key=getattr(cfg, "api_key", "") if cfg else "",
                api_secret=getattr(cfg, "api_secret", "") if cfg else "",
                testnet=getattr(cfg, "testnet", False) if cfg else False,
                dry_run=not is_live,
            )
            # Pre-warm connection for live mode
            if is_live and hasattr(router, "connect"):
                await router.connect()
            return router
        except Exception as exc:
            logger.warning("[Orchestrator] get_order_router failed: {}", exc)
            return None

    def _build_runner(self):
        from execution.bybit_live_runner import BybitLiveRunner
        return BybitLiveRunner(
            cfg=self._runner_cfg, exchange=self._exchange,
            private_ws=self._private_ws, ws_feed=self._ws_feed,
            notifier_bus=self._bus,
        )

    def _make_price_callback(self, symbols):
        exchange = self._exchange
        async def _poll():
            prices = {}
            if exchange is None:
                return prices
            for sym in symbols:
                try:
                    ticker = await exchange.fetch_ticker(sym)
                    prices[sym] = float(ticker.get("last", 0) or 0)
                except Exception:
                    pass
            return prices
        return _poll

    async def _alert(self, message: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(message, level="error")
        except Exception as exc:
            logger.warning("[Orchestrator] alert failed: {}", exc)

    async def _emergency_stop(self, reason: str) -> None:
        try:
            from execution.emergency_stop import EmergencyStop
            es = EmergencyStop(exchange=self._exchange, alert_cfg=self._bus)
            await es.trigger(reason=reason)
        except Exception as exc:
            logger.error("[Orchestrator] EmergencyStop failed: {}", exc)

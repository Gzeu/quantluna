"""
execution/workflow_orchestrator.py  -  QuantLuna Startup Workflow Orchestrator v2

Sprint 28 rev-3:
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

Usage (din main.py):
    orch = WorkflowOrchestrator.from_runner_cfg(cfg, notifier_bus)
    ctx  = await orch.run_startup_workflow()
    if ctx.should_halt:
        sys.exit(1)
    await orch.start_runner(ctx)  # blocks until stop
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class StartupContext:
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
    Orchestrator complet Sprint 28.

    Preferred constructor: WorkflowOrchestrator.from_runner_cfg(cfg, bus)
    Manual constructor for tests: WorkflowOrchestrator(exchange=..., ...)

    def __init__(
        self,
        exchange=None,
        checkpoint_path: str = "state/position_checkpoint.db",
        notifier_bus=None,
        adoption_config=None,
        min_notional: float = 1.0,
        runner=None,
        runner_cfg=None,
        private_ws=None,
        ws_feed=None,
        skip_health_check: bool = False,
    ) -> None:
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

    @classmethod
    def from_runner_cfg(
        cls,
        cfg,
        notifier_bus=None,
        ws_feed=None,
        private_ws=None,
        skip_health_check: bool = False,
    ) -> "WorkflowOrchestrator":
        Build orchestrator from a BybitLiveRunnerConfig.
        Exchange is built lazily via ExchangeFactory inside run_startup_workflow.
        return cls(
            exchange=None,
            checkpoint_path=cfg.checkpoint_path,
            notifier_bus=notifier_bus,
            runner_cfg=cfg,
            ws_feed=ws_feed,
            private_ws=private_ws,
            skip_health_check=skip_health_check,
        )

    async def run_startup_workflow(self) -> StartupContext:
        Executa toate fazele de startup si returneaza StartupContext.
        Daca should_halt=True, apeleaza EmergencyStop si returneaza imediat.
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
                logger.error(f"[Orchestrator] HALT: {ctx.halt_reason}")
                await self._alert(f"Startup HALT (HealthCheck):\n{ctx.halt_reason}")
                await self._emergency_stop(ctx.halt_reason)
                return ctx
            logger.info("[Orchestrator] FAZA 0: HealthCheck OK")
        else:
            logger.info("[Orchestrator] FAZA 0: HealthCheck SKIPPED (skip_health_check=True)")

        if self._exchange is None:
            self._exchange = await self._build_shared_exchange()

        logger.info("[Orchestrator] FAZA 1: Scan pozitii exchange")
        try:
            from execution.position_scanner import PositionScanner
            from execution.checkpoint import PositionCheckpoint
            cp = PositionCheckpoint(self._checkpoint_path)
            scanner = PositionScanner(self._exchange, cp, self._min_notional)
            ctx.scan_report = await scanner.scan()
            logger.info(f"[Orchestrator] {ctx.scan_report.summary()}")
        except Exception as exc:
            logger.error(f"[Orchestrator] Scan failed: {exc} — skip")

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
                logger.error(f"[Orchestrator] HALT (ResumeManager): {ctx.halt_reason}")
                await self._alert(f"Startup HALT (reconciliere):\n{ctx.halt_reason}")
                await self._emergency_stop(ctx.halt_reason)
                return ctx
        except Exception as exc:
            logger.error(f"[Orchestrator] Reconciliere failed: {exc} — continuam")

        has_orphans = (
            ctx.scan_report is not None
            and getattr(ctx.scan_report, "has_orphans", False)
        )
        if has_orphans:
            orphan_count = len(ctx.scan_report.orphans)
            logger.info(f"[Orchestrator] FAZA 3: Adoptie {orphan_count} pozitii orfane")
            await self._alert(f"{orphan_count} pozitii orfane detectate — procesare...")
            try:
                from execution.adoption_engine import AdoptionEngine
                from execution.checkpoint import PositionCheckpoint
                cp = PositionCheckpoint(self._checkpoint_path)
                adoption = AdoptionEngine(
                    self._exchange, cp,
                    order_manager=None,
                    config=self._adoption_cfg
                )
                from execution.position_scanner import ScanReport
                temp_report = ScanReport(orphans=ctx.scan_report.orphans)
                ctx.adoption_results = await adoption.process_report(temp_report)
                logger.info(
                    f"[Orchestrator] Adoptie: {ctx.adopted_count} adoptate, "
                    f"{ctx.closed_count} inchise"
                )
            except Exception as exc:
                logger.error(f"[Orchestrator] Adoptie failed: {exc}")
        else:
            logger.info("[Orchestrator] FAZA 3: Nicio pozitie orfana — skip")

        if ctx.has_adopted_positions:
            logger.info(
                f"[Orchestrator] FAZA 4: ProfitOptimizer "
                f"({ctx.adopted_count} pozitii)"
            )
            try:
                from execution.profit_optimizer import ProfitOptimizer
                from execution.adoption_engine import AdoptionDecision
                optimizer = ProfitOptimizer(self._exchange)
                for result in ctx.adoption_results:
                    if result.decision in (
                        AdoptionDecision.ADOPT, AdoptionDecision.MONITOR_ONLY
                    ):
                        optimizer.register(
                            result,
                            current_price=getattr(result.position, "mark_price", 0.0),
                        )
                ctx.optimizer = optimizer
                logger.info(
                    f"[Orchestrator] ProfitOptimizer: "
                    f"{optimizer.active_count} pozitii active"
                )
            except Exception as exc:
                logger.error(f"[Orchestrator] ProfitOptimizer init failed: {exc}")
        else:
            logger.info("[Orchestrator] FAZA 4: Nicio pozitie adoptata — optimizer idle")

        logger.info("[Orchestrator] Startup workflow COMPLET — runner poate porni")
        logger.info(sep)
        return ctx

    async def start_runner(
        self,
        ctx: StartupContext,
        price_feed_callback: Optional[Callable[[], Awaitable[dict]]] = None,
    ) -> None:
        FAZA 5: Porneste BybitLiveRunner si (optional) optimizer loop in paralel.

        price_feed_callback: async () -> Dict[symbol, price]
          Daca e None si exista pozitii adoptate, se foloseste un poller simplu
          pe self._exchange.

        if ctx.should_halt:
            logger.error("[Orchestrator] start_runner: context HALT — nu pornesc runner")
            return

        logger.info("[Orchestrator] FAZA 5: Pornire BybitLiveRunner")

        runner = self._runner
        if runner is None:
            runner = self._build_runner()

        tasks = [asyncio.create_task(runner.start(), name="bybit_live_runner")]

        if ctx.has_adopted_positions and ctx.optimizer:
            cb = price_feed_callback or self._make_price_callback(
                [getattr(r, "symbol", "") for r in ctx.adoption_results]
            )
            tasks.append(
                asyncio.create_task(
                    self.run_optimizer_loop(ctx, cb), name="optimizer_loop"
                )
            )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("[Orchestrator] Tasks cancelled — stopping runner")
            await runner.stop()
        except Exception as exc:
            logger.error(f"[Orchestrator] Runner error: {exc}")
            await self._alert(f"Runner error: {exc}")
            raise

    async def run_optimizer_loop(
        self,
        ctx: StartupContext,
        price_feed_callback: Callable[[], Awaitable[dict]],
        poll_interval_s: float = 1.0,
    ) -> None:
        if not ctx.optimizer or ctx.optimizer.active_count == 0:
            logger.info("[Orchestrator] Optimizer loop: nicio pozitie de monitorizat")
            return

        logger.info(
            f"[Orchestrator] Optimizer loop pornit: "
            f"{ctx.optimizer.active_count} pozitii active"
        )
        while ctx.optimizer.active_count > 0:
            try:
                prices = await price_feed_callback()
                actions = await ctx.optimizer.on_price_tick(prices)
                for action in actions:
                    logger.info(
                        f"[OLoop] {action.symbol} {action.action_type.value} "
                        f"qty={action.close_qty:.4f} reason={action.reason} "
                        f"PnL={action.current_pnl:+.2f}"
                    )
            except asyncio.CancelledError:
                logger.info("[Orchestrator] Optimizer loop cancelled")
                return
            except Exception as exc:
                logger.warning(f"[Orchestrator] Optimizer loop error: {exc}")
            await asyncio.sleep(poll_interval_s)

        logger.info("[Orchestrator] Optimizer loop: toate pozitiile inchise")

    async def _run_health_check(self):
        cfg = self._runner_cfg
        if cfg is None:
            logger.warning("[Orchestrator] No runner_cfg — skipping HealthCheck")
            return None
        try:
            from execution.health_check import HealthCheck, HealthConfig
            hc = HealthCheck(HealthConfig(
                exchange=getattr(cfg, "venue", "bybit"),
                sym_y=cfg.symbol_y,
                sym_x=cfg.symbol_x,
                api_key=os.getenv("BYBIT_API_KEY", ""),
                api_secret=os.getenv("BYBIT_API_SECRET", ""),
                testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
                min_capital_usdt=getattr(cfg, "min_capital_usdt", 100.0),
            ))
            report = await hc.run()
            report.print_report()
            return report
        except Exception as exc:
            logger.warning(f"[Orchestrator] HealthCheck error: {exc}")
            return None

    async def _build_shared_exchange(self):
        Build shared CCXT instance via get_order_router.
        try:
            from execution.exchange_factory import get_order_router
            return get_order_router(
                exchange=getattr(self._runner_cfg, "venue", "bybit") if self._runner_cfg else "bybit",
                mode="paper" if getattr(self._runner_cfg, "dry_run", True) else "live",
            )
        except Exception as exc:
            logger.warning(f"[Orchestrator] get_order_router failed: {exc}")
            return None

    def _build_runner(self):
        Build BybitLiveRunner from runner_cfg.
        from execution.bybit_live_runner import BybitLiveRunner
        return BybitLiveRunner(
            cfg=self._runner_cfg,
            exchange=self._exchange,
            private_ws=self._private_ws,
            ws_feed=self._ws_feed,
            notifier_bus=self._bus,
        )

    def _make_price_callback(
        self, symbols: list
    ) -> Callable[[], Awaitable[dict]]:
        Fallback price poller via CCXT ticker for optimizer loop.
        exchange = self._exchange

        async def _poll() -> dict:
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
            logger.warning(f"[Orchestrator] alert failed: {exc}")

    async def _emergency_stop(self, reason: str) -> None:
        try:
            from execution.emergency_stop import EmergencyStop
            es = EmergencyStop(
                exchange=self._exchange,
                alert_cfg=self._bus,
            )
            await es.trigger(reason=reason)
        except Exception as exc:
            logger.error(f"[Orchestrator] EmergencyStop failed: {exc}")
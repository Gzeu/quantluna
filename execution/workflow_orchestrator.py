"""
execution/workflow_orchestrator.py  -  QuantLuna Startup Workflow Orchestrator v3.2

Sprint S29 v3.9 (2026-07-12):
  FEAT-C1 [CRITIC]  FAZA 3.5 NOU: StrategyClassifier dupa boot scan
                    - clasifica TOATE pozitiile din cont
                    - solo_hedges → porneste SingleHedgeManager per simbol
                    - orphans     → alertă Telegram, nu se atinge nimic
  FEAT-C2 [CRITIC]  start_runner() porneste acum:
                    runner + hedge_tasks (per simbol solo) + optimizer_loop
                    Toate ca asyncio.Tasks in gather()
  FEAT-C3          StartupContext primeste hedge_managers + classified_result

Sprint 28 -> v3.1 (2026-07-11):
  FIX-O1 [CRITIC] _build_runner() — eliminat parametrul exchange= invalid
  FIX-O2 [MEDIU]  start_runner() — log explicit cand runner-ul e injectat extern

Sprint 28 -> v3 (2026-07-11):
  FAZA 0: Pre-flight HealthCheck      <- health_check.HealthCheck
  FAZA 1: Scan pozitii exchange        <- position_scanner.PositionScanner
  FAZA 2: Reconciliere checkpoint      <- resume_manager.ResumeManager
  FAZA 3: Adoptie pozitii orfane       <- adoption_engine.AdoptionEngine
  FAZA 3.5: StrategyClassifier         <- strategy_classifier [NOU v3.9]
  FAZA 4: Initializare ProfitOptimizer <- profit_optimizer.ProfitOptimizer
  FAZA 5: Pornire runner + hedge tasks <- bybit_live_runner + single_hedge_manager
  FAZA 6: Dashboard health ping        <- Next.js :3000 + FastAPI :8000

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
from typing import Any, Awaitable, Callable, List, Optional

from loguru import logger

_DASHBOARD_PING_RETRIES = 3
_DASHBOARD_PING_DELAY_S = 2.0
_DASHBOARD_PING_TIMEOUT_S = 5.0


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

    dashboard_api_ok: bool = False
    dashboard_frontend_ok: bool = False

    # FEAT-C3 v3.9: rezultatul clasificarii
    classified_result: Optional[Any] = None
    hedge_managers: List[Any] = field(default_factory=list)

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
                if r.decision in (
                    AdoptionDecision.ADOPT, AdoptionDecision.MONITOR_ONLY
                )
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
    Orchestrator complet Sprint S29 -> v3.2.

    Preferred constructor: ``WorkflowOrchestrator.from_runner_cfg(cfg, bus)``
    """

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
        dashboard_api_url: str = "",
        dashboard_frontend_url: str = "",
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
        self._dashboard_api_url = (
            dashboard_api_url
            or os.getenv("DASHBOARD_API_URL", "http://localhost:8000")
        )
        self._dashboard_frontend_url = (
            dashboard_frontend_url
            or os.getenv("DASHBOARD_FRONTEND_URL", "http://localhost:3000")
        )

    @classmethod
    def from_runner_cfg(
        cls,
        cfg,
        notifier_bus=None,
        ws_feed=None,
        private_ws=None,
        skip_health_check: bool = False,
        dashboard_api_url: str = "",
        dashboard_frontend_url: str = "",
    ) -> "WorkflowOrchestrator":
        return cls(
            exchange=None,
            checkpoint_path=cfg.checkpoint_path,
            notifier_bus=notifier_bus,
            runner_cfg=cfg,
            ws_feed=ws_feed,
            private_ws=private_ws,
            skip_health_check=skip_health_check,
            dashboard_api_url=dashboard_api_url,
            dashboard_frontend_url=dashboard_frontend_url,
        )

    async def run_startup_workflow(self) -> StartupContext:
        ctx = StartupContext()
        sep = "=" * 60

        logger.info(sep)
        logger.info("[Orchestrator] QuantLuna startup workflow START")

        # FAZA 0: HealthCheck
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
            logger.info(
                "[Orchestrator] FAZA 0: HealthCheck SKIPPED (skip_health_check=True)"
            )

        if self._exchange is None:
            self._exchange = await self._build_shared_exchange()

        # FAZA 1: Scan pozitii
        logger.info("[Orchestrator] FAZA 1: Scan pozitii exchange")
        try:
            from execution.position_scanner import PositionScanner
            from execution.checkpoint import PositionCheckpoint
            cp = PositionCheckpoint(self._checkpoint_path)
            scanner = PositionScanner(self._exchange, cp, self._min_notional)
            ctx.scan_report = await scanner.scan()
            logger.info("[Orchestrator] {}", ctx.scan_report.summary())
        except Exception as exc:
            logger.error("[Orchestrator] Scan failed: {} - skip", exc)

        # FAZA 2: Reconciliere checkpoint
        logger.info("[Orchestrator] FAZA 2: Reconciliere checkpoint")
        try:
            from execution.resume_manager import ResumeManager
            from execution.checkpoint import PositionCheckpoint
            cp = PositionCheckpoint(self._checkpoint_path)
            resume = ResumeManager(cp, self._exchange, self._bus)
            ctx.reconcile_result = await resume.reconcile_on_startup()
            if getattr(ctx.reconcile_result, "should_halt", False):
                ctx.should_halt = True
                ctx.halt_reason = getattr(
                    ctx.reconcile_result, "message", "reconcile halt"
                )
                logger.error(
                    "[Orchestrator] HALT (ResumeManager): {}", ctx.halt_reason
                )
                await self._alert(
                    f"Startup HALT (reconciliere):\n{ctx.halt_reason}"
                )
                await self._emergency_stop(ctx.halt_reason)
                return ctx
        except Exception as exc:
            logger.error("[Orchestrator] Reconciliere failed: {} - continuam", exc)

        # FAZA 3: Adoptie pozitii orfane (AdoptionEngine existent)
        has_orphans = (
            ctx.scan_report is not None
            and getattr(ctx.scan_report, "has_orphans", False)
        )
        if has_orphans:
            orphan_count = len(ctx.scan_report.orphans)
            logger.info(
                "[Orchestrator] FAZA 3: Adoptie {} pozitii orfane", orphan_count
            )
            await self._alert(
                f"{orphan_count} pozitii orfane detectate - procesare..."
            )
            try:
                from execution.adoption_engine import AdoptionEngine
                from execution.checkpoint import PositionCheckpoint
                from execution.position_scanner import ScanReport
                cp = PositionCheckpoint(self._checkpoint_path)
                adoption = AdoptionEngine(
                    self._exchange, cp,
                    order_manager=None,
                    config=self._adoption_cfg,
                )
                temp_report = ScanReport(orphans=ctx.scan_report.orphans)
                ctx.adoption_results = await adoption.process_report(temp_report)
                logger.info(
                    "[Orchestrator] Adoptie: {} adoptate, {} inchise",
                    ctx.adopted_count, ctx.closed_count,
                )
            except Exception as exc:
                logger.error("[Orchestrator] Adoptie failed: {}", exc)
        else:
            logger.info("[Orchestrator] FAZA 3: Nicio pozitie orfana - skip")

        # FAZA 3.5 NOU v3.9: StrategyClassifier
        # Ruleaza DUPA boot_scan din BybitLiveRunner (Phase 0.5)
        # Clasifica toate pozitiile si pregateste HedgeManagers
        logger.info("[Orchestrator] FAZA 3.5: StrategyClassifier v3.9")
        await self._run_strategy_classifier(ctx)

        # FAZA 4: ProfitOptimizer
        if ctx.has_adopted_positions:
            logger.info(
                "[Orchestrator] FAZA 4: ProfitOptimizer ({} pozitii)",
                ctx.adopted_count,
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
                            current_price=getattr(
                                result.position, "mark_price", 0.0
                            ),
                        )
                ctx.optimizer = optimizer
                logger.info(
                    "[Orchestrator] ProfitOptimizer: {} pozitii active",
                    optimizer.active_count,
                )
            except Exception as exc:
                logger.error(
                    "[Orchestrator] ProfitOptimizer init failed: {}", exc
                )
        else:
            logger.info(
                "[Orchestrator] FAZA 4: Nicio pozitie adoptata — optimizer idle"
            )

        # FAZA 6: Dashboard health ping
        logger.info("[Orchestrator] FAZA 6: Dashboard health ping")
        ctx.dashboard_api_ok = await self._ping_url(
            f"{self._dashboard_api_url}/api/health",
            label="FastAPI dashboard :8000",
        )
        ctx.dashboard_frontend_ok = await self._ping_url(
            self._dashboard_frontend_url,
            label="Next.js frontend :3000",
        )

        if ctx.dashboard_api_ok and ctx.dashboard_frontend_ok:
            logger.info(
                "[Orchestrator] FAZA 6: Dashboard FULL UP — API={} UI={}",
                self._dashboard_api_url,
                self._dashboard_frontend_url,
            )
        elif ctx.dashboard_api_ok:
            logger.warning(
                "[Orchestrator] FAZA 6: FastAPI OK dar Next.js offline ({}). "
                "Porneste cu: docker compose --profile dashboard up",
                self._dashboard_frontend_url,
            )
        elif ctx.dashboard_frontend_ok:
            logger.warning(
                "[Orchestrator] FAZA 6: Next.js OK dar FastAPI offline ({}). "
                "Verifica: docker compose --profile dashboard up",
                self._dashboard_api_url,
            )
        else:
            logger.info(
                "[Orchestrator] FAZA 6: Dashboard offline — runner porneste fara UI. "
                "Porneste cu: docker compose --profile dashboard up"
            )

        # Startup complet
        hedge_info = (
            f" | HedgeManagers: {len(ctx.hedge_managers)}"
            if ctx.hedge_managers else ""
        )
        dashboard_status = (
            f"UI: {self._dashboard_frontend_url}" if ctx.dashboard_frontend_ok
            else "UI offline"
        )
        api_status = (
            f"API: {self._dashboard_api_url}" if ctx.dashboard_api_ok
            else "API offline"
        )
        logger.info(
            "[Orchestrator] Startup workflow COMPLET | {} | {}{}",
            dashboard_status, api_status, hedge_info,
        )
        logger.info(sep)

        hedge_line = (
            f"\n  HedgeManagers activi: {len(ctx.hedge_managers)} "
            f"({', '.join(m._symbol for m in ctx.hedge_managers)})"
            if ctx.hedge_managers else ""
        )
        await self._alert(
            f"QuantLuna pornit OK\n"
            f"  Dashboard: {dashboard_status} | {api_status}\n"
            f"  Pairs: {getattr(self._runner_cfg, 'symbol_y', '?')} / "
            f"{getattr(self._runner_cfg, 'symbol_x', '?')}\n"
            f"  Mode: {'DRY' if getattr(self._runner_cfg, 'dry_run', True) else 'LIVE'}"
            f"{hedge_line}"
        )
        return ctx

    async def start_runner(
        self,
        ctx: StartupContext,
        price_feed_callback: Optional[Callable[[], Awaitable[dict]]] = None,
    ) -> None:
        if ctx.should_halt:
            logger.error(
                "[Orchestrator] start_runner: context HALT — nu pornesc runner"
            )
            return

        logger.info("[Orchestrator] FAZA 5: Pornire BybitLiveRunner + HedgeManagers")

        runner = self._runner
        if runner is None:
            runner = self._build_runner()
            logger.info("[Orchestrator] Runner construit intern de orchestrator")
        else:
            logger.info("[Orchestrator] Runner injectat extern — folosit direct")

        # Task principal: pairs runner
        tasks = [
            asyncio.create_task(runner.start(), name="bybit_live_runner")
        ]

        # FEAT-C2: Task per SingleHedgeManager
        for mgr in ctx.hedge_managers:
            task = asyncio.create_task(
                mgr.manage(),
                name=f"hedge_{mgr._symbol}",
            )
            tasks.append(task)
            logger.info(
                "[Orchestrator] HedgeManager task pornit: %s", mgr._symbol
            )

        # Optimizer loop (existent)
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
            runner.stop()
            for mgr in ctx.hedge_managers:
                mgr.stop()
        except Exception as exc:
            logger.error("[Orchestrator] Runner error: {}", exc)
            await self._alert(f"Runner error: {exc}")
            raise

    async def run_optimizer_loop(
        self,
        ctx: StartupContext,
        price_feed_callback: Callable[[], Awaitable[dict]],
        poll_interval_s: float = 1.0,
    ) -> None:
        if not ctx.optimizer or ctx.optimizer.active_count == 0:
            logger.info(
                "[Orchestrator] Optimizer loop: nicio pozitie de monitorizat"
            )
            return

        logger.info(
            "[Orchestrator] Optimizer loop pornit: {} pozitii active",
            ctx.optimizer.active_count,
        )
        while ctx.optimizer.active_count > 0:
            try:
                prices = await price_feed_callback()
                actions = await ctx.optimizer.on_price_tick(prices)
                for action in actions:
                    logger.info(
                        "[OLoop] {} {} qty={:.4f} reason={} PnL={:+.2f}",
                        action.symbol,
                        action.action_type.value,
                        action.close_qty,
                        action.reason,
                        action.current_pnl,
                    )
            except asyncio.CancelledError:
                logger.info("[Orchestrator] Optimizer loop cancelled")
                return
            except Exception as exc:
                logger.warning("[Orchestrator] Optimizer loop error: {}", exc)
            await asyncio.sleep(poll_interval_s)

        logger.info("[Orchestrator] Optimizer loop: toate pozitiile inchise")

    # ------------------------------------------------------------------
    # FAZA 3.5: StrategyClassifier + HedgeManager instantiere
    # ------------------------------------------------------------------

    async def _run_strategy_classifier(self, ctx: StartupContext) -> None:
        """
        FEAT-C1 v3.9: Clasifica toate pozitiile detectate la boot.

        Foloseste BootScanResult din BybitLiveRunner (daca runner-ul a rulat
        deja Phase 0.5) sau face un boot_scan direct daca e disponibil.

        Pentru fiecare SoloHedgeGroup detectat:
          - Instantiaza SingleHedgeManager
          - Il adauga in ctx.hedge_managers (pornit ulterior in start_runner)

        Pentru orphans:
          - Trimite alerta Telegram
          - Nu face nimic altceva
        """
        try:
            from execution.strategy_classifier import StrategyClassifier
            from execution.single_hedge_manager import (
                SingleHedgeManager, SingleHedgeConfig,
            )
        except ImportError as exc:
            logger.warning(
                "[Orchestrator] StrategyClassifier import failed: %s — skip FAZA 3.5",
                exc,
            )
            return

        # Obtine BootScanResult
        boot_result = await self._get_boot_scan_result()
        if boot_result is None:
            logger.info(
                "[Orchestrator] FAZA 3.5: BootScanResult indisponibil — skip clasificare"
            )
            return

        # Configureaza perechile cunoscute
        symbol_y = getattr(self._runner_cfg, "symbol_y", "BTCUSDT")
        symbol_x = getattr(self._runner_cfg, "symbol_x", "ETHUSDT")
        classifier = StrategyClassifier(pairs=[(symbol_y, symbol_x)])
        classified = classifier.classify(boot_result)
        ctx.classified_result = classified

        # Alerta Telegram cu clasificarea (solo_hedges + orphans)
        if classified.solo_hedges or classified.orphans:
            msg = classified.to_telegram_msg()
            if msg:
                await self._alert(msg)

        # Alertă orfani (nu se atinge nimic)
        if classified.orphans:
            symbols = [op.symbol for op in classified.orphans]
            logger.warning(
                "[Orchestrator] FAZA 3.5: %d pozitii ORFANE detectate — "
                "nu se atinge nimic: %s",
                len(classified.orphans), symbols,
            )

        # Instantiaza SingleHedgeManager per simbol solo
        if not classified.solo_hedges:
            logger.info(
                "[Orchestrator] FAZA 3.5: Nicio pozitie solo detectata — "
                "HedgeManager nu e necesar"
            )
            return

        hedge_cfg = SingleHedgeConfig(
            trailing_sl_pct=getattr(self._runner_cfg, "sl_pct", 0.015),
            initial_sl_pct=getattr(self._runner_cfg, "sl_pct", 0.03),
            initial_tp_pct=getattr(self._runner_cfg, "tp_pct", 0.06),
            category=getattr(self._runner_cfg, "bybit_category", "linear"),
        )

        for group in classified.solo_hedges:
            mgr = SingleHedgeManager(
                group=group,
                order_router=self._exchange,
                notifier_bus=self._bus,
                cfg=hedge_cfg,
            )
            ctx.hedge_managers.append(mgr)
            logger.info(
                "[Orchestrator] FAZA 3.5: SingleHedgeManager creat pt %s (%s)",
                group.symbol, group.dominant_side,
            )

        logger.info(
            "[Orchestrator] FAZA 3.5: %d HedgeManagers pregatiti",
            len(ctx.hedge_managers),
        )

    async def _get_boot_scan_result(self):
        """
        Obtine BootScanResult:
          1. Din runner injectat (daca a rulat deja Phase 0.5 si are _boot_scan_result)
          2. Din runner_cfg via PositionReconciler direct
          3. None daca nu e disponibil
        """
        # 1. Din runner deja rulat
        if self._runner is not None:
            result = getattr(self._runner, "_boot_scan_result", None)
            if result is not None:
                logger.debug(
                    "[Orchestrator] BootScanResult obtinut din runner injectat"
                )
                return result

        # 2. Direct via PositionReconciler (cand orchestratorul ruleaza inainte de runner)
        if self._exchange is not None and self._runner_cfg is not None:
            try:
                from execution.position_reconciler import PositionReconciler
                reconciler = PositionReconciler(
                    order_router=self._exchange,
                    symbol_y=getattr(self._runner_cfg, "symbol_y", "BTCUSDT"),
                    symbol_x=getattr(self._runner_cfg, "symbol_x", "ETHUSDT"),
                    category=getattr(
                        self._runner_cfg, "bybit_category", "linear"
                    ),
                )
                logger.info(
                    "[Orchestrator] FAZA 3.5: boot_scan() direct via PositionReconciler"
                )
                return await reconciler.boot_scan()
            except Exception as exc:
                logger.warning(
                    "[Orchestrator] boot_scan direct failed: %s", exc
                )

        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _ping_url(
        self,
        url: str,
        label: str = "",
        retries: int = _DASHBOARD_PING_RETRIES,
        delay_s: float = _DASHBOARD_PING_DELAY_S,
        timeout_s: float = _DASHBOARD_PING_TIMEOUT_S,
    ) -> bool:
        import urllib.request
        import urllib.error

        for attempt in range(1, retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "QuantLuna-Orchestrator/3"},
                )
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    if resp.status < 500:
                        logger.debug(
                            "[Orchestrator] {} ping OK (HTTP {})",
                            label, resp.status,
                        )
                        return True
            except Exception as exc:
                logger.debug(
                    "[Orchestrator] {} ping attempt {}/{} failed: {}",
                    label, attempt, retries, exc,
                )
                if attempt < retries:
                    await asyncio.sleep(delay_s)
        return False

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
            logger.warning("[Orchestrator] HealthCheck error: {}", exc)
            return None

    async def _build_shared_exchange(self):
        try:
            from execution.exchange_factory import get_order_router
            return get_order_router(
                exchange=(
                    getattr(self._runner_cfg, "venue", "bybit")
                    if self._runner_cfg else "bybit"
                ),
                mode=(
                    "paper"
                    if getattr(self._runner_cfg, "dry_run", True)
                    else "live"
                ),
            )
        except Exception as exc:
            logger.warning("[Orchestrator] get_order_router failed: {}", exc)
            return None

    def _build_runner(self):
        """
        FIX-O1: Instanta BybitLiveRunner v3.6+ cu parametrii corecti.
        """
        from execution.bybit_live_runner import BybitLiveRunner
        return BybitLiveRunner(
            cfg=self._runner_cfg,
            notifier_bus=self._bus,
            ws_feed=self._ws_feed,
            private_ws=self._private_ws,
        )

    def _make_price_callback(
        self, symbols: list
    ) -> Callable[[], Awaitable[dict]]:
        exchange = self._exchange

        async def _poll() -> dict:
            prices: dict = {}
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
            es = EmergencyStop(
                exchange=self._exchange,
                alert_cfg=self._bus,
            )
            await es.trigger(reason=reason)
        except Exception as exc:
            logger.error("[Orchestrator] EmergencyStop failed: {}", exc)

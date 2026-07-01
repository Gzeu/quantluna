"""
execution/workflow_orchestrator.py  —  QuantLuna Startup Workflow Orchestrator

Planul complet de workflow la startup:

  FAZA 0: Pre-flight guard (preflight_check.py deja există)
  FAZA 1: Scan pozitii exchange  ← PositionScanner
  FAZA 2: Reconciliere checkpoint  ← ResumeManager
  FAZA 3: Adoptie pozitii orfane   ← AdoptionEngine
  FAZA 4: Initializare optimizer    ← ProfitOptimizer.register()
  FAZA 5: Pornire LiveTrader normal ← LiveTrader.run()

Orchestratorul rulează fazele 1-4 înainte de a ceda controlul
catre LiveTrader, asiȟurând că ORICE pozitie de pe cont
e cunoscută şi gestionată.

Usage:
    orch = WorkflowOrchestrator(config, exchange, alert_cfg)
    ctx  = await orch.run_startup_workflow()

    if ctx.should_halt:
        # halt şi notificare operator
    elif ctx.has_adopted_positions:
        # porneste optimizer loop în background
    # porneste LiveTrader normal
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from execution.checkpoint import PositionCheckpoint
from execution.position_scanner import PositionScanner, ScanReport
from execution.resume_manager import ResumeManager, ReconcileResult
from execution.adoption_engine import AdoptionEngine, AdoptionResult, AdoptionDecision, AdoptionConfig
from execution.profit_optimizer import ProfitOptimizer

logger = logging.getLogger(__name__)


@dataclass
class StartupContext:
    scan_report: Optional[ScanReport]          = None
    reconcile_result: Optional[ReconcileResult] = None
    adoption_results: List[AdoptionResult]     = field(default_factory=list)
    optimizer: Optional[ProfitOptimizer]       = None

    should_halt: bool  = False
    halt_reason: str   = ""

    @property
    def has_adopted_positions(self) -> bool:
        return any(
            r.decision in (AdoptionDecision.ADOPT, AdoptionDecision.MONITOR_ONLY)
            for r in self.adoption_results
        )

    @property
    def adopted_count(self) -> int:
        return sum(
            1 for r in self.adoption_results
            if r.decision in (AdoptionDecision.ADOPT, AdoptionDecision.MONITOR_ONLY)
        )

    @property
    def closed_count(self) -> int:
        return sum(
            1 for r in self.adoption_results
            if r.decision == AdoptionDecision.CLOSE_NOW
        )


class WorkflowOrchestrator:
    """
    Orchetratore de startup complet.

    Args:
        exchange:        ccxt async exchange instance
        checkpoint_path: calea catre position_checkpoint.db
        alert_cfg:       AlertConfig
        adoption_config: AdoptionConfig (praguri decizie)
        min_notional:    minim notional pentru scan pozitii
    """

    def __init__(
        self,
        exchange,
        checkpoint_path: str = "position_checkpoint.db",
        alert_cfg=None,
        adoption_config: Optional[AdoptionConfig] = None,
        min_notional: float = 1.0,
    ) -> None:
        self._exchange   = exchange
        self._alert      = alert_cfg
        self._cp         = PositionCheckpoint(checkpoint_path)
        self._scanner    = PositionScanner(exchange, self._cp, min_notional)
        self._resume     = ResumeManager(self._cp, exchange, alert_cfg)
        self._adoption   = AdoptionEngine(exchange, self._cp, alert_cfg, adoption_config)
        self._optimizer  = ProfitOptimizer(exchange, alert_cfg)

    async def run_startup_workflow(self) -> StartupContext:
        """
        Execută toate fazele de startup si returnează StartupContext.
        """
        ctx = StartupContext()

        # --- FAZA 1: Scan pozitii exchange ---
        logger.info("=" * 60)
        logger.info("[Orchestrator] FAZA 1: Scan pozitii exchange")
        try:
            ctx.scan_report = await self._scanner.scan()
            logger.info(f"[Orchestrator] {ctx.scan_report.summary()}")
        except Exception as exc:
            logger.error(f"[Orchestrator] Scan failed: {exc} — skip")
            ctx.scan_report = None

        # --- FAZA 2: Reconciliere checkpoint ---
        logger.info("[Orchestrator] FAZA 2: Reconciliere checkpoint")
        try:
            ctx.reconcile_result = await self._resume.reconcile_on_startup()
            if ctx.reconcile_result.should_halt:
                ctx.should_halt  = True
                ctx.halt_reason  = ctx.reconcile_result.message
                logger.error(f"[Orchestrator] HALT cerut de ResumeManager: {ctx.halt_reason}")
                await self._send_alert(
                    f"❌ Startup HALT (reconciliere):\n{ctx.halt_reason}"
                )
                return ctx
        except Exception as exc:
            logger.error(f"[Orchestrator] Reconciliere failed: {exc} — continuam")

        # --- FAZA 3: Adoptie pozitii orfane ---
        if ctx.scan_report and ctx.scan_report.has_orphans:
            orphan_count = len(ctx.scan_report.orphans)
            logger.info(
                f"[Orchestrator] FAZA 3: Adoptie {orphan_count} pozitii orfane"
            )
            await self._send_alert(
                f"⚠️ {orphan_count} pozitii orfane detectate — procesare automată..."
            )
            try:
                ctx.adoption_results = await self._adoption.adopt_all(
                    ctx.scan_report.orphans
                )
                logger.info(
                    f"[Orchestrator] Adoptie completă: "
                    f"{ctx.adopted_count} adoptate, "
                    f"{ctx.closed_count} închise"
                )
            except Exception as exc:
                logger.error(f"[Orchestrator] Adoptie failed: {exc}")
        else:
            logger.info("[Orchestrator] FAZA 3: Nicio pozitie orfană — skip")

        # --- FAZA 4: Initializare ProfitOptimizer ---
        if ctx.has_adopted_positions:
            logger.info(
                f"[Orchestrator] FAZA 4: Initializare ProfitOptimizer "
                f"({ctx.adopted_count} pozitii)"
            )
            for result in ctx.adoption_results:
                if result.decision in (
                    AdoptionDecision.ADOPT,
                    AdoptionDecision.MONITOR_ONLY,
                ):
                    self._optimizer.register(
                        result,
                        current_price=result.position.mark_price,
                    )
            ctx.optimizer = self._optimizer
            logger.info(
                f"[Orchestrator] ProfitOptimizer activ: "
                f"{self._optimizer.active_count} pozitii urmărite"
            )
        else:
            logger.info("[Orchestrator] FAZA 4: Nicio pozitie adoptată — optimizer idle")

        logger.info("[Orchestrator] Startup workflow complet — LiveTrader poate porni")
        logger.info("=" * 60)
        return ctx

    async def run_optimizer_loop(
        self,
        ctx: StartupContext,
        price_feed_callback,
        poll_interval_s: float = 1.0,
    ) -> None:
        """
        Loop de monitoring pentru poziții adoptate.
        Rulează în background task, paralel cu LiveTrader.

        `price_feed_callback` e un callable async care returnează
        Dict[symbol, price] la fiecare apel.

        Exemplu integrare in main.py:
            asyncio.create_task(
                orchestrator.run_optimizer_loop(ctx, get_prices)
            )
        """
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
                        f"[OLoop] Action: {action.symbol} {action.action_type.value} "
                        f"qty={action.close_qty:.4f} reason={action.reason} "
                        f"PnL={action.current_pnl:+.2f}"
                    )
            except asyncio.CancelledError:
                logger.info("[Orchestrator] Optimizer loop cancelled")
                return
            except Exception as exc:
                logger.warning(f"[Orchestrator] Optimizer loop error: {exc}")
            await asyncio.sleep(poll_interval_s)

        logger.info("[Orchestrator] Optimizer loop: toate pozițiile închise")

    async def _send_alert(self, message: str) -> None:
        if not self._alert:
            return
        try:
            from execution.live_trader import _send_alert
            await _send_alert(self._alert, message)
        except Exception as exc:
            logger.warning(f"[Orchestrator] alert failed: {exc}")

    @property
    def checkpoint(self) -> PositionCheckpoint:
        return self._cp

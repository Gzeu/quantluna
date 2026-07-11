"""
core/workflow_orchestrator.py  -  QuantLuna WorkflowOrchestrator v2.0

Sprint S37 (2026-07-12):
  Inlocuieste BybitLiveRunner din start_runner() cu MultiMarketRunner.
  Detectie automata: daca runner_cfg contine enable_spot sau enable_margin,
  se foloseste MultiMarketRunner; altfel comportament vechi (backward compat).

  Patch-uri fata de v1.0:
    - _build_runner() → returneaza MultiMarketRunner sau BybitLiveRunner
    - StartupContext primeste spot_router + margin_router
    - start_runner() delega la MultiMarketRunner.start()
    - stop_runner() apeleaza MultiMarketRunner.stop()
    - Notificare Telegram la startup cu lista markets active

  Compatibilitate backwards:
    Daca MultiMarketRunner nu e disponibil (ImportError),
    fall-back automat la BybitLiveRunner existent.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# StartupContext
# ---------------------------------------------------------------------------

@dataclass
class StartupContext:
    """
    Context creat de WorkflowOrchestrator si transmis tuturor componentelor.
    """
    runner_cfg: Any
    notifier_bus: Any = None
    futures_runner: Any = None        # BybitLiveRunner
    spot_router: Any = None           # SpotOrderRouter (S30)
    margin_router: Any = None         # MarginOrderRouter (S35)
    hedge_managers: List[Any] = field(default_factory=list)
    optimizer: Any = None
    pnl_tracker: Any = None           # DailyPnLTracker (S31)
    capital_allocator: Any = None     # CapitalAllocator (S31)
    state_bus: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# WorkflowOrchestrator
# ---------------------------------------------------------------------------

class WorkflowOrchestrator:
    """
    Orchestratorul principal al QuantLuna.

    v2.0 — foloseste MultiMarketRunner daca sunt active mai multe piete.
    v1.x — folosea direct BybitLiveRunner (ramas ca fallback).
    """

    VERSION = "2.0.0"

    def __init__(
        self,
        runner_cfg,
        notifier_bus=None,
        state_bus=None,
    ) -> None:
        self._runner_cfg = runner_cfg
        self._bus = notifier_bus
        self._state_bus = state_bus
        self._runner = None          # MultiMarketRunner sau BybitLiveRunner
        self._ctx: Optional[StartupContext] = None
        self._started = False

    # ------------------------------------------------------------------
    # Build context
    # ------------------------------------------------------------------

    def _build_context(self) -> StartupContext:
        """Construieste StartupContext cu toti routerele necesare."""
        cfg = self._runner_cfg
        ctx = StartupContext(
            runner_cfg=cfg,
            notifier_bus=self._bus,
            state_bus=self._state_bus,
        )

        # BybitLiveRunner (Futures)
        try:
            from execution.bybit_live_runner import BybitLiveRunner
            ctx.futures_runner = BybitLiveRunner.from_env(cfg)
            logger.info("[WFOrchestrator] BybitLiveRunner init OK")
        except Exception as exc:
            logger.warning("[WFOrchestrator] BybitLiveRunner init failed: {}", exc)

        # SpotOrderRouter (daca enable_spot)
        if getattr(cfg, "enable_spot", False):
            try:
                from execution.spot_order_router import SpotOrderRouter
                ctx.spot_router = SpotOrderRouter.from_env()
                logger.info("[WFOrchestrator] SpotOrderRouter init OK")
            except Exception as exc:
                logger.warning("[WFOrchestrator] SpotOrderRouter init failed: {}", exc)

        # MarginOrderRouter (daca enable_margin)
        if getattr(cfg, "enable_margin", False):
            try:
                from execution.margin_order_router import MarginOrderRouter
                ctx.margin_router = MarginOrderRouter.from_env(
                    margin_mode=getattr(cfg, "margin_mode", "cross")
                )
                logger.info("[WFOrchestrator] MarginOrderRouter init OK")
            except Exception as exc:
                logger.warning(
                    "[WFOrchestrator] MarginOrderRouter init failed: {}", exc
                )

        # DailyPnLTracker
        try:
            from execution.daily_pnl_tracker import DailyPnLTracker
            ctx.pnl_tracker = DailyPnLTracker(
                db_path=os.getenv("DAILY_PNL_DB", "state/daily_pnl.db")
            )
            logger.info("[WFOrchestrator] DailyPnLTracker init OK")
        except Exception as exc:
            logger.warning("[WFOrchestrator] DailyPnLTracker init failed: {}", exc)

        # HedgeManagers din cfg.hedge_pairs
        hedge_pairs = getattr(cfg, "hedge_pairs", []) or []
        for pair in hedge_pairs:
            try:
                from execution.single_hedge_manager import SingleHedgeManager
                mgr = SingleHedgeManager.from_cfg(pair, ctx)
                ctx.hedge_managers.append(mgr)
                logger.info(
                    "[WFOrchestrator] HedgeManager {} init OK", pair
                )
            except Exception as exc:
                logger.warning(
                    "[WFOrchestrator] HedgeManager {} failed: {}", pair, exc
                )

        return ctx

    # ------------------------------------------------------------------
    # Build runner (MultiMarketRunner sau BybitLiveRunner fallback)
    # ------------------------------------------------------------------

    def _build_runner(self, ctx: StartupContext):
        """
        Decide ce runner sa foloseasca:
          - MultiMarketRunner: daca enable_spot sau enable_margin sau
            exista hedge_managers sau capital_allocator
          - BybitLiveRunner:  fallback clasic (o singura piata)
        """
        cfg = self._runner_cfg
        use_multi = (
            getattr(cfg, "enable_spot", False)
            or getattr(cfg, "enable_margin", False)
            or len(ctx.hedge_managers) > 0
            or getattr(cfg, "force_multi_market", False)
        )

        if use_multi:
            try:
                from execution.multi_market_runner import MultiMarketRunner
                runner = MultiMarketRunner.from_startup_context(
                    ctx=ctx,
                    runner_cfg=cfg,
                    notifier_bus=self._bus,
                    futures_runner=ctx.futures_runner,
                    spot_router=ctx.spot_router,
                    margin_router=ctx.margin_router,
                )
                logger.info(
                    "[WFOrchestrator] Runner: MultiMarketRunner v2.0 "
                    "(Futures={} Spot={} Margin={} Hedges={})",
                    getattr(cfg, "enable_futures", True),
                    getattr(cfg, "enable_spot", False),
                    getattr(cfg, "enable_margin", False),
                    len(ctx.hedge_managers),
                )
                return runner
            except ImportError as exc:
                logger.warning(
                    "[WFOrchestrator] MultiMarketRunner import failed ({}), "
                    "fallback la BybitLiveRunner", exc
                )

        # Fallback: BybitLiveRunner direct
        logger.info("[WFOrchestrator] Runner: BybitLiveRunner (single-market)")
        return ctx.futures_runner

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_runner(self) -> None:
        """
        Entry point principal. Construieste contextul, runner-ul
        si porneste totul.
        """
        cfg = self._runner_cfg
        logger.info(
            "[WFOrchestrator] v{} pornind runner_cfg={}",
            self.VERSION, type(cfg).__name__,
        )

        # Build context
        self._ctx = self._build_context()

        # Build runner
        self._runner = self._build_runner(self._ctx)

        if self._runner is None:
            raise RuntimeError(
                "[WFOrchestrator] Niciun runner disponibil. "
                "Verifica BYBIT_API_KEY si configuratia."
            )

        self._started = True
        markets = []
        if getattr(cfg, "enable_futures", True):
            markets.append("Futures")
        if getattr(cfg, "enable_spot", False):
            markets.append("Spot")
        if getattr(cfg, "enable_margin", False):
            markets.append("Margin")
        if not markets:
            markets = ["Futures"]

        await self._alert(
            f"\U0001f7e2 *QuantLuna pornit* (WFOrchestrator v{self.VERSION})\n"
            f"  Piete active: `{'` + `'.join(markets)}`\n"
            f"  Hedges: `{len(self._ctx.hedge_managers)}`\n"
            f"  PnL DB: `{os.getenv('DAILY_PNL_DB', 'state/daily_pnl.db')}`"
        )

        try:
            await self._runner.start()
        except Exception as exc:
            logger.error("[WFOrchestrator] Runner s-a oprit cu eroare: {}", exc)
            await self._alert(
                f"\u274c *QuantLuna OPRIT* cu eroare:\n`{exc}`"
            )
            raise
        finally:
            self._started = False

    def stop_runner(self) -> None:
        """Opreste runner-ul activ."""
        self._started = False
        if self._runner is not None:
            try:
                self._runner.stop()
                logger.info("[WFOrchestrator] stop_runner() OK")
            except Exception as exc:
                logger.warning("[WFOrchestrator] stop_runner error: {}", exc)

    @property
    def context(self) -> Optional[StartupContext]:
        """Returneaza contextul curent (util pentru API/dashboard)."""
        return self._ctx

    @property
    def is_running(self) -> bool:
        return self._started

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(msg, level="info")
        except Exception:
            pass

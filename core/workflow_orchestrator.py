"""
core/workflow_orchestrator.py  -  QuantLuna WorkflowOrchestrator v2.1

Sprint S42 (2026-07-12): AutoReoptimizer integrat ca task autonom
  - Porneste AutoReoptimizer odata cu botul (in asyncio.gather)
  - Inregistreaza toate serviciile in api/services.py registru
  - Opreste AutoReoptimizer la stop_runner()
  - Expune ctx.auto_reoptimizer pentru API /api/optimizer/status

Patch fata de v2.0:
  - _build_auto_reoptimizer() : construieste AutoReoptimizer din context
  - start_runner() : gather adauga reoptimizer.run_loop()
  - _register_all_services() : inregistreaza in api.services._SERVICES
  - stop_runner() : apeleaza reoptimizer.stop()
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class StartupContext:
    runner_cfg: Any
    notifier_bus: Any = None
    futures_runner: Any = None
    spot_router: Any = None
    margin_router: Any = None
    hedge_managers: List[Any] = field(default_factory=list)
    optimizer: Any = None
    pnl_tracker: Any = None
    capital_allocator: Any = None
    auto_reoptimizer: Any = None      # NOU v2.1
    state_bus: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


class WorkflowOrchestrator:
    """
    Orchestratorul principal QuantLuna v2.1.
    Adauga AutoReoptimizer ca task autonom integrat in gather().
    """

    VERSION = "2.1.0"

    def __init__(
        self,
        runner_cfg,
        notifier_bus=None,
        state_bus=None,
    ) -> None:
        self._runner_cfg = runner_cfg
        self._bus = notifier_bus
        self._state_bus = state_bus
        self._runner = None
        self._reoptimizer = None
        self._ctx: Optional[StartupContext] = None
        self._started = False
        self._tasks: List[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Build context
    # ------------------------------------------------------------------

    def _build_context(self) -> StartupContext:
        cfg = self._runner_cfg
        ctx = StartupContext(
            runner_cfg=cfg,
            notifier_bus=self._bus,
            state_bus=self._state_bus,
        )

        # BybitLiveRunner
        try:
            from execution.bybit_live_runner import BybitLiveRunner
            ctx.futures_runner = BybitLiveRunner.from_env(cfg)
            logger.info("[WFOrch v2.1] BybitLiveRunner OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.1] BybitLiveRunner failed: {}", exc)

        # SpotOrderRouter
        if getattr(cfg, "enable_spot", False):
            try:
                from execution.spot_order_router import SpotOrderRouter
                ctx.spot_router = SpotOrderRouter.from_env()
                logger.info("[WFOrch v2.1] SpotOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.1] SpotOrderRouter failed: {}", exc)

        # MarginOrderRouter
        if getattr(cfg, "enable_margin", False):
            try:
                from execution.margin_order_router import MarginOrderRouter
                ctx.margin_router = MarginOrderRouter.from_env(
                    margin_mode=getattr(cfg, "margin_mode", "cross")
                )
                logger.info("[WFOrch v2.1] MarginOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.1] MarginOrderRouter failed: {}", exc)

        # DailyPnLTracker
        try:
            from execution.daily_pnl_tracker import DailyPnLTracker
            ctx.pnl_tracker = DailyPnLTracker(
                db_path=os.getenv("DAILY_PNL_DB", "state/daily_pnl.db")
            )
            logger.info("[WFOrch v2.1] DailyPnLTracker OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.1] DailyPnLTracker failed: {}", exc)

        # HedgeManagers
        for pair in getattr(cfg, "hedge_pairs", []) or []:
            try:
                from execution.single_hedge_manager import SingleHedgeManager
                ctx.hedge_managers.append(
                    SingleHedgeManager.from_cfg(pair, ctx)
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.1] HedgeManager {} failed: {}", pair, exc)

        # AutoReoptimizer  <-- NOU v2.1
        ctx.auto_reoptimizer = self._build_auto_reoptimizer(ctx)

        return ctx

    def _build_auto_reoptimizer(self, ctx: StartupContext) -> Optional[Any]:
        """
        Construieste AutoReoptimizer daca exista backtest engine.
        Graceful fail daca engine nu e disponibil.
        """
        if not getattr(self._runner_cfg, "enable_reoptimizer", True):
            logger.info("[WFOrch v2.1] AutoReoptimizer dezactivat (enable_reoptimizer=False)")
            return None
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
            # Detecteaza engine-ul backtest disponibil
            engine = self._get_backtest_engine()
            if engine is None:
                logger.warning(
                    "[WFOrch v2.1] AutoReoptimizer: backtest engine indisponibil, skip"
                )
                return None

            pairs = self._get_active_pairs()
            reopt = AutoReoptimizer.from_env(
                engine=engine,
                pairs=pairs,
                notifier_bus=self._bus,
            )
            logger.info(
                "[WFOrch v2.1] AutoReoptimizer init OK | {} perechi | "
                "schedule: weekday={} hour={}:00 UTC",
                len(pairs), reopt._weekday, reopt._hour,
            )
            return reopt
        except Exception as exc:
            logger.warning(
                "[WFOrch v2.1] AutoReoptimizer init failed (non-fatal): {}", exc
            )
            return None

    def _get_backtest_engine(self) -> Optional[Any]:
        """Returneaza backtest engine. Incearca adapters in ordine."""
        for cls_path in [
            ("backtest.engine_adapter", "BacktestEngineAdapter"),
            ("backtest.engine", "BacktestEngine"),
        ]:
            try:
                mod = __import__(cls_path[0], fromlist=[cls_path[1]])
                cls = getattr(mod, cls_path[1])
                return cls.from_env() if hasattr(cls, "from_env") else cls()
            except Exception:
                continue
        return None

    def _get_active_pairs(self) -> List[str]:
        """Detecteaza perechile active din cfg."""
        cfg = self._runner_cfg
        pairs = getattr(cfg, "pairs", None) or \
                getattr(cfg, "symbol_pairs", None) or \
                getattr(cfg, "hedge_pairs", None) or []
        if isinstance(pairs, str):
            pairs = [pairs]
        return list(pairs) if pairs else ["BTCUSDT-ETHUSDT"]

    # ------------------------------------------------------------------
    # Build runner
    # ------------------------------------------------------------------

    def _build_runner(self, ctx: StartupContext) -> Any:
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
                    ctx=ctx, runner_cfg=cfg,
                    notifier_bus=self._bus,
                    futures_runner=ctx.futures_runner,
                    spot_router=ctx.spot_router,
                    margin_router=ctx.margin_router,
                )
                logger.info("[WFOrch v2.1] Runner: MultiMarketRunner")
                return runner
            except ImportError as exc:
                logger.warning(
                    "[WFOrch v2.1] MultiMarketRunner fallback: {}", exc
                )
        logger.info("[WFOrch v2.1] Runner: BybitLiveRunner (single)")
        return ctx.futures_runner

    # ------------------------------------------------------------------
    # Services registration
    # ------------------------------------------------------------------

    def _register_all_services(self, ctx: StartupContext) -> None:
        """Inregistreaza toate componentele in api/services registru."""
        try:
            from api.services import register_service
        except ImportError:
            logger.warning("[WFOrch v2.1] api.services indisponibil, skip register")
            return

        cfg = self._runner_cfg

        register_service(
            name="futures_runner",
            display_name="Futures Runner",
            description="BybitLiveRunner - tranzactionare Linear Futures",
            component=ctx.futures_runner,
            enabled=ctx.futures_runner is not None,
            can_toggle=True,
        )
        register_service(
            name="spot_runner",
            display_name="Spot Runner",
            description="SpotOrderRouter - tranzactionare Spot + DCA",
            component=ctx.spot_router,
            enabled=ctx.spot_router is not None,
            can_toggle=True,
        )
        if ctx.margin_router:
            register_service(
                name="margin_guard",
                display_name="Margin Risk Guard",
                description="Monitorizeaza margin ratio, auto-deleverage < 1.1",
                component=None,  # MarginRiskGuard e in MultiMarketRunner
                enabled=getattr(cfg, "enable_margin", False),
                can_toggle=True,
            )
        if ctx.capital_allocator:
            register_service(
                name="capital_allocator",
                display_name="Capital Allocator",
                description="Alocare % equity + profit-take zilnic 23:55 UTC",
                component=ctx.capital_allocator,
                enabled=True,
                can_toggle=True,
            )
        register_service(
            name="withdrawal_guard",
            display_name="Withdrawal Guard",
            description="Retrageri externe cu confirmare Telegram obligatorie",
            component=None,
            enabled=True,
            can_toggle=False,   # nu se poate opri din dashboard pentru siguranta
        )
        if ctx.auto_reoptimizer:
            register_service(
                name="auto_reoptimizer",
                display_name="Auto Reoptimizer",
                description=(
                    f"Grid search WFO saptamanal - "
                    f"{['Lun','Mar','Mie','Joi','Vin','Sam','Dum'][ctx.auto_reoptimizer._weekday]} "
                    f"{ctx.auto_reoptimizer._hour:02d}:00 UTC"
                ),
                component=ctx.auto_reoptimizer,
                enabled=True,
                can_toggle=True,
            )
        for mgr in ctx.hedge_managers:
            sym = getattr(mgr, "_symbol", getattr(mgr, "symbol", "?"))
            register_service(
                name=f"hedge_{sym.lower()}",
                display_name=f"Hedge {sym}",
                description=f"SingleHedgeManager pentru perechea {sym}",
                component=mgr,
                enabled=True,
                can_toggle=True,
            )

        # Injecteaza reoptimizer in api.optimizer state
        try:
            from api.optimizer import set_optimizer_state
            set_optimizer_state({
                "auto_reoptimizer": ctx.auto_reoptimizer,
                "pairs": self._get_active_pairs(),
            })
        except ImportError:
            pass

        logger.info(
            "[WFOrch v2.1] {} servicii inregistrate",
            len([s for s in [ctx.futures_runner, ctx.spot_router,
                             ctx.capital_allocator, ctx.auto_reoptimizer]
                 if s is not None])
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_runner(self) -> None:
        logger.info("[WFOrch v2.1] START")
        self._ctx = self._build_context()
        self._runner = self._build_runner(self._ctx)
        self._reoptimizer = self._ctx.auto_reoptimizer

        if self._runner is None:
            raise RuntimeError("Niciun runner disponibil. Verifica BYBIT_API_KEY.")

        self._register_all_services(self._ctx)
        self._started = True

        markets = []
        cfg = self._runner_cfg
        if getattr(cfg, "enable_futures", True):  markets.append("Futures")
        if getattr(cfg, "enable_spot", False):    markets.append("Spot")
        if getattr(cfg, "enable_margin", False):  markets.append("Margin")
        if not markets: markets = ["Futures"]

        reopt_info = (
            f"Reoptimizer: "
            f"{['Lun','Mar','Mie','Joi','Vin','Sam','Dum'][self._reoptimizer._weekday]} "
            f"{self._reoptimizer._hour:02d}:00 UTC"
            if self._reoptimizer else "Reoptimizer: OFF"
        )

        await self._alert(
            f"\U0001f7e2 *QuantLuna v{self.VERSION} pornit*\n"
            f"  Piete: `{'` + `'.join(markets)}`\n"
            f"  Hedges: `{len(self._ctx.hedge_managers)}`\n"
            f"  {reopt_info}\n"
            f"  Dashboard: `http://localhost:3000`"
        )

        # Construieste lista de coroutine pentru gather
        coros = [self._runner.start()]

        # AutoReoptimizer ca task autonom paralel
        if self._reoptimizer is not None:
            coros.append(self._reoptimizer.run_loop())
            logger.info("[WFOrch v2.1] AutoReoptimizer adaugat in gather()")

        self._tasks = [asyncio.create_task(c) for c in coros]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("[WFOrch v2.1] gather() cancelled")
        except Exception as exc:
            logger.error("[WFOrch v2.1] Eroare: {}", exc)
            await self._alert(f"\u274c *QuantLuna EROARE*: `{exc}`")
            raise
        finally:
            self._started = False

    def stop_runner(self) -> None:
        self._started = False
        if self._runner is not None:
            try: self._runner.stop()
            except Exception: pass
        if self._reoptimizer is not None:
            try: self._reoptimizer.stop()
            except Exception: pass
        for t in self._tasks:
            if not t.done(): t.cancel()
        logger.info("[WFOrch v2.1] stop_runner() OK")

    @property
    def context(self) -> Optional[StartupContext]:
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

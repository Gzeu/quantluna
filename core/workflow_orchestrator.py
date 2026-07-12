"""
core/workflow_orchestrator.py  -  QuantLuna WorkflowOrchestrator v2.3

Sprint S44c (2026-07-12): PositionScanner + AdoptionEngine integrate la startup

Patch fata de v2.2:
  - _adopt_open_positions()   : nou — apelat in start_runner() inainte de gather()
      Pas 1: PositionScanner.from_env(cfg).scan()        -> ScanReport
      Pas 2: AdoptionEngine.process_report(scan_report)  -> List[AdoptionResult]
               ADOPT       : inregistreaza in OrderManager + plaseaza TP/SL
               CLOSE_NOW   : inchide imediat (liq iminenta / loss depasit)
               MONITOR_ONLY: urmareste fara TP/SL (notional prea mic)
      Pas 3: NativeSlTp.rearm_if_missing(adopted, router) -> re-armeaza SL/TP
               native pe exchange pentru pozitii adoptate fara protectie
  - on_cycle_restart callback : cand un TP/SL adoptat se umple, ciclul restarteaza
  - StartupContext.adoption_results : camp nou
  - VERSION 2.2.0 -> 2.3.0
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class StartupContext:
    runner_cfg:        Any
    notifier_bus:      Any                = None
    futures_runner:    Any                = None
    spot_router:       Any                = None
    margin_router:     Any                = None
    hedge_managers:    List[Any]          = field(default_factory=list)
    optimizer:         Any                = None
    pnl_tracker:       Any                = None
    capital_allocator: Any                = None
    auto_reoptimizer:  Any                = None
    watchdog:          Any                = None
    state_bus:         Any                = None
    adoption_results:  List[Any]          = field(default_factory=list)  # NOU v2.3
    extra:             Dict[str, Any]     = field(default_factory=dict)


class WorkflowOrchestrator:
    """
    Orchestratorul principal QuantLuna v2.3.

    Adauga adoptarea pozitiilor deschise la startup:
        _adopt_open_positions() este apelat in start_runner() INAINTE de gather().

    Arhitectura gather (neschimbata fata de v2.2):
        asyncio.gather(
            runner.start(),              # trading loop principal
            reoptimizer.run_loop(),      # grid search WFO saptamanal
            watchdog.run_loop(),         # monitoring continuu 60s
        )
    """

    VERSION = "2.3.0"

    def __init__(
        self,
        runner_cfg,
        notifier_bus=None,
        state_bus=None,
        dispatcher=None,
    ) -> None:
        self._runner_cfg  = runner_cfg
        self._bus         = notifier_bus
        self._state_bus   = state_bus
        self._dispatcher  = dispatcher
        self._runner      = None
        self._reoptimizer = None
        self._watchdog    = None
        self._ctx:        Optional[StartupContext] = None
        self._started     = False
        self._tasks:      List[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, dispatcher=None) -> "WorkflowOrchestrator":
        try:
            from execution.runner_config import RunnerConfig
            cfg = RunnerConfig.from_env()
        except Exception:
            import types
            cfg = types.SimpleNamespace(
                pairs=os.getenv("PAIRS", "BTCUSDT-ETHUSDT").split(","),
                hedge_pairs=os.getenv("HEDGE_PAIRS", "").split(",") if os.getenv("HEDGE_PAIRS") else [],
                enable_spot=os.getenv("ENABLE_SPOT", "false").lower() == "true",
                enable_margin=os.getenv("ENABLE_MARGIN", "false").lower() == "true",
                enable_reoptimizer=os.getenv("ENABLE_REOPTIMIZER", "true").lower() == "true",
                enable_watchdog=os.getenv("WATCHDOG_ENABLED", "true").lower() == "true",
            )
        return cls(runner_cfg=cfg, dispatcher=dispatcher)

    async def build_context(self) -> StartupContext:
        """Construieste context fara a porni runnerul. Util in api/main.py lifespan."""
        if self._ctx is None:
            self._ctx = self._build_context()
        return self._ctx

    @property
    def pairs(self) -> List[str]:
        return self._get_active_pairs()

    @property
    def reoptimizer(self) -> Optional[Any]:
        return self._reoptimizer or (self._ctx.auto_reoptimizer if self._ctx else None)

    @property
    def watchdog(self) -> Optional[Any]:
        return self._watchdog or (self._ctx.watchdog if self._ctx else None)

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
            logger.info("[WFOrch v2.3] BybitLiveRunner OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.3] BybitLiveRunner failed: {}", exc)

        # SpotOrderRouter
        if getattr(cfg, "enable_spot", False):
            try:
                from execution.spot_order_router import SpotOrderRouter
                ctx.spot_router = SpotOrderRouter.from_env()
                logger.info("[WFOrch v2.3] SpotOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.3] SpotOrderRouter failed: {}", exc)

        # MarginOrderRouter
        if getattr(cfg, "enable_margin", False):
            try:
                from execution.margin_order_router import MarginOrderRouter
                ctx.margin_router = MarginOrderRouter.from_env(
                    margin_mode=getattr(cfg, "margin_mode", "cross")
                )
                logger.info("[WFOrch v2.3] MarginOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.3] MarginOrderRouter failed: {}", exc)

        # DailyPnLTracker
        try:
            from execution.daily_pnl_tracker import DailyPnLTracker
            ctx.pnl_tracker = DailyPnLTracker(
                db_path=os.getenv("DAILY_PNL_DB", "state/daily_pnl.db")
            )
            logger.info("[WFOrch v2.3] DailyPnLTracker OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.3] DailyPnLTracker failed: {}", exc)

        # HedgeManagers
        for pair in getattr(cfg, "hedge_pairs", []) or []:
            try:
                from execution.single_hedge_manager import SingleHedgeManager
                ctx.hedge_managers.append(
                    SingleHedgeManager.from_cfg(pair, ctx)
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.3] HedgeManager {} failed: {}", pair, exc)

        # AutoReoptimizer
        ctx.auto_reoptimizer = self._build_auto_reoptimizer(ctx)

        # MonitoringWatchdog
        ctx.watchdog = self._build_watchdog(ctx)

        return ctx

    def _build_auto_reoptimizer(self, ctx: StartupContext) -> Optional[Any]:
        if not getattr(self._runner_cfg, "enable_reoptimizer", True):
            logger.info("[WFOrch v2.3] AutoReoptimizer dezactivat")
            return None
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
            engine = self._get_backtest_engine()
            if engine is None:
                logger.warning("[WFOrch v2.3] AutoReoptimizer: engine indisponibil")
                return None
            pairs = self._get_active_pairs()
            reopt = AutoReoptimizer.from_env(
                engine=engine, pairs=pairs, notifier_bus=self._bus,
            )
            logger.info("[WFOrch v2.3] AutoReoptimizer OK | {} perechi", len(pairs))
            return reopt
        except Exception as exc:
            logger.warning("[WFOrch v2.3] AutoReoptimizer init failed: {}", exc)
            return None

    def _build_watchdog(self, ctx: StartupContext) -> Optional[Any]:
        if not getattr(self._runner_cfg, "enable_watchdog", True):
            logger.info("[WFOrch v2.3] MonitoringWatchdog dezactivat (enable_watchdog=False)")
            return None
        try:
            from core.monitoring_watchdog import MonitoringWatchdog
            pairs = self._get_active_pairs()

            async def _metrics_provider(pair: str) -> dict:
                try:
                    from api.risk import get_live_metrics
                    return await get_live_metrics(pair)
                except Exception:
                    pass
                if ctx.pnl_tracker is not None:
                    try:
                        snap = ctx.pnl_tracker.snapshot(pair)
                        return {
                            "sharpe":      snap.get("sharpe_24h", 99.0),
                            "drawdown":    snap.get("max_drawdown", 0.0),
                            "z_score":     snap.get("z_score", 0.0),
                            "half_life":   snap.get("half_life_h", 0.0),
                            "loss_streak": snap.get("loss_streak", 0),
                        }
                    except Exception:
                        pass
                return {
                    "sharpe": 99.0, "drawdown": 0.0,
                    "z_score": 0.0, "half_life": 0.0,
                    "loss_streak": 0,
                }

            async def _halt_callback(pair: str) -> None:
                try:
                    from api.pairs import halt_pair
                    await halt_pair(pair, reason="watchdog_dd_breach")
                    logger.warning("[WFOrch v2.3] HALT executat: {}", pair)
                except Exception as exc:
                    logger.error("[WFOrch v2.3] halt_callback esuat {}: {}", pair, exc)

            async def _reduce_callback(pair: str, factor: float) -> None:
                try:
                    from api.sizing import reduce_pair_size
                    await reduce_pair_size(pair, factor)
                    logger.info("[WFOrch v2.3] REDUCE_SIZE {}x: {}", factor, pair)
                except Exception as exc:
                    logger.error("[WFOrch v2.3] reduce_callback esuat {}: {}", pair, exc)

            dispatcher = self._dispatcher or self._bus
            wd = MonitoringWatchdog.from_env(
                pairs=pairs,
                metrics_provider=_metrics_provider,
                dispatcher=dispatcher,
                halt_callback=_halt_callback,
                reduce_callback=_reduce_callback,
            )
            logger.info(
                "[WFOrch v2.3] MonitoringWatchdog OK | {} perechi | interval={}s",
                len(pairs), wd._check_interval,
            )
            return wd
        except Exception as exc:
            logger.warning("[WFOrch v2.3] MonitoringWatchdog init failed (non-fatal): {}", exc)
            return None

    def _get_backtest_engine(self) -> Optional[Any]:
        for cls_path in [
            ("backtest.engine_adapter", "BacktestEngineAdapter"),
            ("backtest.engine",         "BacktestEngine"),
        ]:
            try:
                mod = __import__(cls_path[0], fromlist=[cls_path[1]])
                cls = getattr(mod, cls_path[1])
                return cls.from_env() if hasattr(cls, "from_env") else cls()
            except Exception:
                continue
        return None

    def _get_active_pairs(self) -> List[str]:
        cfg = self._runner_cfg
        pairs = (
            getattr(cfg, "pairs", None)
            or getattr(cfg, "symbol_pairs", None)
            or getattr(cfg, "hedge_pairs", None)
            or []
        )
        if isinstance(pairs, str):
            pairs = [p.strip() for p in pairs.split(",")]
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
                logger.info("[WFOrch v2.3] Runner: MultiMarketRunner")
                return runner
            except ImportError as exc:
                logger.warning("[WFOrch v2.3] MultiMarketRunner fallback: {}", exc)
        logger.info("[WFOrch v2.3] Runner: BybitLiveRunner (single)")
        return ctx.futures_runner

    # ------------------------------------------------------------------
    # Services registration
    # ------------------------------------------------------------------

    def _register_all_services(self, ctx: StartupContext) -> None:
        try:
            from api.services import register_service
        except ImportError:
            logger.warning("[WFOrch v2.3] api.services indisponibil")
            return

        cfg = self._runner_cfg

        register_service(
            name="futures_runner", display_name="Futures Runner",
            description="BybitLiveRunner - tranzactionare Linear Futures",
            component=ctx.futures_runner,
            enabled=ctx.futures_runner is not None, can_toggle=True,
        )
        register_service(
            name="spot_runner", display_name="Spot Runner",
            description="SpotOrderRouter - tranzactionare Spot + DCA",
            component=ctx.spot_router,
            enabled=ctx.spot_router is not None, can_toggle=True,
        )
        if ctx.margin_router:
            register_service(
                name="margin_guard", display_name="Margin Risk Guard",
                description="Monitorizeaza margin ratio, auto-deleverage < 1.1",
                component=None,
                enabled=getattr(cfg, "enable_margin", False), can_toggle=True,
            )
        if ctx.capital_allocator:
            register_service(
                name="capital_allocator", display_name="Capital Allocator",
                description="Alocare % equity + profit-take zilnic 23:55 UTC",
                component=ctx.capital_allocator,
                enabled=True, can_toggle=True,
            )
        register_service(
            name="withdrawal_guard", display_name="Withdrawal Guard",
            description="Retrageri externe cu confirmare Telegram obligatorie",
            component=None, enabled=True, can_toggle=False,
        )
        if ctx.auto_reoptimizer:
            register_service(
                name="auto_reoptimizer", display_name="Auto Reoptimizer",
                description=(
                    f"Grid search WFO saptamanal - "
                    f"{['Lun','Mar','Mie','Joi','Vin','Sam','Dum'][ctx.auto_reoptimizer._weekday]} "
                    f"{ctx.auto_reoptimizer._hour:02d}:00 UTC"
                ),
                component=ctx.auto_reoptimizer,
                enabled=True, can_toggle=True,
            )
        for mgr in ctx.hedge_managers:
            sym = getattr(mgr, "_symbol", getattr(mgr, "symbol", "?"))
            register_service(
                name=f"hedge_{sym.lower()}", display_name=f"Hedge {sym}",
                description=f"SingleHedgeManager pentru perechea {sym}",
                component=mgr, enabled=True, can_toggle=True,
            )
        if ctx.watchdog:
            register_service(
                name="monitoring_watchdog", display_name="Monitoring Watchdog",
                description=(
                    f"Monitoring continuu {ctx.watchdog._check_interval}s: "
                    "Sharpe, DD, z-score, half-life, loss streak"
                ),
                component=ctx.watchdog,
                enabled=True, can_toggle=True,
            )

        try:
            from api.optimizer import set_optimizer_state
            set_optimizer_state({
                "auto_reoptimizer": ctx.auto_reoptimizer,
                "pairs":            self._get_active_pairs(),
            })
        except ImportError:
            pass

        try:
            from api.watchdog import set_watchdog_state
            set_watchdog_state({
                "watchdog":   ctx.watchdog,
                "dispatcher": self._dispatcher or self._bus,
            })
        except ImportError:
            pass

        running_count = sum(
            1 for s in [
                ctx.futures_runner, ctx.spot_router,
                ctx.capital_allocator, ctx.auto_reoptimizer, ctx.watchdog,
            ] if s is not None
        )
        logger.info("[WFOrch v2.3] {} servicii inregistrate", running_count)

    # ------------------------------------------------------------------
    # NOU v2.3 — Adoptare pozitii deschise la startup
    # ------------------------------------------------------------------

    async def _adopt_open_positions(self, ctx: StartupContext) -> None:
        """
        Preia pozitiile deschise de pe exchange la startup si le adopta in
        OrderManager cu TP/SL protectie imediata.

        Flux:
            1. PositionScanner.from_env(cfg).scan()         -> ScanReport
            2. AdoptionEngine.process_report(scan_report)   -> List[AdoptionResult]
               ADOPT:        inregistreaza in OM + plaseaza TP/SL reduce-only
               CLOSE_NOW:    inchide imediat (liq iminenta sau loss > threshold)
               MONITOR_ONLY: urmareste fara TP/SL (notional prea mic)
            3. NativeSlTp.rearm_if_missing(adopted_results, order_router)
               re-armeaza SL/TP native pe exchange daca lipsesc

        Non-fatal: orice eroare este WARNING-logata; botul porneste normal.
        """
        cfg = self._runner_cfg

        # --- Pas 1: Scan pozitii live de pe exchange ---
        try:
            from execution.position_scanner import PositionScanner
            scanner = PositionScanner.from_env(cfg)
            scan_report = await scanner.scan()
        except Exception as exc:
            logger.warning(
                "[WFOrch v2.3] PositionScanner failed (non-fatal) — "
                "bot porneste fara adoptare pozitii: {}", exc
            )
            return

        total   = len(scan_report.orphans)
        managed = len(scan_report.managed) if hasattr(scan_report, "managed") else 0
        logger.info(
            "[WFOrch v2.3] PositionScanner: {} orphane + {} managed pe exchange",
            total, managed,
        )

        if total == 0:
            logger.info("[WFOrch v2.3] Nicio pozitie orfana — adoptare nu e necesara")
            return

        # --- Pas 2: Construieste AdoptionEngine si proceseaza raportul ---
        try:
            from execution.adoption_engine import AdoptionEngine, AdoptionConfig, AdoptionDecision
            from execution.order_manager   import OrderManager, OrderManagerConfig
            from execution.checkpoint      import Checkpoint

            # OrderManager necesar pentru plasarea TP/SL prin adopt_and_protect
            order_manager = OrderManager(OrderManagerConfig(
                base_qty=getattr(cfg, "base_qty", 0.01),
                entry_zscore=getattr(cfg, "entry_zscore", 2.0),
                exit_zscore=getattr(cfg, "exit_zscore", 0.5),
                dry_run=getattr(cfg, "dry_run", False),
            ))

            # Checkpoint pentru persistenta pozitiei adoptate
            checkpoint_path = os.getenv(
                "CHECKPOINT_PATH",
                getattr(cfg, "checkpoint_path", "state/checkpoint.json"),
            )
            checkpoint = Checkpoint(path=checkpoint_path)

            # Obtinem exchange-ul din runner pentru operatii de urgenta
            exchange = (
                getattr(ctx.futures_runner, "_exchange", None)
                or getattr(ctx.futures_runner, "exchange",  None)
            )

            # on_cycle_restart: cand un TP/SL adoptat se executa, restarteaza ciclul
            async def _on_cycle_restart(symbol: str) -> None:
                logger.info(
                    "[WFOrch v2.3] on_cycle_restart pentru {} — "
                    "SpreadMonitor reset + DecisionEngine deblocat", symbol
                )
                try:
                    runner = ctx.futures_runner
                    if runner is not None and hasattr(runner, "reset_cycle"):
                        await runner.reset_cycle(symbol)
                    elif runner is not None and hasattr(runner, "_spread_monitor"):
                        runner._spread_monitor.reset()
                        logger.info(
                            "[WFOrch v2.3] SpreadMonitor.reset() executat pentru {}", symbol
                        )
                except Exception as exc:
                    logger.warning(
                        "[WFOrch v2.3] reset_cycle failed pentru {} (non-fatal): {}",
                        symbol, exc,
                    )

            adoption_cfg = AdoptionConfig(
                close_loss_pct=float(os.getenv("ADOPT_CLOSE_LOSS_PCT",   "-0.05")),
                min_liq_distance_pct=float(os.getenv("ADOPT_MIN_LIQ_PCT",  "0.08")),
                min_notional_adopt=float(os.getenv("ADOPT_MIN_NOTIONAL",   "5.0")),
                tp_target_pct=float(os.getenv("ADOPT_TP_PCT",              "0.04")),
                sl_max_loss_pct=float(os.getenv("ADOPT_SL_PCT",            "0.03")),
                trailing_pct=float(os.getenv("ADOPT_TRAILING_PCT",         "0.015")),
                restart_cooldown_s=float(os.getenv("ADOPT_RESTART_COOLDOWN", "10.0")),
            )

            engine = AdoptionEngine(
                exchange=exchange,
                checkpoint=checkpoint,
                order_manager=order_manager,
                config=adoption_cfg,
                on_cycle_restart=_on_cycle_restart,
            )

            results = await engine.process_report(scan_report)
            ctx.adoption_results = results

        except Exception as exc:
            logger.warning(
                "[WFOrch v2.3] AdoptionEngine failed (non-fatal) — "
                "bot porneste fara protectie pozitii: {}", exc
            )
            return

        # Log sumar adoptare
        adopted      = [r for r in results if r.decision == AdoptionDecision.ADOPT]
        closed_now   = [r for r in results if r.decision == AdoptionDecision.CLOSE_NOW]
        monitor_only = [r for r in results if r.decision == AdoptionDecision.MONITOR_ONLY]

        logger.info(
            "[WFOrch v2.3] Adoptare finalizata: ADOPT={} CLOSE_NOW={} MONITOR_ONLY={}",
            len(adopted), len(closed_now), len(monitor_only),
        )
        for r in adopted:
            logger.info(
                "[WFOrch v2.3]   ADOPT   {} {} qty={} tp={} sl={}",
                r.position.symbol, r.position.side,
                r.position.qty,
                f"{r.tp_price:.4f}" if r.tp_price else "N/A",
                f"{r.sl_price:.4f}" if r.sl_price else "N/A",
            )
        for r in closed_now:
            logger.warning(
                "[WFOrch v2.3]   CLOSE_NOW {} {} qty={} motiv={}",
                r.position.symbol, r.position.side, r.position.qty, r.reason,
            )
        for r in monitor_only:
            logger.info(
                "[WFOrch v2.3]   MONITOR_ONLY {} {} notional={:.2f}USDT",
                r.position.symbol, r.position.side, r.position.notional_usdt,
            )

        # --- Pas 3: NativeSlTp — re-armeaza SL/TP native pe exchange ---
        if adopted:
            try:
                from execution.native_sl_tp import NativeSlTp
                order_router = (
                    getattr(ctx.futures_runner, "_order_router", None)
                    or getattr(ctx.futures_runner, "order_router",  None)
                )
                if order_router is not None and hasattr(NativeSlTp, "rearm_if_missing"):
                    await NativeSlTp.rearm_if_missing(adopted, order_router)
                    logger.info(
                        "[WFOrch v2.3] NativeSlTp.rearm_if_missing OK pentru {} pozitii",
                        len(adopted),
                    )
                else:
                    logger.info(
                        "[WFOrch v2.3] NativeSlTp.rearm_if_missing indisponibil — "
                        "SL/TP native prin OrderManager sunt suficiente"
                    )
            except Exception as exc:
                logger.warning(
                    "[WFOrch v2.3] NativeSlTp.rearm_if_missing failed (non-fatal): {}", exc
                )

        # Notifica pe Telegram/Slack rezultatul adoptarii
        if (adopted or closed_now) and self._bus:
            try:
                lines = [f"\U0001f4cb *QuantLuna v{self.VERSION} — Adoptare pozitii la startup*"]
                if adopted:
                    lines.append(f"  \u2705 Adoptate: {len(adopted)}")
                    for r in adopted:
                        lines.append(
                            f"    `{r.position.symbol}` {r.position.side} "
                            f"qty={r.position.qty} "
                            f"TP=`{r.tp_price:.4f}` SL=`{r.sl_price:.4f}`"
                        )
                if closed_now:
                    lines.append(f"  \u26a0\ufe0f Inchise imediat: {len(closed_now)}")
                    for r in closed_now:
                        lines.append(
                            f"    `{r.position.symbol}` {r.position.side} "
                            f"— {r.reason}"
                        )
                await self._alert("\n".join(lines))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_runner(self) -> None:
        logger.info("[WFOrch v2.3] START")
        self._ctx         = self._build_context()
        self._runner      = self._build_runner(self._ctx)
        self._reoptimizer = self._ctx.auto_reoptimizer
        self._watchdog    = self._ctx.watchdog

        if self._runner is None:
            raise RuntimeError("Niciun runner disponibil. Verifica BYBIT_API_KEY.")

        self._register_all_services(self._ctx)

        # NOU v2.3 — adopta pozitiile deschise INAINTE de a intra in gather()
        await self._adopt_open_positions(self._ctx)

        self._started = True

        markets = []
        cfg = self._runner_cfg
        if getattr(cfg, "enable_futures", True):  markets.append("Futures")
        if getattr(cfg, "enable_spot",    False):  markets.append("Spot")
        if getattr(cfg, "enable_margin",  False):  markets.append("Margin")
        if not markets: markets = ["Futures"]

        reopt_info = (
            f"Reoptimizer: "
            f"{['Lun','Mar','Mie','Joi','Vin','Sam','Dum'][self._reoptimizer._weekday]} "
            f"{self._reoptimizer._hour:02d}:00 UTC"
            if self._reoptimizer else "Reoptimizer: OFF"
        )
        wd_info = (
            f"Watchdog: activ (interval {self._watchdog._check_interval}s)"
            if self._watchdog else "Watchdog: OFF"
        )
        adopt_info = (
            f"Pozitii adoptate: {len([r for r in self._ctx.adoption_results if hasattr(r, 'decision') and str(r.decision) == 'adopt'])}"
            if self._ctx.adoption_results else "Pozitii adoptate: 0"
        )

        await self._alert(
            f"\U0001f7e2 *QuantLuna v{self.VERSION} pornit*\n"
            f"  Piete: `{'` + `'.join(markets)}`\n"
            f"  Hedges: `{len(self._ctx.hedge_managers)}`\n"
            f"  {reopt_info}\n"
            f"  {wd_info}\n"
            f"  {adopt_info}\n"
            f"  Dashboard: `http://localhost:3000`"
        )

        coros = [self._runner.start()]

        if self._reoptimizer is not None:
            coros.append(self._reoptimizer.run_loop())
            logger.info("[WFOrch v2.3] AutoReoptimizer adaugat in gather()")

        if self._watchdog is not None:
            coros.append(self._watchdog.run_loop())
            logger.info("[WFOrch v2.3] MonitoringWatchdog adaugat in gather()")

        self._tasks = [asyncio.create_task(c) for c in coros]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("[WFOrch v2.3] gather() cancelled")
        except Exception as exc:
            logger.error("[WFOrch v2.3] Eroare fatala: {}", exc)
            await self._alert(f"\u274c *QuantLuna EROARE*: `{exc}`")
            raise
        finally:
            self._started = False

    async def stop_runner(self) -> None:
        self._started = False
        if self._runner is not None:
            try: self._runner.stop()
            except Exception: pass
        if self._reoptimizer is not None:
            try: self._reoptimizer.stop()
            except Exception: pass
        if self._watchdog is not None:
            try: self._watchdog.stop()
            except Exception: pass
        for t in self._tasks:
            if not t.done(): t.cancel()
        await asyncio.sleep(0)
        await self._alert("\U0001f534 *QuantLuna oprit.*")
        logger.info("[WFOrch v2.3] stop_runner() OK")

    @property
    def context(self) -> Optional[StartupContext]:
        return self._ctx

    @property
    def is_running(self) -> bool:
        return self._started

    async def _alert(self, msg: str) -> None:
        if not self._bus and not self._dispatcher:
            return
        bus = self._bus or self._dispatcher
        try:
            if hasattr(bus, "send_alert"):
                await bus.send_alert(msg, level="info")
            elif hasattr(bus, "emit"):
                from notifications.event_types import AlertEvent, EventType
                await bus.emit(AlertEvent(
                    event_type=EventType.SYSTEM_START,
                    payload={"text": msg},
                ))
        except Exception:
            pass

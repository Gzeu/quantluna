"""
core/workflow_orchestrator.py  —  QuantLuna WorkflowOrchestrator v2.4

Sprint S44d (2026-07-12): Wire toate 5 gap-uri critice

1. CapitalAllocator   — construit in _build_context(), run_loop() in gather()
2. PositionReconciler — boot_scan() la startup + reconcile_loop() periodic 60s
3. MarginRiskGuard    — watch_loop() in gather() daca ENABLE_MARGIN=true
4. handle_partial_exit— inregistrat ca hook pe runner / expus in ctx
5. ReportScheduler    — raport zilnic 08:00 UTC in gather()

Patch fata de v2.3 (adoptare pozitii):
  - _build_context()    : + CapitalAllocator + MarginRiskGuard
  - start_runner()      : + boot_scan() inainte de adopt
                          + gather() include toti cei 5 task-uri noi
  - StartupContext      : + capital_allocator, margin_guard, boot_scan_result,
                            partial_exit_handler
  - VERSION 2.3.0 → 2.4.0
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
    runner_cfg:             Any
    notifier_bus:           Any                = None
    futures_runner:         Any                = None
    spot_router:            Any                = None
    margin_router:          Any                = None
    hedge_managers:         List[Any]          = field(default_factory=list)
    optimizer:              Any                = None
    pnl_tracker:            Any                = None
    capital_allocator:      Any                = None   # ← v2.4 wired
    auto_reoptimizer:       Any                = None
    watchdog:               Any                = None
    state_bus:              Any                = None
    margin_guard:           Any                = None   # ← v2.4 wired
    boot_scan_result:       Any                = None   # ← v2.4 wired
    partial_exit_handler:   Any                = None   # ← v2.4 wired
    adoption_results:       List[Any]          = field(default_factory=list)
    extra:                  Dict[str, Any]     = field(default_factory=dict)


# ---------------------------------------------------------------------------
# WorkflowOrchestrator v2.4
# ---------------------------------------------------------------------------

class WorkflowOrchestrator:
    """
    Orchestratorul principal QuantLuna v2.4.

    Arhitectura gather completa:
        asyncio.gather(
            runner.start(),                 # trading loop principal
            reoptimizer.run_loop(),         # WFO saptamanal
            watchdog.run_loop(),            # monitoring 60s
            capital_allocator.run_loop(),   # profit-take zilnic 23:55 UTC  [NOU v2.4]
            margin_guard.watch_loop(),      # margin protection 30s          [NOU v2.4]
            _reconcile_loop(),              # reconciliere pozitii 60s       [NOU v2.4]
            _report_loop(),                 # raport zilnic 08:00 UTC        [NOU v2.4]
        )

    La startup (inainte de gather):
        boot_scan()               → balanta + pozitii Telegram              [NOU v2.4]
        _adopt_open_positions()   → ADOPT/CLOSE_NOW/MONITOR_ONLY + TP/SL   [v2.3]
        _register_partial_exit()  → hook partial exit pe runner              [NOU v2.4]
    """

    VERSION = "2.4.0"

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
            logger.info("[WFOrch v2.4] BybitLiveRunner OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.4] BybitLiveRunner failed: {}", exc)

        # SpotOrderRouter
        if getattr(cfg, "enable_spot", False):
            try:
                from execution.spot_order_router import SpotOrderRouter
                ctx.spot_router = SpotOrderRouter.from_env()
                logger.info("[WFOrch v2.4] SpotOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.4] SpotOrderRouter failed: {}", exc)

        # MarginOrderRouter
        if getattr(cfg, "enable_margin", False):
            try:
                from execution.margin_order_router import MarginOrderRouter
                ctx.margin_router = MarginOrderRouter.from_env(
                    margin_mode=getattr(cfg, "margin_mode", "cross")
                )
                logger.info("[WFOrch v2.4] MarginOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.4] MarginOrderRouter failed: {}", exc)

        # DailyPnLTracker
        try:
            from execution.daily_pnl_tracker import DailyPnLTracker
            ctx.pnl_tracker = DailyPnLTracker(
                db_path=os.getenv("DAILY_PNL_DB", "state/daily_pnl.db")
            )
            logger.info("[WFOrch v2.4] DailyPnLTracker OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.4] DailyPnLTracker failed: {}", exc)

        # ─────────────────────────────────────────────────────────────────
        # GAP 1: CapitalAllocator — construit daca PnLTracker disponibil
        # ─────────────────────────────────────────────────────────────────
        if ctx.pnl_tracker is not None:
            try:
                from execution.capital_allocator import CapitalAllocator
                ctx.capital_allocator = CapitalAllocator.from_env(
                    tracker=ctx.pnl_tracker,
                    notifier_bus=self._bus,
                )
                logger.info(
                    "[WFOrch v2.4] CapitalAllocator OK "
                    "(profit-take zilnic 23:55 UTC, rezerva 10%)"
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.4] CapitalAllocator failed: {}", exc)
        else:
            logger.warning(
                "[WFOrch v2.4] CapitalAllocator SKIP — DailyPnLTracker indisponibil"
            )

        # ─────────────────────────────────────────────────────────────────
        # GAP 3: MarginRiskGuard — construit daca ENABLE_MARGIN=true
        # ─────────────────────────────────────────────────────────────────
        if getattr(cfg, "enable_margin", False) and ctx.margin_router is not None:
            try:
                from execution.margin_risk_guard import MarginRiskGuard, MarginRiskConfig
                ctx.margin_guard = MarginRiskGuard(
                    order_router=ctx.margin_router,
                    notifier_bus=self._bus,
                    cfg=MarginRiskConfig(
                        poll_interval_s=float(os.getenv("MARGIN_GUARD_POLL_S", "30")),
                        danger_threshold=float(os.getenv("MARGIN_DANGER_RATIO", "1.5")),
                        critical_threshold=float(os.getenv("MARGIN_CRITICAL_RATIO", "1.1")),
                        auto_close_on_critical=os.getenv(
                            "MARGIN_AUTO_CLOSE", "true"
                        ).lower() == "true",
                        max_auto_closes_per_session=int(
                            os.getenv("MARGIN_MAX_AUTO_CLOSES", "3")
                        ),
                    ),
                )
                logger.info(
                    "[WFOrch v2.4] MarginRiskGuard OK "
                    "(danger={} critical={} poll={}s)",
                    os.getenv("MARGIN_DANGER_RATIO", "1.5"),
                    os.getenv("MARGIN_CRITICAL_RATIO", "1.1"),
                    os.getenv("MARGIN_GUARD_POLL_S", "30"),
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.4] MarginRiskGuard failed: {}", exc)

        # HedgeManagers
        for pair in getattr(cfg, "hedge_pairs", []) or []:
            try:
                from execution.single_hedge_manager import SingleHedgeManager
                ctx.hedge_managers.append(
                    SingleHedgeManager.from_cfg(pair, ctx)
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.4] HedgeManager {} failed: {}", pair, exc)

        # AutoReoptimizer
        ctx.auto_reoptimizer = self._build_auto_reoptimizer(ctx)

        # MonitoringWatchdog
        ctx.watchdog = self._build_watchdog(ctx)

        return ctx

    def _build_auto_reoptimizer(self, ctx: StartupContext) -> Optional[Any]:
        if not getattr(self._runner_cfg, "enable_reoptimizer", True):
            logger.info("[WFOrch v2.4] AutoReoptimizer dezactivat")
            return None
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
            engine = self._get_backtest_engine()
            if engine is None:
                return None
            reopt = AutoReoptimizer.from_env(
                engine=engine,
                pairs=self._get_active_pairs(),
                notifier_bus=self._bus,
            )
            logger.info("[WFOrch v2.4] AutoReoptimizer OK")
            return reopt
        except Exception as exc:
            logger.warning("[WFOrch v2.4] AutoReoptimizer failed: {}", exc)
            return None

    def _build_watchdog(self, ctx: StartupContext) -> Optional[Any]:
        if not getattr(self._runner_cfg, "enable_watchdog", True):
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
                    "z_score": 0.0, "half_life": 0.0, "loss_streak": 0,
                }

            async def _halt_callback(pair: str) -> None:
                try:
                    from api.pairs import halt_pair
                    await halt_pair(pair, reason="watchdog_dd_breach")
                except Exception as exc:
                    logger.error("[WFOrch v2.4] halt_callback {}: {}", pair, exc)

            async def _reduce_callback(pair: str, factor: float) -> None:
                try:
                    from api.sizing import reduce_pair_size
                    await reduce_pair_size(pair, factor)
                except Exception as exc:
                    logger.error("[WFOrch v2.4] reduce_callback {}: {}", pair, exc)

            wd = MonitoringWatchdog.from_env(
                pairs=pairs,
                metrics_provider=_metrics_provider,
                dispatcher=self._dispatcher or self._bus,
                halt_callback=_halt_callback,
                reduce_callback=_reduce_callback,
            )
            logger.info("[WFOrch v2.4] MonitoringWatchdog OK")
            return wd
        except Exception as exc:
            logger.warning("[WFOrch v2.4] MonitoringWatchdog failed: {}", exc)
            return None

    def _get_backtest_engine(self) -> Optional[Any]:
        for mod_name, cls_name in [
            ("backtest.engine_adapter", "BacktestEngineAdapter"),
            ("backtest.engine",         "BacktestEngine"),
        ]:
            try:
                mod = __import__(mod_name, fromlist=[cls_name])
                cls = getattr(mod, cls_name)
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
                logger.info("[WFOrch v2.4] Runner: MultiMarketRunner")
                return runner
            except ImportError as exc:
                logger.warning("[WFOrch v2.4] MultiMarketRunner fallback: {}", exc)
        logger.info("[WFOrch v2.4] Runner: BybitLiveRunner (single)")
        return ctx.futures_runner

    # ------------------------------------------------------------------
    # Services registration
    # ------------------------------------------------------------------

    def _register_all_services(self, ctx: StartupContext) -> None:
        try:
            from api.services import register_service
        except ImportError:
            logger.warning("[WFOrch v2.4] api.services indisponibil")
            return

        cfg = self._runner_cfg

        register_service(
            name="futures_runner", display_name="Futures Runner",
            description="BybitLiveRunner — Linear Futures trading loop",
            component=ctx.futures_runner,
            enabled=ctx.futures_runner is not None, can_toggle=True,
        )
        register_service(
            name="spot_runner", display_name="Spot Runner",
            description="SpotOrderRouter — Spot + DCA",
            component=ctx.spot_router,
            enabled=ctx.spot_router is not None, can_toggle=True,
        )
        if ctx.margin_router:
            register_service(
                name="margin_guard", display_name="Margin Risk Guard",
                description="Monitorizeaza margin ratio, auto-deleverage < 1.1",
                component=ctx.margin_guard,
                enabled=ctx.margin_guard is not None, can_toggle=True,
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
                description="Grid search WFO saptamanal",
                component=ctx.auto_reoptimizer,
                enabled=True, can_toggle=True,
            )
        for mgr in ctx.hedge_managers:
            sym = getattr(mgr, "_symbol", getattr(mgr, "symbol", "?"))
            register_service(
                name=f"hedge_{sym.lower()}", display_name=f"Hedge {sym}",
                description=f"SingleHedgeManager pentru {sym}",
                component=mgr, enabled=True, can_toggle=True,
            )
        if ctx.watchdog:
            register_service(
                name="monitoring_watchdog", display_name="Monitoring Watchdog",
                description=f"Monitoring continuu {ctx.watchdog._check_interval}s",
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

    # ------------------------------------------------------------------
    # GAP 2: Boot scan complet (PositionReconciler)
    # ------------------------------------------------------------------

    async def _run_boot_scan(self, ctx: StartupContext) -> None:
        """
        Pas 0 (inaintea adoptarii): scaneaza complet contul Bybit.
        - scan_wallet_balance() → equity, available, uPnL total
        - scan_all_positions()  → TOATE pozitiile deschise
        Trimite rezultatul pe Telegram si il salveaza in ctx.boot_scan_result.
        Non-fatal: eroarea e logata, startup continua.
        """
        order_router = (
            getattr(ctx.futures_runner, "_order_router", None)
            or getattr(ctx.futures_runner, "order_router",  None)
            or ctx.margin_router
        )
        if order_router is None:
            logger.warning("[WFOrch v2.4] BootScan SKIP — order_router indisponibil")
            return

        pairs = self._get_active_pairs()
        pair_str = pairs[0] if pairs else "BTCUSDT-ETHUSDT"
        parts = pair_str.replace("/", "-").split("-")
        sym_y = parts[0] if len(parts) >= 1 else "BTCUSDT"
        sym_x = parts[1] if len(parts) >= 2 else "ETHUSDT"

        try:
            from execution.position_reconciler import PositionReconciler
            reconciler = PositionReconciler(
                order_router=order_router,
                symbol_y=sym_y,
                symbol_x=sym_x,
            )
            result = await reconciler.boot_scan()
            ctx.boot_scan_result = result

            # Notificare Telegram cu situatia completa
            if self._bus:
                try:
                    await self._bus.send_alert(result.to_telegram_msg(), level="info")
                except Exception:
                    pass

            logger.info(
                "[WFOrch v2.4] BootScan OK: equity={} pozitii={}",
                f"{result.wallet.equity:.2f} USDT" if result.wallet else "N/A",
                len(result.positions),
            )
        except Exception as exc:
            logger.warning("[WFOrch v2.4] BootScan failed (non-fatal): {}", exc)

    async def _reconcile_loop(self, ctx: StartupContext) -> None:
        """
        GAP 2b: Task periodic — reconciliaza pozitiile locale vs exchange la
        fiecare RECONCILE_INTERVAL_S secunde (default 60s).
        Daca detecteaza divergenta, logeaza WARNING si trimite Telegram.
        """
        interval = int(os.getenv("RECONCILE_INTERVAL_S", "60"))
        logger.info("[WFOrch v2.4] reconcile_loop pornit (interval={}s)", interval)

        order_router = (
            getattr(ctx.futures_runner, "_order_router", None)
            or getattr(ctx.futures_runner, "order_router",  None)
        )
        if order_router is None:
            logger.warning("[WFOrch v2.4] reconcile_loop SKIP — no order_router")
            return

        pairs = self._get_active_pairs()
        pair_str = pairs[0] if pairs else "BTCUSDT-ETHUSDT"
        parts = pair_str.replace("/", "-").split("-")
        sym_y = parts[0] if len(parts) >= 1 else "BTCUSDT"
        sym_x = parts[1] if len(parts) >= 2 else "ETHUSDT"

        try:
            from execution.position_reconciler import PositionReconciler
            reconciler = PositionReconciler(
                order_router=order_router,
                symbol_y=sym_y,
                symbol_x=sym_x,
            )
        except Exception as exc:
            logger.warning("[WFOrch v2.4] PositionReconciler init failed: {}", exc)
            return

        while True:
            try:
                await asyncio.sleep(interval)
                positions = await reconciler.scan_all_positions()
                # Verifica divergenta vs runner local
                runner = ctx.futures_runner
                if runner is not None and hasattr(runner, "_order_manager"):
                    om = runner._order_manager
                    active_syms_local = set(
                        r.request.symbol
                        for r in getattr(om, "_orders", {}).values()
                        if getattr(r, "status", None) and
                           str(r.status) in ("filled", "FILLED")
                    )
                    active_syms_exchange = {p.symbol for p in positions}
                    orphans_exchange = active_syms_exchange - active_syms_local
                    orphans_local    = active_syms_local   - active_syms_exchange
                    if orphans_exchange:
                        logger.warning(
                            "[WFOrch v2.4] reconcile: {} pozitii pe exchange fara local: {}",
                            len(orphans_exchange), orphans_exchange,
                        )
                        await self._alert(
                            f"\u26a0\ufe0f *Reconciliere* — {len(orphans_exchange)} pozitii "
                            f"orfane pe exchange: `{'`, `'.join(orphans_exchange)}`\n"
                            f"Verificati si adoptati manual daca e necesar."
                        )
                    if orphans_local:
                        logger.warning(
                            "[WFOrch v2.4] reconcile: {} pozitii locale fara exchange: {}",
                            len(orphans_local), orphans_local,
                        )
            except asyncio.CancelledError:
                logger.info("[WFOrch v2.4] reconcile_loop cancelled")
                return
            except Exception as exc:
                logger.warning("[WFOrch v2.4] reconcile_loop error (continua): {}", exc)

    # ------------------------------------------------------------------
    # GAP 4: PartialExitHandler — inregistrat pe runner
    # ------------------------------------------------------------------

    def _register_partial_exit(self, ctx: StartupContext) -> None:
        """
        Inregistreaza handle_partial_exit ca hook pe runner daca suporta,
        altfel il expune in ctx.partial_exit_handler pentru LiveTrader.
        """
        try:
            from execution.partial_exit_handler import handle_partial_exit
            ctx.partial_exit_handler = handle_partial_exit

            runner = ctx.futures_runner
            if runner is None:
                return

            # Inregistrare directa pe runner daca suporta hook
            if hasattr(runner, "register_partial_exit_hook"):
                runner.register_partial_exit_hook(handle_partial_exit)
                logger.info(
                    "[WFOrch v2.4] PartialExitHandler inregistrat pe runner "
                    "via register_partial_exit_hook()"
                )
                return

            # Inregistrare pe ActionExecutor daca accesibil
            ae = (
                getattr(runner, "_action_executor", None)
                or getattr(runner, "action_executor",  None)
            )
            if ae is not None and hasattr(ae, "set_partial_exit_handler"):
                ae.set_partial_exit_handler(handle_partial_exit)
                logger.info(
                    "[WFOrch v2.4] PartialExitHandler inregistrat pe ActionExecutor"
                )
                return

            # Fallback: expus in ctx — LiveTrader il poate accesa ca
            # ctx.partial_exit_handler(signal, position, exchange, checkpoint)
            logger.info(
                "[WFOrch v2.4] PartialExitHandler expus in ctx.partial_exit_handler "
                "(runner nu suporta hook direct — LiveTrader trebuie sa il apeleze)"
            )
        except Exception as exc:
            logger.warning(
                "[WFOrch v2.4] PartialExitHandler registration failed (non-fatal): {}", exc
            )

    # ------------------------------------------------------------------
    # GAP 5: Report scheduler zilnic 08:00 UTC
    # ------------------------------------------------------------------

    async def _report_loop(self, ctx: StartupContext) -> None:
        """
        Task periodic — trimite raport zilnic la 08:00 UTC.
        Incearca in ordine:
          1. reporting.daily_report.send_daily_report()
          2. DailyPnLTracker.get_daily_summary() cu formatare manuala
        Non-fatal.
        """
        report_hour   = int(os.getenv("DAILY_REPORT_HOUR_UTC",   "8"))
        report_minute = int(os.getenv("DAILY_REPORT_MINUTE_UTC", "0"))
        logger.info(
            "[WFOrch v2.4] report_loop pornit — raport zilnic {:02d}:{:02d} UTC",
            report_hour, report_minute,
        )

        while True:
            try:
                await self._wait_until_utc(report_hour, report_minute)
                await self._send_daily_report(ctx)
                await asyncio.sleep(70)  # evita dublu-trigger
            except asyncio.CancelledError:
                logger.info("[WFOrch v2.4] report_loop cancelled")
                return
            except Exception as exc:
                logger.warning("[WFOrch v2.4] report_loop error: {}", exc)
                await asyncio.sleep(300)

    async def _send_daily_report(self, ctx: StartupContext) -> None:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Incearca reporting.daily_report
        try:
            from reporting.daily_report import send_daily_report
            await send_daily_report(
                date=today,
                notifier_bus=self._bus,
                pnl_tracker=ctx.pnl_tracker,
            )
            logger.info("[WFOrch v2.4] Raport zilnic trimis via reporting.daily_report")
            return
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("[WFOrch v2.4] reporting.daily_report failed: {}", exc)

        # Fallback: DailyPnLTracker.get_daily_summary
        if ctx.pnl_tracker is None:
            return
        try:
            summary = await ctx.pnl_tracker.get_daily_summary(today)
            pairs   = self._get_active_pairs()
            equity  = summary.get("total_equity_usdt", 0.0)
            pnl     = summary.get("realised_pnl_usdt", 0.0)
            pnl_pct = (pnl / equity) if equity > 0 else 0.0
            emoji   = "✅" if pnl >= 0 else "❌"

            msg = (
                f"{emoji} *Raport zilnic {today}*\n"
                f"💰 Equity: `{equity:,.2f} USDT`\n"
                f"📈 PnL: `{pnl:+.2f} USDT` ({pnl_pct:+.2%})\n"
                f"📊 Perechi active: `{'`, `'.join(pairs)}`"
            )
            await self._alert(msg)
            logger.info("[WFOrch v2.4] Raport zilnic trimis (fallback PnLTracker)")
        except Exception as exc:
            logger.warning("[WFOrch v2.4] _send_daily_report fallback failed: {}", exc)

    @staticmethod
    async def _wait_until_utc(hour: int, minute: int) -> None:
        """Asteapta pana la urmatoarea ora:minut UTC."""
        import datetime as dt
        while True:
            now = dt.datetime.now(dt.timezone.utc)
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target:
                target += dt.timedelta(days=1)
            wait_s = (target - now).total_seconds()
            try:
                await asyncio.sleep(min(wait_s, 60))
            except asyncio.CancelledError:
                return
            now = dt.datetime.now(dt.timezone.utc)
            if now.hour == hour and now.minute == minute:
                return

    # ------------------------------------------------------------------
    # v2.3: Adoptare pozitii deschise la startup
    # ------------------------------------------------------------------

    async def _adopt_open_positions(self, ctx: StartupContext) -> None:
        """
        Preia pozitiile deschise de pe exchange la startup si le adopta in
        OrderManager cu TP/SL protectie imediata.
        ADOPT / CLOSE_NOW / MONITOR_ONLY.
        Non-fatal.
        """
        cfg = self._runner_cfg
        try:
            from execution.position_scanner import PositionScanner
            scanner = PositionScanner.from_env(cfg)
            scan_report = await scanner.scan()
        except Exception as exc:
            logger.warning(
                "[WFOrch v2.4] PositionScanner failed (non-fatal): {}", exc
            )
            return

        total = len(scan_report.orphans)
        if total == 0:
            logger.info("[WFOrch v2.4] Nicio pozitie orfana — adoptare nu e necesara")
            return

        try:
            from execution.adoption_engine import AdoptionEngine, AdoptionConfig, AdoptionDecision
            from execution.order_manager   import OrderManager, OrderManagerConfig
            from execution.checkpoint      import Checkpoint

            order_manager = OrderManager(OrderManagerConfig(
                base_qty=getattr(cfg, "base_qty", 0.01),
                entry_zscore=getattr(cfg, "entry_zscore", 2.0),
                exit_zscore=getattr(cfg, "exit_zscore", 0.5),
                dry_run=getattr(cfg, "dry_run", False),
            ))
            checkpoint_path = os.getenv(
                "CHECKPOINT_PATH",
                getattr(cfg, "checkpoint_path", "state/checkpoint.json"),
            )
            checkpoint = Checkpoint(path=checkpoint_path)
            exchange = (
                getattr(ctx.futures_runner, "_exchange", None)
                or getattr(ctx.futures_runner, "exchange",  None)
            )

            async def _on_cycle_restart(symbol: str) -> None:
                runner = ctx.futures_runner
                if runner is not None and hasattr(runner, "reset_cycle"):
                    await runner.reset_cycle(symbol)
                elif runner is not None and hasattr(runner, "_spread_monitor"):
                    runner._spread_monitor.reset()

            adoption_cfg = AdoptionConfig(
                close_loss_pct=float(os.getenv("ADOPT_CLOSE_LOSS_PCT",    "-0.05")),
                min_liq_distance_pct=float(os.getenv("ADOPT_MIN_LIQ_PCT",  "0.08")),
                min_notional_adopt=float(os.getenv("ADOPT_MIN_NOTIONAL",    "5.0")),
                tp_target_pct=float(os.getenv("ADOPT_TP_PCT",               "0.04")),
                sl_max_loss_pct=float(os.getenv("ADOPT_SL_PCT",             "0.03")),
                trailing_pct=float(os.getenv("ADOPT_TRAILING_PCT",          "0.015")),
                restart_cooldown_s=float(os.getenv("ADOPT_RESTART_COOLDOWN","10.0")),
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

            from execution.adoption_engine import AdoptionDecision
            adopted    = [r for r in results if r.decision == AdoptionDecision.ADOPT]
            closed_now = [r for r in results if r.decision == AdoptionDecision.CLOSE_NOW]

            # NativeSlTp re-arm
            if adopted:
                try:
                    from execution.native_sl_tp import NativeSlTp
                    router = (
                        getattr(ctx.futures_runner, "_order_router", None)
                        or getattr(ctx.futures_runner, "order_router",  None)
                    )
                    if router and hasattr(NativeSlTp, "rearm_if_missing"):
                        await NativeSlTp.rearm_if_missing(adopted, router)
                except Exception as exc:
                    logger.warning("[WFOrch v2.4] NativeSlTp rearm failed: {}", exc)

            if (adopted or closed_now) and self._bus:
                lines = [f"📋 *QuantLuna v{self.VERSION} — Adoptare pozitii*"]
                for r in adopted:
                    lines.append(
                        f"  ✅ `{r.position.symbol}` {r.position.side} "
                        f"TP=`{r.tp_price:.4f}` SL=`{r.sl_price:.4f}`"
                    )
                for r in closed_now:
                    lines.append(
                        f"  ⚠️ `{r.position.symbol}` INCHIS — {r.reason}"
                    )
                await self._alert("\n".join(lines))

        except Exception as exc:
            logger.warning("[WFOrch v2.4] AdoptionEngine failed (non-fatal): {}", exc)

    # ------------------------------------------------------------------
    # Lifecycle — start_runner (COMPLET v2.4)
    # ------------------------------------------------------------------

    async def start_runner(self) -> None:
        logger.info("[WFOrch v2.4] START")
        self._ctx         = self._build_context()
        self._runner      = self._build_runner(self._ctx)
        self._reoptimizer = self._ctx.auto_reoptimizer
        self._watchdog    = self._ctx.watchdog

        if self._runner is None:
            raise RuntimeError(
                "[WFOrch v2.4] Niciun runner disponibil. Verifica BYBIT_API_KEY."
            )

        self._register_all_services(self._ctx)

        # Pas 0: Boot scan complet (balanta + pozitii) [GAP 2]
        await self._run_boot_scan(self._ctx)

        # Pas 1: Adopta pozitii orfane [v2.3]
        await self._adopt_open_positions(self._ctx)

        # Pas 2: Inregistreaza PartialExitHandler [GAP 4]
        self._register_partial_exit(self._ctx)

        self._started = True

        # ─── Construieste lista coro-urilor pentru gather() ──────────
        coros = [self._runner.start()]

        if self._reoptimizer is not None:
            coros.append(self._reoptimizer.run_loop())
            logger.info("[WFOrch v2.4] + AutoReoptimizer")

        if self._watchdog is not None:
            coros.append(self._watchdog.run_loop())
            logger.info("[WFOrch v2.4] + MonitoringWatchdog")

        # GAP 1: CapitalAllocator run_loop() zilnic 23:55 UTC
        if self._ctx.capital_allocator is not None:
            coros.append(self._ctx.capital_allocator.run_loop())
            logger.info("[WFOrch v2.4] + CapitalAllocator (profit-take 23:55 UTC)")

        # GAP 3: MarginRiskGuard watch_loop() continuu 30s
        if self._ctx.margin_guard is not None:
            coros.append(self._ctx.margin_guard.watch_loop())
            logger.info("[WFOrch v2.4] + MarginRiskGuard (poll 30s)")

        # GAP 2b: PositionReconciler loop periodic 60s
        coros.append(self._reconcile_loop(self._ctx))
        logger.info("[WFOrch v2.4] + reconcile_loop (60s)")

        # GAP 5: Report scheduler zilnic 08:00 UTC
        coros.append(self._report_loop(self._ctx))
        logger.info("[WFOrch v2.4] + report_loop (08:00 UTC)")

        # ─── Mesaj startup complet ───────────────────────────────────
        markets = []
        if getattr(self._runner_cfg, "enable_futures", True):  markets.append("Futures")
        if getattr(self._runner_cfg, "enable_spot",    False):  markets.append("Spot")
        if getattr(self._runner_cfg, "enable_margin",  False):  markets.append("Margin")
        if not markets: markets = ["Futures"]

        services_count = len(coros)
        adopt_count = len([
            r for r in self._ctx.adoption_results
            if hasattr(r, "decision") and str(r.decision) == "adopt"
        ])

        await self._alert(
            f"\U0001f7e2 *QuantLuna v{self.VERSION} pornit*\n"
            f"  Piete: `{'` + `'.join(markets)}`\n"
            f"  Hedges: `{len(self._ctx.hedge_managers)}`\n"
            f"  Servicii active in gather: `{services_count}`\n"
            f"  Pozitii adoptate: `{adopt_count}`\n"
            f"  CapitalAllocator: `{'activ' if self._ctx.capital_allocator else 'OFF'}`\n"
            f"  MarginGuard: `{'activ' if self._ctx.margin_guard else 'OFF'}`\n"
            f"  Reconciliere: `60s` | Raport zilnic: `08:00 UTC`\n"
            f"  Dashboard: `http://localhost:3000`"
        )

        # ─── gather() ───────────────────────────────────────────────
        self._tasks = [asyncio.create_task(c) for c in coros]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("[WFOrch v2.4] gather() cancelled")
        except Exception as exc:
            logger.error("[WFOrch v2.4] Eroare fatala: {}", exc)
            await self._alert(f"\u274c *QuantLuna EROARE*: `{exc}`")
            raise
        finally:
            self._started = False

    async def stop_runner(self) -> None:
        self._started = False
        for comp in [
            self._runner,
            self._reoptimizer,
            self._watchdog,
            self._ctx.capital_allocator if self._ctx else None,
            self._ctx.margin_guard      if self._ctx else None,
        ]:
            if comp is not None and hasattr(comp, "stop"):
                try: comp.stop()
                except Exception: pass
        for t in self._tasks:
            if not t.done(): t.cancel()
        await asyncio.sleep(0)
        await self._alert("\U0001f534 *QuantLuna oprit.*")
        logger.info("[WFOrch v2.4] stop_runner() OK")

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

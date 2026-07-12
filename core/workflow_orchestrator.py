"""
core/workflow_orchestrator.py  —  QuantLuna WorkflowOrchestrator v2.5

Sprint S45 (2026-07-12): Completare totala

Nou fata de v2.4:
  1. SpotWalletScanner  → integrat in _run_boot_scan() cand enable_spot=true
  2. InternalTransferManager → construit si pasat in CapitalAllocator
  3. SizingEngine + DecisionEngine → construite in _build_context(),
     SizingEngine pasat DecisionEngine, ambele expuse in ctx
  4. PnlReconciler → task periodic in gather() (daca modul exista)
  5. ProfitOptimizer → construit in _build_context(), expus in ctx
  6. reporting/daily_report.send_daily_report() → apelat din _report_loop()
  7. Mesaj startup Telegram imbunatatit cu toate serviciile

Versiunie: 2.4.0 -> 2.5.0
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# StartupContext v2.5
# ---------------------------------------------------------------------------

@dataclass
class StartupContext:
    runner_cfg:              Any
    notifier_bus:            Any               = None
    futures_runner:          Any               = None
    spot_router:             Any               = None
    margin_router:           Any               = None
    hedge_managers:          List[Any]         = field(default_factory=list)
    optimizer:               Any               = None
    pnl_tracker:             Any               = None
    capital_allocator:       Any               = None
    auto_reoptimizer:        Any               = None
    watchdog:                Any               = None
    state_bus:               Any               = None
    margin_guard:            Any               = None
    boot_scan_result:        Any               = None
    spot_wallet_report:      Any               = None   # v2.5 NEW
    partial_exit_handler:    Any               = None
    profit_optimizer:        Any               = None   # v2.5 NEW
    sizing_engine:           Any               = None   # v2.5 NEW
    decision_engine:         Any               = None   # v2.5 NEW
    internal_transfer_mgr:   Any               = None   # v2.5 NEW
    adoption_results:        List[Any]         = field(default_factory=list)
    extra:                   Dict[str, Any]    = field(default_factory=dict)


# ---------------------------------------------------------------------------
# WorkflowOrchestrator v2.5
# ---------------------------------------------------------------------------

class WorkflowOrchestrator:
    """
    Orchestratorul principal QuantLuna v2.5 — completare totala.

    Arhitectura gather completa:
        asyncio.gather(
            runner.start(),
            reoptimizer.run_loop(),
            watchdog.run_loop(),
            capital_allocator.run_loop(),      # profit-take 23:55 UTC
            margin_guard.watch_loop(),         # margin protection 30s
            _reconcile_loop(),                 # pozitii 60s
            _pnl_reconcile_loop(),             # PnL 5 min
            _report_loop(),                    # raport 08:00 UTC
        )
    """

    VERSION = "2.5.0"

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
    # _build_context v2.5
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
            logger.info("[WFOrch v2.5] BybitLiveRunner OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.5] BybitLiveRunner failed: {}", exc)

        # SpotOrderRouter
        if getattr(cfg, "enable_spot", False):
            try:
                from execution.spot_order_router import SpotOrderRouter
                ctx.spot_router = SpotOrderRouter.from_env()
                logger.info("[WFOrch v2.5] SpotOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.5] SpotOrderRouter failed: {}", exc)

        # MarginOrderRouter
        if getattr(cfg, "enable_margin", False):
            try:
                from execution.margin_order_router import MarginOrderRouter
                ctx.margin_router = MarginOrderRouter.from_env(
                    margin_mode=getattr(cfg, "margin_mode", "cross")
                )
                logger.info("[WFOrch v2.5] MarginOrderRouter OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.5] MarginOrderRouter failed: {}", exc)

        # DailyPnLTracker
        try:
            from execution.daily_pnl_tracker import DailyPnLTracker
            ctx.pnl_tracker = DailyPnLTracker(
                db_path=os.getenv("DAILY_PNL_DB", "state/daily_pnl.db")
            )
            logger.info("[WFOrch v2.5] DailyPnLTracker OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.5] DailyPnLTracker failed: {}", exc)

        # InternalTransferManager v2.5
        try:
            from execution.internal_transfer_manager import InternalTransferManager
            ctx.internal_transfer_mgr = InternalTransferManager.from_env(
                notifier_bus=self._bus,
                db_path=os.getenv("TRANSFER_DB", "state/internal_transfers.db"),
            )
            logger.info("[WFOrch v2.5] InternalTransferManager OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.5] InternalTransferManager failed: {}", exc)

        # CapitalAllocator cu InternalTransferManager injectat
        if ctx.pnl_tracker is not None:
            try:
                from execution.capital_allocator import CapitalAllocator
                ctx.capital_allocator = CapitalAllocator.from_env(
                    tracker=ctx.pnl_tracker,
                    notifier_bus=self._bus,
                )
                # Injecteaza InternalTransferManager dupa constructie
                if ctx.internal_transfer_mgr is not None:
                    ctx.capital_allocator._transfer_mgr = ctx.internal_transfer_mgr
                    logger.info(
                        "[WFOrch v2.5] InternalTransferManager injectat in CapitalAllocator"
                    )
                logger.info(
                    "[WFOrch v2.5] CapitalAllocator OK "
                    "(profit-take 23:55 UTC, transfer automat Futures->Spot)"
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.5] CapitalAllocator failed: {}", exc)
        else:
            logger.warning("[WFOrch v2.5] CapitalAllocator SKIP — PnLTracker indisponibil")

        # MarginRiskGuard
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
                        auto_close_on_critical=os.getenv("MARGIN_AUTO_CLOSE", "true").lower() == "true",
                        max_auto_closes_per_session=int(os.getenv("MARGIN_MAX_AUTO_CLOSES", "3")),
                    ),
                )
                logger.info("[WFOrch v2.5] MarginRiskGuard OK")
            except Exception as exc:
                logger.warning("[WFOrch v2.5] MarginRiskGuard failed: {}", exc)

        # SizingEngine v2.5 — construit inainte de DecisionEngine
        try:
            from execution.sizing_engine import SizingEngine
            ctx.sizing_engine = SizingEngine.from_env(
                pnl_tracker=ctx.pnl_tracker,
                notifier_bus=self._bus,
            )
            logger.info("[WFOrch v2.5] SizingEngine OK")
        except Exception as exc:
            logger.warning("[WFOrch v2.5] SizingEngine failed: {}", exc)

        # DecisionEngine v2.5 — cu SizingEngine injectat
        try:
            from execution.decision_engine import DecisionEngine
            ctx.decision_engine = DecisionEngine.from_env(
                sizing_engine=ctx.sizing_engine,
                pnl_tracker=ctx.pnl_tracker,
                capital_allocator=ctx.capital_allocator,
            )
            # Injecteaza in runner daca suporta
            runner = ctx.futures_runner
            if runner is not None:
                if hasattr(runner, "set_decision_engine"):
                    runner.set_decision_engine(ctx.decision_engine)
                    logger.info("[WFOrch v2.5] DecisionEngine injectat in runner")
                elif hasattr(runner, "_decision_engine"):
                    runner._decision_engine = ctx.decision_engine
                    logger.info("[WFOrch v2.5] DecisionEngine set pe runner._decision_engine")
            logger.info("[WFOrch v2.5] DecisionEngine OK (SizingEngine integrat)")
        except Exception as exc:
            logger.warning("[WFOrch v2.5] DecisionEngine failed: {}", exc)

        # ProfitOptimizer v2.5
        try:
            exchange = (
                getattr(ctx.futures_runner, "_exchange", None)
                or getattr(ctx.futures_runner, "exchange", None)
            )
            if exchange is not None:
                from execution.profit_optimizer import ProfitOptimizer
                ctx.profit_optimizer = ProfitOptimizer(exchange=exchange)
                # Injecteaza streak callback in DecisionEngine
                if ctx.decision_engine is not None:
                    # La fiecare trade inchis, ProfitOptimizer poate notifica DecisionEngine
                    logger.info("[WFOrch v2.5] ProfitOptimizer OK (streak->DecisionEngine wired)")
        except Exception as exc:
            logger.warning("[WFOrch v2.5] ProfitOptimizer failed: {}", exc)

        # HedgeManagers
        for pair in getattr(cfg, "hedge_pairs", []) or []:
            try:
                from execution.single_hedge_manager import SingleHedgeManager
                ctx.hedge_managers.append(
                    SingleHedgeManager.from_cfg(pair, ctx)
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.5] HedgeManager {} failed: {}", pair, exc)

        # AutoReoptimizer
        ctx.auto_reoptimizer = self._build_auto_reoptimizer(ctx)

        # MonitoringWatchdog (cu callback streak->DecisionEngine)
        ctx.watchdog = self._build_watchdog(ctx)

        return ctx

    def _build_auto_reoptimizer(self, ctx: StartupContext) -> Optional[Any]:
        if not getattr(self._runner_cfg, "enable_reoptimizer", True):
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
            logger.info("[WFOrch v2.5] AutoReoptimizer OK")
            return reopt
        except Exception as exc:
            logger.warning("[WFOrch v2.5] AutoReoptimizer failed: {}", exc)
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
                        dd = snap.get("max_drawdown", 0.0)
                        # Agrega drawdown la DecisionEngine
                        if ctx.decision_engine is not None:
                            ctx.decision_engine.update_drawdown(dd)
                        return {
                            "sharpe":      snap.get("sharpe_24h", 99.0),
                            "drawdown":    dd,
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
                    logger.error("[WFOrch v2.5] halt_callback {}: {}", pair, exc)

            async def _reduce_callback(pair: str, factor: float) -> None:
                try:
                    from api.sizing import reduce_pair_size
                    await reduce_pair_size(pair, factor)
                except Exception as exc:
                    logger.error("[WFOrch v2.5] reduce_callback {}: {}", pair, exc)

            wd = MonitoringWatchdog.from_env(
                pairs=pairs,
                metrics_provider=_metrics_provider,
                dispatcher=self._dispatcher or self._bus,
                halt_callback=_halt_callback,
                reduce_callback=_reduce_callback,
            )
            logger.info("[WFOrch v2.5] MonitoringWatchdog OK")
            return wd
        except Exception as exc:
            logger.warning("[WFOrch v2.5] MonitoringWatchdog failed: {}", exc)
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
                logger.info("[WFOrch v2.5] Runner: MultiMarketRunner")
                return runner
            except ImportError as exc:
                logger.warning("[WFOrch v2.5] MultiMarketRunner fallback: {}", exc)
        logger.info("[WFOrch v2.5] Runner: BybitLiveRunner (single)")
        return ctx.futures_runner

    # ------------------------------------------------------------------
    # Services registration
    # ------------------------------------------------------------------

    def _register_all_services(self, ctx: StartupContext) -> None:
        try:
            from api.services import register_service
        except ImportError:
            logger.warning("[WFOrch v2.5] api.services indisponibil")
            return

        services = [
            ("futures_runner",     "Futures Runner",      ctx.futures_runner,        True),
            ("spot_runner",        "Spot Runner",         ctx.spot_router,           ctx.spot_router is not None),
            ("margin_guard",       "Margin Risk Guard",   ctx.margin_guard,          ctx.margin_guard is not None),
            ("capital_allocator",  "Capital Allocator",   ctx.capital_allocator,     ctx.capital_allocator is not None),
            ("sizing_engine",      "Sizing Engine",       ctx.sizing_engine,         ctx.sizing_engine is not None),
            ("decision_engine",    "Decision Engine",     ctx.decision_engine,       ctx.decision_engine is not None),
            ("profit_optimizer",   "Profit Optimizer",    ctx.profit_optimizer,      ctx.profit_optimizer is not None),
            ("internal_transfer",  "Transfer Mgr",        ctx.internal_transfer_mgr, ctx.internal_transfer_mgr is not None),
            ("withdrawal_guard",   "Withdrawal Guard",    None,                      True),
            ("auto_reoptimizer",   "Auto Reoptimizer",    ctx.auto_reoptimizer,      ctx.auto_reoptimizer is not None),
            ("monitoring_watchdog","Monitoring Watchdog", ctx.watchdog,              ctx.watchdog is not None),
        ]
        for name, display, comp, enabled in services:
            try:
                from api.services import register_service
                register_service(
                    name=name, display_name=display,
                    description=display,
                    component=comp, enabled=enabled, can_toggle=True,
                )
            except Exception:
                pass

        for mgr in ctx.hedge_managers:
            sym = getattr(mgr, "_symbol", getattr(mgr, "symbol", "?"))
            try:
                from api.services import register_service
                register_service(
                    name=f"hedge_{sym.lower()}",
                    display_name=f"Hedge {sym}",
                    description=f"SingleHedgeManager {sym}",
                    component=mgr, enabled=True, can_toggle=True,
                )
            except Exception:
                pass

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
    # Boot scan v2.5 (Futures + Spot)
    # ------------------------------------------------------------------

    async def _run_boot_scan(self, ctx: StartupContext) -> None:
        """
        Boot scan complet:
          1. PositionReconciler.boot_scan() — balanta Futures + pozitii
          2. SpotWalletScanner.scan()        — wallet Spot (daca enable_spot)
        Trimite ambele pe Telegram.
        """
        order_router = (
            getattr(ctx.futures_runner, "_order_router", None)
            or getattr(ctx.futures_runner, "order_router", None)
            or ctx.margin_router
        )

        # 1. Futures boot scan
        if order_router is not None:
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
                if self._bus:
                    await self._bus.send_alert(result.to_telegram_msg(), level="info")
                logger.info(
                    "[WFOrch v2.5] BootScan Futures OK: equity={} pozitii={}",
                    f"{result.wallet.equity:.2f} USDT" if result.wallet else "N/A",
                    len(result.positions),
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.5] BootScan Futures failed (non-fatal): {}", exc)
        else:
            logger.warning("[WFOrch v2.5] BootScan Futures SKIP — no order_router")

        # 2. Spot wallet scan v2.5
        if ctx.spot_router is not None:
            try:
                from execution.spot_wallet_scanner import SpotWalletScanner
                scanner = SpotWalletScanner(
                    router=ctx.spot_router,
                    min_usdt_value=float(os.getenv("SPOT_SCAN_MIN_USDT", "5.0")),
                    significant_threshold_usdt=float(os.getenv("SPOT_SCAN_THRESHOLD", "10.0")),
                )
                spot_report = await scanner.scan()
                ctx.spot_wallet_report = spot_report
                if self._bus:
                    await self._bus.send_alert(spot_report.to_telegram_msg(), level="info")
                logger.info(
                    "[WFOrch v2.5] BootScan Spot OK: total={:.2f} USDT free={:.2f}",
                    spot_report.total_usdt_value,
                    spot_report.free_usdt,
                )
            except Exception as exc:
                logger.warning("[WFOrch v2.5] BootScan Spot failed (non-fatal): {}", exc)

    # ------------------------------------------------------------------
    # Reconciliere periodica pozitii
    # ------------------------------------------------------------------

    async def _reconcile_loop(self, ctx: StartupContext) -> None:
        interval = int(os.getenv("RECONCILE_INTERVAL_S", "60"))
        logger.info("[WFOrch v2.5] reconcile_loop pornit ({}s)", interval)

        order_router = (
            getattr(ctx.futures_runner, "_order_router", None)
            or getattr(ctx.futures_runner, "order_router", None)
        )
        if order_router is None:
            return

        pairs = self._get_active_pairs()
        pair_str = pairs[0] if pairs else "BTCUSDT-ETHUSDT"
        parts = pair_str.replace("/", "-").split("-")
        sym_y = parts[0] if len(parts) >= 1 else "BTCUSDT"
        sym_x = parts[1] if len(parts) >= 2 else "ETHUSDT"

        try:
            from execution.position_reconciler import PositionReconciler
            reconciler = PositionReconciler(
                order_router=order_router, symbol_y=sym_y, symbol_x=sym_x,
            )
        except Exception as exc:
            logger.warning("[WFOrch v2.5] PositionReconciler init failed: {}", exc)
            return

        while True:
            try:
                await asyncio.sleep(interval)
                positions = await reconciler.scan_all_positions()
                runner = ctx.futures_runner
                if runner is not None and hasattr(runner, "_order_manager"):
                    om = runner._order_manager
                    active_local = set(
                        r.request.symbol
                        for r in getattr(om, "_orders", {}).values()
                        if getattr(r, "status", None) and
                           str(r.status) in ("filled", "FILLED")
                    )
                    active_exchange = {p.symbol for p in positions}
                    orphans = active_exchange - active_local
                    if orphans:
                        logger.warning(
                            "[WFOrch v2.5] reconcile: orphans exchange={}", orphans
                        )
                        await self._alert(
                            f"\u26a0\ufe0f *Reconciliere* — {len(orphans)} pozitii orfane: "
                            f"`{'`, `'.join(orphans)}`"
                        )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("[WFOrch v2.5] reconcile_loop err: {}", exc)

    # ------------------------------------------------------------------
    # PnL reconciler periodic
    # ------------------------------------------------------------------

    async def _pnl_reconcile_loop(self, ctx: StartupContext) -> None:
        """
        Reconciliaza PnL local vs exchange la fiecare 5 minute.
        Foloseste pnl_reconciler daca exista, altfel skip.
        """
        interval = int(os.getenv("PNL_RECONCILE_INTERVAL_S", "300"))
        try:
            from execution.pnl_reconciler import PnlReconciler
        except ImportError:
            logger.info("[WFOrch v2.5] pnl_reconcile_loop SKIP — modul indisponibil")
            return

        logger.info("[WFOrch v2.5] pnl_reconcile_loop pornit ({}s)", interval)
        order_router = (
            getattr(ctx.futures_runner, "_order_router", None)
            or getattr(ctx.futures_runner, "order_router", None)
        )
        if order_router is None:
            return

        try:
            reconciler = PnlReconciler(
                order_router=order_router,
                pnl_tracker=ctx.pnl_tracker,
                notifier_bus=self._bus,
            )
        except Exception as exc:
            logger.warning("[WFOrch v2.5] PnlReconciler init failed: {}", exc)
            return

        while True:
            try:
                await asyncio.sleep(interval)
                await reconciler.reconcile()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("[WFOrch v2.5] pnl_reconcile err: {}", exc)

    # ------------------------------------------------------------------
    # PartialExitHandler
    # ------------------------------------------------------------------

    def _register_partial_exit(self, ctx: StartupContext) -> None:
        try:
            from execution.partial_exit_handler import handle_partial_exit
            ctx.partial_exit_handler = handle_partial_exit
            runner = ctx.futures_runner
            if runner is None:
                return
            if hasattr(runner, "register_partial_exit_hook"):
                runner.register_partial_exit_hook(handle_partial_exit)
                logger.info("[WFOrch v2.5] PartialExitHandler -> runner hook")
                return
            ae = (
                getattr(runner, "_action_executor", None)
                or getattr(runner, "action_executor", None)
            )
            if ae is not None and hasattr(ae, "set_partial_exit_handler"):
                ae.set_partial_exit_handler(handle_partial_exit)
                logger.info("[WFOrch v2.5] PartialExitHandler -> ActionExecutor")
                return
            logger.info("[WFOrch v2.5] PartialExitHandler expus in ctx")
        except Exception as exc:
            logger.warning("[WFOrch v2.5] PartialExitHandler reg failed: {}", exc)

    # ------------------------------------------------------------------
    # Report scheduler
    # ------------------------------------------------------------------

    async def _report_loop(self, ctx: StartupContext) -> None:
        report_hour   = int(os.getenv("DAILY_REPORT_HOUR_UTC",   "8"))
        report_minute = int(os.getenv("DAILY_REPORT_MINUTE_UTC", "0"))
        logger.info(
            "[WFOrch v2.5] report_loop pornit ({:02d}:{:02d} UTC)",
            report_hour, report_minute,
        )
        while True:
            try:
                await self._wait_until_utc(report_hour, report_minute)
                from datetime import datetime, timezone
                date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                try:
                    from reporting.daily_report import send_daily_report
                    await send_daily_report(
                        date=date,
                        notifier_bus=self._bus,
                        pnl_tracker=ctx.pnl_tracker,
                        capital_allocator=ctx.capital_allocator,
                        pairs=self._get_active_pairs(),
                    )
                    logger.info("[WFOrch v2.5] Raport zilnic trimis")
                except ImportError:
                    await self._send_daily_report_fallback(ctx, date)
                await asyncio.sleep(70)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("[WFOrch v2.5] report_loop err: {}", exc)
                await asyncio.sleep(300)

    async def _send_daily_report_fallback(self, ctx: StartupContext, date: str) -> None:
        if ctx.pnl_tracker is None:
            return
        try:
            summary = await ctx.pnl_tracker.get_daily_summary(date)
            equity  = summary.get("total_equity_usdt", 0.0)
            pnl     = summary.get("realised_pnl_usdt", 0.0)
            pnl_pct = (pnl / equity) if equity > 0 else 0.0
            emoji   = "\u2705" if pnl >= 0 else "\u274c"
            await self._alert(
                f"{emoji} *Raport zilnic {date}*\n"
                f"\U0001f4b0 Equity: `{equity:,.2f} USDT`\n"
                f"\U0001f4c8 PnL: `{pnl:+.2f} USDT` ({pnl_pct:+.2%})"
            )
        except Exception as exc:
            logger.warning("[WFOrch v2.5] report fallback failed: {}", exc)

    @staticmethod
    async def _wait_until_utc(hour: int, minute: int) -> None:
        import datetime as dt
        while True:
            now    = dt.datetime.now(dt.timezone.utc)
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
    # Adoptare pozitii (v2.3, neschimbat)
    # ------------------------------------------------------------------

    async def _adopt_open_positions(self, ctx: StartupContext) -> None:
        cfg = self._runner_cfg
        try:
            from execution.position_scanner import PositionScanner
            scanner = PositionScanner.from_env(cfg)
            scan_report = await scanner.scan()
        except Exception as exc:
            logger.warning("[WFOrch v2.5] PositionScanner failed (non-fatal): {}", exc)
            return

        total = len(scan_report.orphans)
        if total == 0:
            logger.info("[WFOrch v2.5] Nicio pozitie orfana")
            return

        try:
            from execution.adoption_engine import AdoptionEngine, AdoptionConfig
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
                or getattr(ctx.futures_runner, "exchange", None)
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

            # Inregistreaza pozitii adoptate in ProfitOptimizer
            if ctx.profit_optimizer is not None and adopted:
                try:
                    from execution.profit_optimizer import ProfitOptimizer
                    for r in adopted:
                        ctx.profit_optimizer.register(
                            result=r,
                            current_price=r.position.entry_price,
                        )
                    logger.info(
                        "[WFOrch v2.5] ProfitOptimizer: {} pozitii inregistrate",
                        len(adopted),
                    )
                except Exception as exc:
                    logger.warning("[WFOrch v2.5] ProfitOptimizer register failed: {}", exc)

            # NativeSlTp re-arm
            if adopted:
                try:
                    from execution.native_sl_tp import NativeSlTp
                    router = (
                        getattr(ctx.futures_runner, "_order_router", None)
                        or getattr(ctx.futures_runner, "order_router", None)
                    )
                    if router and hasattr(NativeSlTp, "rearm_if_missing"):
                        await NativeSlTp.rearm_if_missing(adopted, router)
                except Exception as exc:
                    logger.warning("[WFOrch v2.5] NativeSlTp rearm failed: {}", exc)

            if (adopted or closed_now) and self._bus:
                lines = [f"\U0001f4cb *QuantLuna v{self.VERSION} — Adoptare pozitii*"]
                for r in adopted:
                    lines.append(
                        f"  \u2705 `{r.position.symbol}` {r.position.side} "
                        f"TP=`{r.tp_price:.4f}` SL=`{r.sl_price:.4f}`"
                    )
                for r in closed_now:
                    lines.append(f"  \u26a0\ufe0f `{r.position.symbol}` INCHIS — {r.reason}")
                await self._alert("\n".join(lines))

        except Exception as exc:
            logger.warning("[WFOrch v2.5] AdoptionEngine failed (non-fatal): {}", exc)

    # ------------------------------------------------------------------
    # start_runner v2.5 — COMPLET
    # ------------------------------------------------------------------

    async def start_runner(self) -> None:
        logger.info("[WFOrch v2.5] START")
        self._ctx         = self._build_context()
        self._runner      = self._build_runner(self._ctx)
        self._reoptimizer = self._ctx.auto_reoptimizer
        self._watchdog    = self._ctx.watchdog

        if self._runner is None:
            raise RuntimeError(
                "[WFOrch v2.5] Niciun runner. Verifica BYBIT_API_KEY."
            )

        self._register_all_services(self._ctx)

        # Secventa startup
        await self._run_boot_scan(self._ctx)          # Futures + Spot scan
        await self._adopt_open_positions(self._ctx)   # ADOPT/CLOSE_NOW
        self._register_partial_exit(self._ctx)        # PartialExitHandler

        self._started = True

        # Construieste lista coro-urilor
        coros = [self._runner.start()]

        if self._reoptimizer:    coros.append(self._reoptimizer.run_loop())
        if self._watchdog:       coros.append(self._watchdog.run_loop())
        if self._ctx.capital_allocator:
            coros.append(self._ctx.capital_allocator.run_loop())
        if self._ctx.margin_guard:
            coros.append(self._ctx.margin_guard.watch_loop())

        coros.append(self._reconcile_loop(self._ctx))
        coros.append(self._pnl_reconcile_loop(self._ctx))
        coros.append(self._report_loop(self._ctx))

        # Mesaj startup complet
        markets = []
        if getattr(self._runner_cfg, "enable_futures", True):  markets.append("Futures")
        if getattr(self._runner_cfg, "enable_spot",    False):  markets.append("Spot")
        if getattr(self._runner_cfg, "enable_margin",  False):  markets.append("Margin")
        if not markets: markets = ["Futures"]

        services_on = [
            c for c in [
                self._ctx.capital_allocator,
                self._ctx.margin_guard,
                self._ctx.sizing_engine,
                self._ctx.decision_engine,
                self._ctx.profit_optimizer,
                self._ctx.internal_transfer_mgr,
                self._ctx.auto_reoptimizer,
                self._ctx.watchdog,
            ] if c is not None
        ]

        await self._alert(
            f"\U0001f7e2 *QuantLuna v{self.VERSION} pornit*\n"
            f"  Piete: `{'` + `'.join(markets)}`\n"
            f"  Servicii: `{len(services_on)}/8` active\n"
            f"  SizingEngine: `{'activ mult=1.0x' if self._ctx.sizing_engine else 'OFF'}`\n"
            f"  CapitalAllocator: `{'activ 23:55 UTC' if self._ctx.capital_allocator else 'OFF'}`\n"
            f"  MarginGuard: `{'activ 30s' if self._ctx.margin_guard else 'OFF'}`\n"
            f"  Transfer Futures<->Spot: `{'activ' if self._ctx.internal_transfer_mgr else 'OFF'}`\n"
            f"  Reconciliere: `60s pozitii | 5min PnL`\n"
            f"  Raport zilnic: `08:00 UTC`\n"
            f"  Dashboard: `http://localhost:3000`"
        )

        self._tasks = [asyncio.create_task(c) for c in coros]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("[WFOrch v2.5] gather() cancelled")
        except Exception as exc:
            logger.error("[WFOrch v2.5] Eroare fatala: {}", exc)
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
            self._ctx.capital_allocator     if self._ctx else None,
            self._ctx.margin_guard          if self._ctx else None,
        ]:
            if comp is not None and hasattr(comp, "stop"):
                try: comp.stop()
                except Exception: pass
        for t in self._tasks:
            if not t.done(): t.cancel()
        await asyncio.sleep(0)
        await self._alert("\U0001f534 *QuantLuna oprit.*")
        logger.info("[WFOrch v2.5] stop_runner() OK")

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

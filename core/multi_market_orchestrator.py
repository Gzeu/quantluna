"""
core/multi_market_orchestrator.py  —  MultiMarketOrchestrator v2.2

Sprint 32 (2026-07-12) — implementare completa.

Orchestratorul multi-market gestioneaza rularea simultana a:
  • BybitLiveRunner (futures linear, multi-pereche)
  • MonitoringWatchdog (Sharpe / DD / z-score / half-life / loss-streak)
  • AutoReoptimizer (WFO saptamanal, aplica automat parametrii noi)

Trei taskuri asyncio ruleaza in parallel via asyncio.gather().

Diferenta fata de execution.WorkflowOrchestrator:
  WorkflowOrchestrator  = startup workflow (5 faze: HealthCheck → Runner)
  MultiMarketOrchestrator = runtime multi-market dupa startup

Usage::

    # Simplu (din env vars)
    orch = MultiMarketOrchestrator.from_env(dispatcher=alert_dispatcher)
    ctx  = await orch.build_context()
    await orch.start_runner(ctx)   # blocheaza pana la stop()

    # Cu config explicit
    orch = MultiMarketOrchestrator(
        pairs=["BTCUSDT-ETHUSDT", "SOLUSDT-AVAXUSDT"],
        runner=bybit_runner,
        notifier_bus=bus,
        dispatcher=alert_dispatcher,
    )

Variabile de mediu (mostenitoare din S44b):
  WATCHDOG_ENABLED, WATCHDOG_CHECK_INTERVAL, WATCHDOG_SHARPE_MIN,
  WATCHDOG_MAX_DD, WATCHDOG_Z_MAX, WATCHDOG_HL_MAX
  OPTIMIZER_ENABLED, REOPT_SCHEDULE_DAY, REOPT_SCHEDULE_HOUR,
  REOPT_GRID_TYPE, REOPT_DRY_RUN
  PAIRS ("BTCUSDT-ETHUSDT,SOLUSDT-AVAXUSDT")
  ENABLE_SPOT, ENABLE_MARGIN
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


# ── MultiMarketContext ────────────────────────────────────────────────────────────────

@dataclass
class MultiMarketContext:
    """
    Starea runtime a sesiunii multi-market.
    Produsa de MultiMarketOrchestrator.build_context().
    """
    pairs:             List[str]                   = field(default_factory=list)
    enable_spot:       bool                        = False
    enable_margin:     bool                        = False
    enable_watchdog:   bool                        = True
    enable_reoptimizer: bool                       = True

    # Subsisteme instantiate
    runner:            Optional[Any]               = None
    watchdog:          Optional[Any]               = None  # MonitoringWatchdog
    reoptimizer:       Optional[Any]               = None  # AutoReoptimizer

    # Stare runtime
    should_halt:       bool                        = False
    halt_reason:       str                         = ""
    started_at:        Optional[str]               = None

    @property
    def active_subsystems(self) -> List[str]:
        subs = ["runner"]
        if self.watchdog is not None:
            subs.append("watchdog")
        if self.reoptimizer is not None:
            subs.append("reoptimizer")
        return subs


# ── MultiMarketOrchestrator ────────────────────────────────────────────────────────

class MultiMarketOrchestrator:
    """
    Orchestrator multi-market QuantLuna v2.2.

    Porneste 3 coroutine in asyncio.gather():
      1. runner.start()          — BybitLiveRunner (trading loop)
      2. watchdog.run_loop()     — MonitoringWatchdog (60s checks)
      3. reoptimizer.run_loop()  — AutoReoptimizer (saptamanal)

    La orice CancelledError sau exceptie runner, oprim graceful toate.
    """

    VERSION = "2.2.0"

    def __init__(
        self,
        pairs:            Optional[List[str]]  = None,
        runner:           Optional[Any]        = None,
        runner_cfg:       Optional[Any]        = None,
        notifier_bus:     Optional[Any]        = None,
        dispatcher:       Optional[Any]        = None,
        enable_spot:      bool                 = False,
        enable_margin:    bool                 = False,
        enable_watchdog:  bool                 = True,
        enable_reoptimizer: bool               = True,
        per_pair_watchdog_cfg: Optional[Dict[str, Dict]] = None,
    ) -> None:
        self._pairs             = pairs or []
        self._runner            = runner
        self._runner_cfg        = runner_cfg
        self._bus               = notifier_bus
        self._dispatcher        = dispatcher
        self._enable_spot       = enable_spot
        self._enable_margin     = enable_margin
        self._enable_watchdog   = enable_watchdog
        self._enable_reoptimizer = enable_reoptimizer
        self._per_pair_wd_cfg   = per_pair_watchdog_cfg or {}
        self._ctx: Optional[MultiMarketContext] = None
        self._tasks: List[asyncio.Task] = []

    # ─ Constructori ────────────────────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        dispatcher:           Optional[Any]            = None,
        runner:               Optional[Any]            = None,
        notifier_bus:         Optional[Any]            = None,
        per_pair_watchdog_cfg: Optional[Dict[str, Dict]] = None,
    ) -> "MultiMarketOrchestrator":
        """
        Builder complet din variabile de mediu.

        PAIRS="BTCUSDT-ETHUSDT,SOLUSDT-AVAXUSDT"   (sau SYMBOL_Y-SYMBOL_X single)
        ENABLE_SPOT=false
        ENABLE_MARGIN=false
        WATCHDOG_ENABLED=true
        OPTIMIZER_ENABLED=true
        """
        raw_pairs = os.getenv("PAIRS", "")
        if raw_pairs:
            pairs = [p.strip() for p in raw_pairs.split(",") if p.strip()]
        else:
            sym_y = os.getenv("SYMBOL_Y", "BTCUSDT")
            sym_x = os.getenv("SYMBOL_X", "ETHUSDT")
            pairs = [f"{sym_y}-{sym_x}"]

        return cls(
            pairs=pairs,
            runner=runner,
            notifier_bus=notifier_bus,
            dispatcher=dispatcher,
            enable_spot=os.getenv("ENABLE_SPOT", "false").lower() == "true",
            enable_margin=os.getenv("ENABLE_MARGIN", "false").lower() == "true",
            enable_watchdog=os.getenv("WATCHDOG_ENABLED", "true").lower() != "false",
            enable_reoptimizer=os.getenv("OPTIMIZER_ENABLED", "true").lower() != "false",
            per_pair_watchdog_cfg=per_pair_watchdog_cfg,
        )

    # ─ Build context ───────────────────────────────────────────────────────────

    async def build_context(self) -> MultiMarketContext:
        """
        Instantiaza toate subsistemele si intoarce MultiMarketContext.
        Apeleaza inainte de start_runner().
        """
        from datetime import datetime, timezone
        ctx = MultiMarketContext(
            pairs=list(self._pairs),
            enable_spot=self._enable_spot,
            enable_margin=self._enable_margin,
            enable_watchdog=self._enable_watchdog,
            enable_reoptimizer=self._enable_reoptimizer,
            runner=self._runner,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # Watchdog
        if self._enable_watchdog and self._pairs:
            ctx.watchdog = await self._build_watchdog(ctx)

        # Reoptimizer
        if self._enable_reoptimizer and self._pairs:
            ctx.reoptimizer = await self._build_reoptimizer(ctx)

        self._ctx = ctx
        logger.info(
            "[MMOrch] Context construit | perechi={} | subsisteme={}",
            len(ctx.pairs), ctx.active_subsystems,
        )
        return ctx

    # ─ Lifecycle ──────────────────────────────────────────────────────────────

    async def start_runner(self, ctx: Optional[MultiMarketContext] = None) -> None:
        """
        Porneste toate taskurile in parallel via asyncio.gather().
        Blocheaza pana la stop() sau exceptie.

        Daca ctx=None, construieste automat via build_context().
        """
        if ctx is None:
            ctx = await self.build_context()

        if ctx.should_halt:
            logger.error("[MMOrch] start_runner: context HALT — nu pornesc")
            return

        runner = ctx.runner or self._runner
        if runner is None:
            raise RuntimeError(
                "[MMOrch] Niciun runner furnizat. "
                "Seteaza runner= sau injecteaza via WorkflowOrchestrator.start_runner()."
            )

        logger.info(
            "[MMOrch] start_runner v{} | perechi={} | spot={} | margin={}",
            self.VERSION, ctx.pairs, ctx.enable_spot, ctx.enable_margin,
        )
        await self._alert(
            f"\U0001f680 *MultiMarketOrchestrator v{self.VERSION} pornit*\n"
            f"  Perechi: `{'  |  '.join(ctx.pairs)}`\n"
            f"  Spot: `{ctx.enable_spot}` | Margin: `{ctx.enable_margin}`\n"
            f"  Watchdog: `{ctx.watchdog is not None}` | "
            f"Reoptimizer: `{ctx.reoptimizer is not None}`"
        )

        coros = [runner.start()]
        if ctx.watchdog is not None:
            coros.append(ctx.watchdog.run_loop())
        if ctx.reoptimizer is not None:
            coros.append(ctx.reoptimizer.run_loop())

        self._tasks = [asyncio.create_task(c) for c in coros]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("[MMOrch] CancelledError — oprire graceful")
        except Exception as exc:
            logger.error("[MMOrch] Exceptie in gather: {}", exc)
            await self._alert(f"\U0001f6a8 *MMOrch eroare*: `{exc}`")
            raise
        finally:
            await self.stop_runner(ctx)

    async def stop_runner(self, ctx: Optional[MultiMarketContext] = None) -> None:
        """
        Oprire graceful: watchdog.stop() + reoptimizer.stop() + cancel tasks.
        """
        ctx = ctx or self._ctx
        logger.info("[MMOrch] stop_runner — oprire graceful")

        if ctx is not None:
            if ctx.watchdog is not None:
                try:
                    ctx.watchdog.stop()
                    logger.info("[MMOrch] Watchdog oprit")
                except Exception as exc:
                    logger.warning("[MMOrch] watchdog.stop() eroare: {}", exc)

            if ctx.reoptimizer is not None:
                try:
                    ctx.reoptimizer.stop()
                    logger.info("[MMOrch] Reoptimizer oprit")
                except Exception as exc:
                    logger.warning("[MMOrch] reoptimizer.stop() eroare: {}", exc)

        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._tasks.clear()
        await self._alert("\u2705 *MultiMarketOrchestrator oprit.*")

    # ─ Builders subsisteme ─────────────────────────────────────────────────────

    async def _build_watchdog(self, ctx: MultiMarketContext):
        """
        Instantiaza MonitoringWatchdog cu metrics_provider cascadat:
          1. RiskManager (daca runner il expune)
          2. PnLTracker (fallback)
          3. Stub fallback (returneaza valori safe)
        """
        from core.monitoring_watchdog import MonitoringWatchdog

        runner = ctx.runner or self._runner
        metrics_provider = self._resolve_metrics_provider(runner)

        halt_callback   = self._make_halt_callback()
        reduce_callback = self._make_reduce_callback()

        watchdog = MonitoringWatchdog.from_env(
            pairs=ctx.pairs,
            metrics_provider=metrics_provider,
            dispatcher=self._dispatcher,
            halt_callback=halt_callback,
            reduce_callback=reduce_callback,
            per_pair_cfg=self._per_pair_wd_cfg,
        )
        logger.info(
            "[MMOrch] Watchdog construit | perechi={} | provider={}",
            len(ctx.pairs), type(metrics_provider).__name__,
        )
        return watchdog

    async def _build_reoptimizer(self, ctx: MultiMarketContext):
        """
        Instantiaza AutoReoptimizer cu BacktestEngine din config.
        Graceful: daca BacktestEngine nu poate fi construit, returneaza None.
        """
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
            engine = self._resolve_backtest_engine()
            reoptimizer = AutoReoptimizer.from_env(
                engine=engine,
                pairs=ctx.pairs,
                notifier_bus=self._bus,
            )
            logger.info(
                "[MMOrch] Reoptimizer construit | perechi={}",
                len(ctx.pairs),
            )
            return reoptimizer
        except Exception as exc:
            logger.warning(
                "[MMOrch] AutoReoptimizer nu a putut fi construit: {} — dezactivat",
                exc,
            )
            return None

    # ─ Metrics provider (cascaded) ────────────────────────────────────────────────

    def _resolve_metrics_provider(self, runner: Any) -> Callable:
        """
        Cascada de providers:
          1. runner.get_pair_metrics(pair)    — RiskManager integrat
          2. runner.risk_manager.get_metrics(pair)  — RiskManager separat
          3. runner.pnl_tracker.get_metrics(pair)   — PnLTracker
          4. stub: returneaza metrici safe (sharpe=99, dd=0, z=0, hl=1, streak=0)
        """
        # 1. get_pair_metrics nativ
        if runner is not None and hasattr(runner, "get_pair_metrics"):
            async def _provider_native(pair: str):
                return await runner.get_pair_metrics(pair)
            return _provider_native

        # 2. risk_manager.get_metrics
        risk_mgr = getattr(runner, "risk_manager", None) if runner else None
        if risk_mgr is not None and hasattr(risk_mgr, "get_metrics"):
            async def _provider_risk(pair: str):
                return await risk_mgr.get_metrics(pair)
            return _provider_risk

        # 3. pnl_tracker
        pnl_tracker = getattr(runner, "pnl_tracker", None) if runner else None
        if pnl_tracker is not None and hasattr(pnl_tracker, "get_metrics"):
            async def _provider_pnl(pair: str):
                return await pnl_tracker.get_metrics(pair)
            return _provider_pnl

        # 4. stub safe
        logger.warning(
            "[MMOrch] metrics_provider: niciun provider real gasit — folosesc stub safe"
        )
        async def _stub(pair: str) -> Dict[str, float]:
            return {
                "sharpe": 99.0,
                "drawdown": 0.0,
                "z_score": 0.0,
                "half_life": 1.0,
                "loss_streak": 0,
            }
        return _stub

    def _resolve_backtest_engine(self):
        """
        Rezolva BacktestEngine din runner_cfg sau din env.
        Graceful: returneaza None daca nu poate construi.
        """
        try:
            from backtest.backtest_engine import BacktestEngine
            cfg = self._runner_cfg
            if cfg is not None:
                return BacktestEngine.from_config(cfg)
            return BacktestEngine.from_env()
        except Exception as exc:
            logger.warning("[MMOrch] BacktestEngine resolve failed: {}", exc)
            return None

    # ─ Callbacks halt / reduce ───────────────────────────────────────────────────────

    def _make_halt_callback(self) -> Callable:
        """
        halt_callback(pair) → api.pairs.halt_pair(pair)
        Fallback: log + alert daca api.pairs nu e disponibil.
        """
        async def _halt(pair: str) -> None:
            try:
                from api.pairs import halt_pair
                await halt_pair(pair)
                logger.warning("[MMOrch] HALT executat pentru {} via api.pairs", pair)
            except ImportError:
                logger.error(
                    "[MMOrch] halt_callback: api.pairs.halt_pair nu e disponibil — "
                    "pereche {} NU a fost oprita!", pair
                )
                await self._alert(
                    f"\U0001f6a8 *HALT FAIL* `{pair}` — api.pairs indisponibil!"
                )
            except Exception as exc:
                logger.error("[MMOrch] halt_callback eroare {}: {}", pair, exc)
                await self._alert(f"\U0001f6a8 *HALT FAIL* `{pair}`: `{exc}`")
        return _halt

    def _make_reduce_callback(self) -> Callable:
        """
        reduce_callback(pair, factor) → api.sizing.reduce_pair_size(pair, factor)
        Fallback: log daca api.sizing nu e disponibil.
        """
        async def _reduce(pair: str, factor: float = 0.5) -> None:
            try:
                from api.sizing import reduce_pair_size
                await reduce_pair_size(pair, factor)
                logger.info(
                    "[MMOrch] REDUCE_SIZE {:.0%} pentru {} via api.sizing",
                    factor, pair,
                )
            except ImportError:
                logger.warning(
                    "[MMOrch] reduce_callback: api.sizing.reduce_pair_size nu e disponibil — "
                    "sizing {} NESCHIMBAT", pair
                )
            except Exception as exc:
                logger.error("[MMOrch] reduce_callback eroare {}: {}", pair, exc)
        return _reduce

    # ─ Proprietati publice ────────────────────────────────────────────────────────────

    @property
    def pairs(self) -> List[str]:
        return list(self._pairs)

    @property
    def watchdog(self):
        return self._ctx.watchdog if self._ctx else None

    @property
    def reoptimizer(self):
        return self._ctx.reoptimizer if self._ctx else None

    # ─ Alert helper ────────────────────────────────────────────────────────────────

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(msg, level="info")
        except Exception as exc:
            logger.warning("[MMOrch] alert failed: {}", exc)


__all__ = ["MultiMarketOrchestrator", "MultiMarketContext"]

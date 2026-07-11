"""
execution/multi_market_runner.py  -  QuantLuna Multi-Market Runner v1.0

Sprint S34 (2026-07-12):
  Leaga toate componentele intr-un singur gather():
    - BybitLiveRunner       (Futures Linear pairs)
    - SpotStrategyRunner    (Spot hodl + DCA)
    - SingleHedgeManager[]  (solo hedges per simbol)
    - CapitalAllocator      (ciclu zilnic 23:55 UTC)
    - InternalTransferManager (watch cooldown)
    - MarginRiskGuard       (monitorizare margin ratio, S35)

  WorkflowOrchestrator.start_runner() va delega aici daca
  runner_cfg.markets include mai mult de un tip de piata.

Usage::

    runner = MultiMarketRunner.from_startup_context(
        ctx, cfg, notifier_bus=bus
    )
    await runner.start()   # blocks until stop()
    runner.stop()
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, List, Optional

from loguru import logger


@dataclass
class MultiMarketConfig:
    """
    Configuratie pentru MultiMarketRunner.

    Campurile reflect runner_cfg existent plus extensii multi-market.
    """
    enable_futures: bool = True
    enable_spot: bool = False
    enable_margin: bool = False
    spot_min_usdt_value: float = 5.0
    capital_profit_take_pct: float = 0.03    # 3% PnL zilnic -> profit take
    capital_reserve_pct: float = 0.10        # 10% din profit -> rezerva
    daily_pnl_db: str = "state/daily_pnl.db"
    transfers_db: str = "state/internal_transfers.db"


class MultiMarketRunner:
    """
    Runner unificat care gestioneaza toate pietele simultan.

    Arhitectura:
        asyncio.gather(
          futures_runner.start(),          # BybitLiveRunner
          spot_runner.run_loop(),          # SpotStrategyRunner (nou)
          *[mgr.manage() for mgr in hedges], # SingleHedgeManager[]
          capital_allocator.run_loop(),    # CapitalAllocator
          margin_risk_guard.watch_loop(),  # MarginRiskGuard (daca margin activ)
        )
    """

    def __init__(
        self,
        futures_runner=None,
        spot_runner=None,
        hedge_managers: Optional[List[Any]] = None,
        capital_allocator=None,
        margin_risk_guard=None,
        notifier_bus=None,
        cfg: Optional[MultiMarketConfig] = None,
    ) -> None:
        self._futures_runner = futures_runner
        self._spot_runner = spot_runner
        self._hedge_managers = hedge_managers or []
        self._capital_allocator = capital_allocator
        self._margin_guard = margin_risk_guard
        self._bus = notifier_bus
        self._cfg = cfg or MultiMarketConfig()
        self._running = False
        self._tasks: List[asyncio.Task] = []

    @classmethod
    def from_startup_context(
        cls,
        ctx,           # StartupContext din WorkflowOrchestrator
        runner_cfg,
        notifier_bus=None,
        futures_runner=None,
        spot_router=None,
        margin_router=None,
    ) -> "MultiMarketRunner":
        """
        Factory: construieste MultiMarketRunner din StartupContext.
        Preia hedge_managers si optimizer din ctx.
        """
        cfg = MultiMarketConfig(
            enable_futures=True,
            enable_spot=getattr(runner_cfg, "enable_spot", False),
            enable_margin=getattr(runner_cfg, "enable_margin", False),
            capital_profit_take_pct=getattr(runner_cfg, "profit_take_pct", 0.03),
            capital_reserve_pct=getattr(runner_cfg, "reserve_pct", 0.10),
        )

        # CapitalAllocator + DailyPnLTracker
        capital_allocator = None
        try:
            from execution.daily_pnl_tracker import DailyPnLTracker
            from execution.capital_allocator import CapitalAllocator, StrategyAllocation
            tracker = DailyPnLTracker(db_path=cfg.daily_pnl_db)
            allocations = [
                StrategyAllocation(
                    name="pairs_futures",
                    target_pct=0.70 if not cfg.enable_spot else 0.55,
                    profit_take_pct=cfg.capital_profit_take_pct,
                ),
            ]
            if cfg.enable_spot:
                allocations.append(StrategyAllocation(
                    name="spot", target_pct=0.20,
                    profit_take_pct=cfg.capital_profit_take_pct * 1.5,
                ))
            if cfg.enable_margin:
                allocations.append(StrategyAllocation(
                    name="margin", target_pct=0.15,
                    profit_take_pct=cfg.capital_profit_take_pct,
                ))
            allocations.append(StrategyAllocation(
                name="reserve", target_pct=0.10, profit_take_pct=999.0,
            ))
            capital_allocator = CapitalAllocator(
                tracker=tracker,
                allocations=allocations,
                notifier_bus=notifier_bus,
            )
        except Exception as exc:
            logger.warning("[MultiMarketRunner] CapitalAllocator init failed: {}", exc)

        # MarginRiskGuard
        margin_guard = None
        if cfg.enable_margin and margin_router is not None:
            try:
                from execution.margin_risk_guard import MarginRiskGuard
                margin_guard = MarginRiskGuard(
                    order_router=margin_router,
                    notifier_bus=notifier_bus,
                )
            except Exception as exc:
                logger.warning(
                    "[MultiMarketRunner] MarginRiskGuard init failed: {}", exc
                )

        return cls(
            futures_runner=futures_runner,
            spot_runner=None,  # SpotStrategyRunner vine in S34.1
            hedge_managers=getattr(ctx, "hedge_managers", []),
            capital_allocator=capital_allocator,
            margin_risk_guard=margin_guard,
            notifier_bus=notifier_bus,
            cfg=cfg,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Porneste toate componentele in paralel. Blocheaza pana la stop()."""
        self._running = True
        logger.info("[MultiMarketRunner] START")
        await self._alert(
            f"\U0001f680 *MultiMarketRunner pornit*\n"
            f"  Futures: {'ON' if self._cfg.enable_futures else 'OFF'}\n"
            f"  Spot:    {'ON' if self._cfg.enable_spot else 'OFF'}\n"
            f"  Margin:  {'ON' if self._cfg.enable_margin else 'OFF'}\n"
            f"  Hedges:  {len(self._hedge_managers)}"
        )

        coros = []

        # Futures runner
        if self._futures_runner is not None and self._cfg.enable_futures:
            coros.append(self._futures_runner.start())
            logger.info("[MultiMarketRunner] Task: BybitLiveRunner (Futures)")

        # Spot runner
        if self._spot_runner is not None and self._cfg.enable_spot:
            coros.append(self._spot_runner.run_loop())
            logger.info("[MultiMarketRunner] Task: SpotStrategyRunner")

        # Hedge managers
        for mgr in self._hedge_managers:
            coros.append(mgr.manage())
            sym = getattr(mgr, "_symbol", "?")
            logger.info("[MultiMarketRunner] Task: HedgeManager {}", sym)

        # Capital allocator
        if self._capital_allocator is not None:
            coros.append(self._capital_allocator.run_loop())
            logger.info("[MultiMarketRunner] Task: CapitalAllocator (zilnic 23:55 UTC)")

        # Margin risk guard
        if self._margin_guard is not None and self._cfg.enable_margin:
            coros.append(self._margin_guard.watch_loop())
            logger.info("[MultiMarketRunner] Task: MarginRiskGuard")

        if not coros:
            logger.warning("[MultiMarketRunner] Nicio componenta activa!")
            return

        tasks = [asyncio.create_task(c) for c in coros]
        self._tasks = tasks

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("[MultiMarketRunner] CancelledError - oprire...")
        except Exception as exc:
            logger.error("[MultiMarketRunner] Eroare critica: {}", exc)
            await self._alert(f"\u274c MultiMarketRunner EROARE: {exc}")
            raise
        finally:
            self._running = False
            logger.info("[MultiMarketRunner] OPRIT")

    def stop(self) -> None:
        """Opreste toate task-urile activ."""
        self._running = False
        if self._futures_runner is not None:
            try:
                self._futures_runner.stop()
            except Exception:
                pass
        if self._capital_allocator is not None:
            try:
                self._capital_allocator.stop()
            except Exception:
                pass
        if self._margin_guard is not None:
            try:
                self._margin_guard.stop()
            except Exception:
                pass
        for mgr in self._hedge_managers:
            try:
                mgr.stop()
            except Exception:
                pass
        for t in self._tasks:
            if not t.done():
                t.cancel()
        logger.info("[MultiMarketRunner] stop() apelat")

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(msg, level="info")
        except Exception:
            pass

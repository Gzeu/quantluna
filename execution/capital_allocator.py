"""
execution/capital_allocator.py  -  QuantLuna Capital Allocator v1.0

Sprint S31 (2026-07-12):
  Gestioneaza alocarea dinamica a capitalului pe strategii.
  Reguli configurabile per strategie: % din equity, profit_take,
  high_watermark scaling, rezerva USDT minima.

  Logica principala (rulata zilnic la 23:55 UTC):
    1. Citeste PnL zilnic din DailyPnLTracker
    2. Daca PnL > profit_take_pct → muta excesul in rezerva
    3. Daca equity > high_watermark * scale_trigger → rebalanceaza
    4. Trimite raport Telegram

Usage::

    allocator = CapitalAllocator.from_env(tracker, notifier_bus)
    await allocator.run_daily_cycle()
    # sau in loop:
    await allocator.run_loop()  # ruleaza zilnic la 23:55 UTC
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, time as dtime
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class StrategyAllocation:
    """
    Configuratia de alocare pentru o strategie.

    Exemplu::
        StrategyAllocation(
            name="pairs_futures",
            target_pct=0.70,         # 70% din equity total
            min_usdt=500.0,          # minim 500 USDT
            max_usdt=50000.0,        # maxim 50k USDT
            profit_take_pct=0.03,    # la +3% PnL zilnic, ia profitul
            high_watermark_scale=1.20,  # la +20% equity, rebalanceaza
        )
    """
    name: str
    target_pct: float = 0.70          # % din equity total alocat acestei strategii
    min_usdt: float = 100.0
    max_usdt: float = 100_000.0
    profit_take_pct: float = 0.03     # 3% PnL zilnic → ia profitul
    high_watermark_scale: float = 1.20  # +20% equity → rebalanceaza
    reserve_pct: float = 0.10         # 10% din profit → rezerva


@dataclass
class AllocationDecision:
    strategy: str
    action: str           # "PROFIT_TAKE" | "REBALANCE" | "HOLD" | "SCALE_UP"
    amount_usdt: float
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DailyCycleResult:
    date: str
    total_equity_usdt: float
    realised_pnl_usdt: float
    decisions: List[AllocationDecision] = field(default_factory=list)
    moved_to_reserve_usdt: float = 0.0
    telegram_msg: str = ""


class CapitalAllocator:
    """
    Aloca si rebalanceaza capital pe strategii bazat pe PnL zilnic.

    Integrat in MultiMarketRunner (S34) ca task separat.
    Poate rula standalone cu run_loop() pentru ciclu zilnic automat.
    """

    _DAILY_CYCLE_HOUR_UTC = 23
    _DAILY_CYCLE_MINUTE_UTC = 55

    def __init__(
        self,
        tracker,  # DailyPnLTracker
        allocations: List[StrategyAllocation],
        notifier_bus=None,
        internal_transfer_manager=None,  # InternalTransferManager (S32)
        reserve_min_usdt: float = 200.0,
    ) -> None:
        self._tracker = tracker
        self._allocations = {a.name: a for a in allocations}
        self._bus = notifier_bus
        self._transfer_mgr = internal_transfer_manager
        self._reserve_min_usdt = reserve_min_usdt
        self._high_watermarks: Dict[str, float] = {}
        self._running = False

    @classmethod
    def from_env(
        cls,
        tracker,
        notifier_bus=None,
        allocations: Optional[List[StrategyAllocation]] = None,
    ) -> "CapitalAllocator":
        if allocations is None:
            allocations = [
                StrategyAllocation(
                    name="pairs_futures",
                    target_pct=0.70,
                    profit_take_pct=0.03,
                ),
                StrategyAllocation(
                    name="spot_hodl",
                    target_pct=0.20,
                    profit_take_pct=0.05,
                ),
                StrategyAllocation(
                    name="reserve",
                    target_pct=0.10,
                    profit_take_pct=999.0,  # rezerva nu se atinge
                ),
            ]
        return cls(tracker=tracker, allocations=allocations, notifier_bus=notifier_bus)

    # ------------------------------------------------------------------
    # Ciclu zilnic
    # ------------------------------------------------------------------

    async def run_loop(self) -> None:
        """Loop infinit: ruleaza daily_cycle zilnic la 23:55 UTC."""
        self._running = True
        logger.info(
            "[CapitalAllocator] Loop pornit — ciclu zilnic la {:02d}:{:02d} UTC",
            self._DAILY_CYCLE_HOUR_UTC, self._DAILY_CYCLE_MINUTE_UTC,
        )
        while self._running:
            try:
                await self._wait_until_daily_cycle()
                if not self._running:
                    break
                result = await self.run_daily_cycle()
                logger.info(
                    "[CapitalAllocator] Ciclu zilnic completat: {} decizii, "
                    "rezerva primita: {:.2f} USDT",
                    len(result.decisions), result.moved_to_reserve_usdt,
                )
                await asyncio.sleep(70)  # evita dublu-trigger
            except asyncio.CancelledError:
                logger.info("[CapitalAllocator] Loop cancelled")
                return
            except Exception as exc:
                logger.error("[CapitalAllocator] Loop error: {}", exc)
                await asyncio.sleep(300)

    def stop(self) -> None:
        self._running = False

    async def run_daily_cycle(self) -> DailyCycleResult:
        """Executa un ciclu de alocare: citeste PnL, ia decizii, notifica."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info("[CapitalAllocator] Ciclu zilnic {}", today)

        # 1. PnL zilnic si equity total
        try:
            summary = await self._tracker.get_daily_summary(today)
        except Exception as exc:
            logger.error("[CapitalAllocator] get_daily_summary failed: {}", exc)
            summary = {"total_equity_usdt": 0.0, "realised_pnl_usdt": 0.0}

        total_equity = float(summary.get("total_equity_usdt", 0.0))
        realised_pnl = float(summary.get("realised_pnl_usdt", 0.0))
        pnl_pct = (realised_pnl / total_equity) if total_equity > 0 else 0.0

        decisions: List[AllocationDecision] = []
        moved_to_reserve = 0.0

        for name, alloc in self._allocations.items():
            if name == "reserve":
                continue

            allocated_usdt = total_equity * alloc.target_pct
            hwm = self._high_watermarks.get(name, allocated_usdt)

            # Profit take
            if pnl_pct >= alloc.profit_take_pct and realised_pnl > 0:
                excess = realised_pnl * alloc.reserve_pct
                moved_to_reserve += excess
                decisions.append(AllocationDecision(
                    strategy=name,
                    action="PROFIT_TAKE",
                    amount_usdt=excess,
                    reason=(
                        f"PnL={pnl_pct:.1%} >= threshold={alloc.profit_take_pct:.1%}"
                        f" | muta {excess:.2f} USDT in rezerva"
                    ),
                ))
                logger.info(
                    "[CapitalAllocator] PROFIT_TAKE {} : {:.2f} USDT -> rezerva",
                    name, excess,
                )
                # Executa transfer daca InternalTransferManager disponibil
                if self._transfer_mgr is not None and excess > 1.0:
                    try:
                        await self._transfer_mgr.futures_to_spot(excess)
                    except Exception as e:
                        logger.warning(
                            "[CapitalAllocator] transfer_to_reserve failed: {}", e
                        )

            # High watermark scale
            if allocated_usdt > hwm * alloc.high_watermark_scale:
                decisions.append(AllocationDecision(
                    strategy=name,
                    action="SCALE_UP",
                    amount_usdt=allocated_usdt - hwm,
                    reason=(
                        f"Equity crescut cu >{(alloc.high_watermark_scale-1):.0%} "
                        f"fata de HWM {hwm:.0f} USDT"
                    ),
                ))
                self._high_watermarks[name] = allocated_usdt
                logger.info(
                    "[CapitalAllocator] SCALE_UP {}: nou HWM={:.0f} USDT",
                    name, allocated_usdt,
                )

            if not decisions or decisions[-1].strategy != name:
                decisions.append(AllocationDecision(
                    strategy=name, action="HOLD",
                    amount_usdt=allocated_usdt,
                    reason="PnL in target, fara actiune",
                ))

        # Construieste mesaj Telegram
        telegram_msg = self._build_telegram_msg(
            today, total_equity, realised_pnl, pnl_pct,
            decisions, moved_to_reserve,
        )
        result = DailyCycleResult(
            date=today,
            total_equity_usdt=total_equity,
            realised_pnl_usdt=realised_pnl,
            decisions=decisions,
            moved_to_reserve_usdt=moved_to_reserve,
            telegram_msg=telegram_msg,
        )

        # Notificare
        await self._alert(telegram_msg)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _wait_until_daily_cycle(self) -> None:
        """Asteapta pana la urmatorul 23:55 UTC."""
        while self._running:
            now = datetime.now(timezone.utc)
            target = now.replace(
                hour=self._DAILY_CYCLE_HOUR_UTC,
                minute=self._DAILY_CYCLE_MINUTE_UTC,
                second=0, microsecond=0,
            )
            if now >= target:
                import datetime as dt
                target += dt.timedelta(days=1)
            wait_s = (target - now).total_seconds()
            logger.debug(
                "[CapitalAllocator] Urmatorul ciclu in {:.0f}s ({} UTC)",
                wait_s, target.strftime("%Y-%m-%d %H:%M"),
            )
            try:
                await asyncio.sleep(min(wait_s, 60))  # poll la fiecare minut
            except asyncio.CancelledError:
                return
            # Verifica daca suntem in fereastra
            now = datetime.now(timezone.utc)
            if (
                now.hour == self._DAILY_CYCLE_HOUR_UTC
                and now.minute == self._DAILY_CYCLE_MINUTE_UTC
            ):
                return

    def _build_telegram_msg(
        self,
        date: str,
        equity: float,
        pnl: float,
        pnl_pct: float,
        decisions: List[AllocationDecision],
        moved_to_reserve: float,
    ) -> str:
        emoji = "✅" if pnl >= 0 else "❌"
        lines = [
            f"{emoji} *Raport zilnic {date}*",
            f"💰 Equity total: `{equity:,.2f} USDT`",
            f"📈 PnL realizat: `{pnl:+.2f} USDT` ({pnl_pct:+.2%})",
        ]
        if moved_to_reserve > 0:
            lines.append(f"🏦 Mutat in rezerva: `{moved_to_reserve:.2f} USDT`")

        actions = [d for d in decisions if d.action != "HOLD"]
        if actions:
            lines.append("\n🔄 *Actiuni:*")
            for d in actions:
                lines.append(f"  • `{d.strategy}` [{d.action}]: {d.reason}")
        else:
            lines.append("\u23f8️ Fara actiuni — toate strategiile in target")
        return "\n".join(lines)

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(msg, level="info")
        except Exception as exc:
            logger.warning("[CapitalAllocator] alert failed: {}", exc)

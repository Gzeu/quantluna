"""
QuantLuna — Integration Loop (Sprint 19 + Sprint 28)

Sprint 28 additions:
  #5  FundingMonitor gate — blocks entry when |funding_net_ann| exceeds
      cfg.funding_gate_max_net_ann. Reads _last_y/_last_x directly from
      the FundingMonitor instance (non-blocking, uses last polled value).
      gate_blocked_by includes 'funding_gate'; CycleResult.funding_blocked=True.

  #6  Partial exit — two-stage exit instead of one full exit:
        Stage 1: |z| <= partial_exit_zscore (default 1.0)
                 Close partial_exit_pct (default 50%) of position.
        Stage 2: |z| <= exit_zscore (default 0.5)
                 Close remaining qty.
      State tracked via _remaining_qty and _partial_done.

  reset_cycle() — resets _in_position/_entry_side/_remaining_qty/_partial_done
      without touching Kalman/SpreadMonitor warmup or CircuitBreaker counters.
      Called by BybitLiveRunner.start_new_cycle() after external close.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


@dataclass
class BarData:
    symbol_y:  str
    symbol_x:  str
    price_y:   float
    price_x:   float
    timestamp: float = field(default_factory=time.time)


@dataclass
class CycleResult:
    bar_idx:         int
    zscore:          float
    gate_allowed:    bool
    gate_blocked_by: List[str]
    order_submitted: bool
    spread_healthy:  bool
    size_multiplier: float
    duration_ms:     float
    partial_exit:    bool = False   # Sprint 28 #6
    funding_blocked: bool = False   # Sprint 28 #5


@dataclass
class IntegrationLoopConfig:
    symbol_y: str = "BTCUSDT"
    symbol_x: str = "ETHUSDT"
    venue:    str = "bybit"

    entry_zscore:        float = 2.0
    exit_zscore:         float = 0.5
    partial_exit_zscore: float = 1.0    # Sprint 28 #6
    partial_exit_pct:    float = 0.50   # Sprint 28 #6
    base_qty:            float = 0.001

    dry_run:        bool  = True
    bar_interval_s: float = 0.0
    max_bars:       int   = 1000

    # Sprint 28 #5: None = disabled
    funding_gate_max_net_ann: Optional[float] = None


class IntegrationLoop:
    """
    End-to-end integration loop:
    Kalman → SpreadMonitor → RegimeFilter → [FundingGate]
    → OrderManager (entry / partial-exit / full-exit)
    → NotifierBus

    Sprint 28:
      - funding_monitor: optional FundingMonitor; gate blocks entry
      - two-stage partial exit (50% at z=1.0, rest at z=0.5)
      - reset_cycle(): safe position reset for external-trade restart
    """

    def __init__(
        self,
        cfg: Optional[IntegrationLoopConfig] = None,
        kalman=None,
        spread_monitor=None,
        regime_filter=None,
        order_manager=None,
        notifier_bus=None,
        funding_monitor=None,   # Sprint 28 #5
    ) -> None:
        self.cfg              = cfg or IntegrationLoopConfig()
        self._kalman          = kalman
        self._spread_monitor  = spread_monitor
        self._regime_filter   = regime_filter
        self._order_manager   = order_manager
        self._notifier_bus    = notifier_bus
        self._funding_monitor = funding_monitor  # Sprint 28 #5

        self._bar_idx        = 0
        self._in_position    = False
        self._entry_side:    Optional[str] = None
        self._remaining_qty: float         = 0.0   # Sprint 28 #6
        self._partial_done:  bool          = False  # Sprint 28 #6
        self._results:       List[CycleResult] = []

    # ------------------------------------------------------------------
    # Sprint 28: reset_cycle
    # ------------------------------------------------------------------

    def reset_cycle(self) -> None:
        """
        Reset position state without touching Kalman/SpreadMonitor warmup
        or CircuitBreaker counters.
        Called after an external trade closes so the next bar can enter fresh.
        """
        logger.info(
            f"IntegrationLoop.reset_cycle: clearing "
            f"in_position={self._in_position} side={self._entry_side}"
        )
        self._in_position    = False
        self._entry_side     = None
        self._remaining_qty  = 0.0
        self._partial_done   = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_synthetic(self, bars: List[BarData]) -> List[CycleResult]:
        self._results.clear()
        self._bar_idx = 0
        for bar in bars:
            result = await self._process_bar(bar)
            self._results.append(result)
            if self.cfg.bar_interval_s > 0:
                await asyncio.sleep(self.cfg.bar_interval_s)
        logger.info(
            f"IntegrationLoop: {len(self._results)} bars | "
            f"orders={sum(r.order_submitted for r in self._results)} | "
            f"partials={sum(r.partial_exit for r in self._results)} | "
            f"funding_blocks={sum(r.funding_blocked for r in self._results)}"
        )
        return self._results

    @property
    def results(self) -> List[CycleResult]:
        return list(self._results)

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    async def _process_bar(self, bar: BarData) -> CycleResult:
        t0 = time.perf_counter()

        # 1. Kalman update
        zscore        = 0.0
        half_life     = 24.0
        kalman_p_diag = 0.0
        spread_val    = bar.price_y - bar.price_x
        if self._kalman is not None:
            try:
                self._kalman.update(bar.price_y, bar.price_x)
                zscore        = float(getattr(self._kalman, "zscore",    0.0))
                half_life     = float(getattr(self._kalman, "half_life", 24.0))
                kalman_p_diag = float(getattr(self._kalman, "p_diag",   0.0))
                spread_val    = float(getattr(self._kalman, "spread",    spread_val))
            except Exception as exc:
                logger.warning(f"Kalman error: {exc}")

        # 2. SpreadMonitor
        spread_report  = None
        spread_healthy = True
        if self._spread_monitor is not None:
            spread_report  = self._spread_monitor.update(
                spread=spread_val, zscore=zscore,
                half_life=half_life, kalman_p_diag=kalman_p_diag,
            )
            spread_healthy = spread_report.healthy

        # 3. RegimeFilter
        gate_allowed    = True
        gate_blocked_by: List[str] = []
        size_multiplier = 1.0
        if self._regime_filter is not None:
            gate = self._regime_filter.check(
                ltf_zscore=zscore,
                spread_report=spread_report,
            )
            gate_allowed    = gate.allowed
            gate_blocked_by = list(gate.blocked_by)
            size_multiplier = gate.size_multiplier

        # Sprint 28 #5: FundingMonitor gate
        funding_blocked = False
        max_net = self.cfg.funding_gate_max_net_ann
        if gate_allowed and max_net is not None and self._funding_monitor is not None:
            funding_net = abs(
                getattr(self._funding_monitor, "_last_y", 0.0)
                - getattr(self._funding_monitor, "_last_x", 0.0)
            )
            if funding_net > max_net:
                gate_allowed = False
                funding_blocked = True
                gate_blocked_by.append("funding_gate")
                logger.debug(
                    f"[FundingGate] blocked: net_ann={funding_net:.4f} "
                    f"(max={max_net:.4f})"
                )

        # 4. Entry / partial-exit / full-exit
        order_submitted = False
        partial_exit    = False
        cfg             = self.cfg

        if gate_allowed:
            if not self._in_position and abs(zscore) >= cfg.entry_zscore:
                # --- Entry ---
                side = "SELL" if zscore > 0 else "BUY"
                qty  = cfg.base_qty * size_multiplier
                ok   = await self._submit_order(
                    symbol=cfg.symbol_y, side=side, qty=qty
                )
                if ok:
                    self._in_position    = True
                    self._entry_side     = side
                    self._remaining_qty  = qty
                    self._partial_done   = False
                    order_submitted      = True
                    if self._notifier_bus is not None:
                        try:
                            await self._notifier_bus.send_entry_signal(
                                symbol=cfg.symbol_y,
                                side=side,
                                zscore=zscore,
                                venue=cfg.venue,
                            )
                        except Exception:
                            pass

            elif self._in_position:
                abs_z     = abs(zscore)
                exit_side = "BUY" if self._entry_side == "SELL" else "SELL"

                # Sprint 28 #6: Stage 1 — partial exit
                if (
                    not self._partial_done
                    and abs_z <= cfg.partial_exit_zscore
                    and abs_z > cfg.exit_zscore
                ):
                    partial_qty = self._remaining_qty * cfg.partial_exit_pct
                    ok = await self._submit_order(
                        symbol=cfg.symbol_y, side=exit_side, qty=partial_qty
                    )
                    if ok:
                        self._remaining_qty *= (1.0 - cfg.partial_exit_pct)
                        self._partial_done   = True
                        partial_exit         = True
                        order_submitted      = True
                        logger.info(
                            f"[PartialExit] {cfg.partial_exit_pct:.0%} closed | "
                            f"z={zscore:.3f} remaining={self._remaining_qty:.6f}"
                        )

                # Stage 2 — full exit
                elif abs_z <= cfg.exit_zscore:
                    close_qty = self._remaining_qty if self._remaining_qty > 0 else cfg.base_qty
                    ok = await self._submit_order(
                        symbol=cfg.symbol_y, side=exit_side, qty=close_qty
                    )
                    if ok:
                        self._in_position    = False
                        self._entry_side     = None
                        self._remaining_qty  = 0.0
                        self._partial_done   = False
                        order_submitted      = True

        self._bar_idx += 1
        duration_ms = (time.perf_counter() - t0) * 1000

        return CycleResult(
            bar_idx=self._bar_idx,
            zscore=zscore,
            gate_allowed=gate_allowed,
            gate_blocked_by=gate_blocked_by,
            order_submitted=order_submitted,
            spread_healthy=spread_healthy,
            size_multiplier=size_multiplier,
            duration_ms=duration_ms,
            partial_exit=partial_exit,
            funding_blocked=funding_blocked,
        )

    async def _submit_order(self, symbol: str, side: str, qty: float) -> bool:
        if self.cfg.dry_run:
            logger.info(f"[DRY RUN] bar={self._bar_idx} {side} {qty:.6f} {symbol}")
            return True
        if self._order_manager is None:
            return False
        try:
            from execution.order_manager import OrderRequest
            req = OrderRequest(
                venue=self.cfg.venue,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="MARKET",
                tag="bot_entry",
            )
            await self._order_manager.submit(req)
            return True
        except Exception as exc:
            logger.error(f"Order submit failed: {exc}")
            return False

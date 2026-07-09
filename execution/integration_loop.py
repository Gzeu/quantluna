"""
QuantLuna — Integration Loop (Sprint 19 + Sprint 28)

Loop-ul de integrare end-to-end care leagă toate componentele S17-S18
within un singur ciclu asincron. Acesta NU este live_trader.py —
este un harness de validare care poate fi rulat în paper mode sau
test mode fără conexiuni reale la exchange.

Fluxul unui ciclu:
  1. Fetch bar nou (mock sau real)
  2. Update Kalman + spread z-score
  3. Update SpreadMonitor → SpreadHealthReport
  4. Check RegimeFilter (CB + VolRegime + MTF + Spread)
  5. Dacă gate.allowed → submit OrderRequest via OrderManager
  6. Record PnL → CircuitBreaker
  7. NotifierBus → trimite semnale

Sprint 28 additions:
  reset_cycle() — resets position state (_in_position, _entry_side, _bar_idx)
    without touching the Kalman/SpreadMonitor warmup state.
    Called by BybitLiveRunner.start_new_cycle() after an external trade closes
    so the bot can immediately look for new entries on the next bar.

Usage:
    loop = IntegrationLoop(IntegrationLoopConfig(dry_run=True))
    await loop.run(n_bars=100)

    # Sprint 28: after external trade closes
    loop.reset_cycle()
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


@dataclass
class BarData:
    """Minimal bar structure pentru integration loop."""
    symbol_y:   str
    symbol_x:   str
    price_y:    float
    price_x:    float
    timestamp:  float = field(default_factory=time.time)


@dataclass
class CycleResult:
    """Rezultatul unui ciclu de integrare."""
    bar_idx:         int
    zscore:          float
    gate_allowed:    bool
    gate_blocked_by: List[str]
    order_submitted: bool
    spread_healthy:  bool
    size_multiplier: float
    duration_ms:     float


@dataclass
class IntegrationLoopConfig:
    # Pair
    symbol_y: str = "BTCUSDT"
    symbol_x: str = "ETHUSDT"
    venue:    str = "bybit"

    # Thresholds (default paper-friendly)
    entry_zscore:  float = 2.0
    exit_zscore:   float = 0.5
    base_qty:      float = 0.001

    # Dry run: no real orders
    dry_run: bool = True

    # Timing
    bar_interval_s: float = 0.0  # 0 = as fast as possible (test mode)

    # Max cycles before stop
    max_bars: int = 1000


class IntegrationLoop:
    """
    End-to-end integration loop: Kalman → SpreadMonitor → RegimeFilter
    → OrderManager → CircuitBreaker → NotifierBus.

    All components are optional — pass None to skip.
    In test mode, inject synthetic bars via run_synthetic().

    Sprint 28: reset_cycle() resets position state so the loop can
    immediately look for new entries after an external trade closes.
    """

    def __init__(
        self,
        cfg: Optional[IntegrationLoopConfig] = None,
        kalman=None,
        spread_monitor=None,
        regime_filter=None,
        order_manager=None,
        notifier_bus=None,
    ) -> None:
        self.cfg            = cfg or IntegrationLoopConfig()
        self._kalman        = kalman
        self._spread_monitor = spread_monitor
        self._regime_filter = regime_filter
        self._order_manager = order_manager
        self._notifier_bus  = notifier_bus

        self._bar_idx    = 0
        self._in_position = False
        self._entry_side: Optional[str] = None
        self._results: List[CycleResult] = []

    # ------------------------------------------------------------------
    # Sprint 28: cycle reset
    # ------------------------------------------------------------------

    def reset_cycle(self) -> None:
        """
        Reset position tracking state without disturbing Kalman / SpreadMonitor
        warmup data.

        Called by BybitLiveRunner.start_new_cycle() after an external position
        closes so the next bar can trigger a fresh entry signal immediately.

        What is reset:
          - _in_position → False
          - _entry_side  → None
          - _bar_idx     → kept (continuous bar counter, not reset)
          - _results     → kept (history preserved)

        What is NOT reset (intentionally):
          - Kalman state (spread / hedge ratio / half-life warmup)
          - SpreadMonitor warmup bars
          - CircuitBreaker loss counters (risk management must persist)
        """
        was_in_position = self._in_position
        was_side        = self._entry_side
        self._in_position = False
        self._entry_side  = None
        logger.info(
            f"IntegrationLoop.reset_cycle: position state cleared "
            f"(was_in_position={was_in_position} was_side={was_side}). "
            f"Ready for new entries on next bar."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_synthetic(
        self,
        bars: List[BarData],
    ) -> List[CycleResult]:
        """
        Run the loop on a pre-built list of synthetic bars.
        Returns all CycleResult records.
        """
        self._results.clear()
        self._bar_idx = 0

        for bar in bars:
            result = await self._process_bar(bar)
            self._results.append(result)
            self._bar_idx += 1
            if self.cfg.bar_interval_s > 0:
                await asyncio.sleep(self.cfg.bar_interval_s)

        logger.info(
            f"IntegrationLoop: completed {len(self._results)} bars "
            f"| orders={sum(r.order_submitted for r in self._results)} "
            f"| gate_blocks={sum(not r.gate_allowed for r in self._results)}"
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

        # 1. Kalman update → hedge ratio + spread
        zscore = 0.0
        half_life = 24.0
        kalman_p_diag = 0.0
        spread_val = bar.price_y - bar.price_x  # fallback: simple spread

        if self._kalman is not None:
            try:
                self._kalman.update(bar.price_y, bar.price_x)
                zscore        = float(getattr(self._kalman, "zscore", 0.0))
                half_life     = float(getattr(self._kalman, "half_life", 24.0))
                kalman_p_diag = float(getattr(self._kalman, "p_diag", 0.0))
                spread_val    = float(getattr(self._kalman, "spread", spread_val))
            except Exception as exc:
                logger.warning(f"IntegrationLoop: Kalman update error: {exc}")

        # 2. SpreadMonitor update
        spread_report = None
        spread_healthy = True
        if self._spread_monitor is not None:
            spread_report  = self._spread_monitor.update(
                spread=spread_val, zscore=zscore,
                half_life=half_life, kalman_p_diag=kalman_p_diag,
            )
            spread_healthy = spread_report.healthy

        # 3. RegimeFilter check
        gate_allowed    = True
        gate_blocked_by: List[str] = []
        size_multiplier = 1.0
        if self._regime_filter is not None:
            gate = self._regime_filter.check(
                ltf_zscore=zscore,
                spread_report=spread_report,
            )
            gate_allowed    = gate.allowed
            gate_blocked_by = gate.blocked_by
            size_multiplier = gate.size_multiplier

        # 4. Entry/exit logic
        order_submitted = False
        cfg = self.cfg

        if gate_allowed:
            if not self._in_position and abs(zscore) >= cfg.entry_zscore:
                side = "SELL" if zscore > 0 else "BUY"
                qty  = cfg.base_qty * size_multiplier
                order_submitted = await self._submit_order(
                    symbol=cfg.symbol_y, side=side, qty=qty
                )
                if order_submitted:
                    self._in_position = True
                    self._entry_side  = side
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

            elif self._in_position and abs(zscore) <= cfg.exit_zscore:
                exit_side = "BUY" if self._entry_side == "SELL" else "SELL"
                order_submitted = await self._submit_order(
                    symbol=cfg.symbol_y, side=exit_side, qty=cfg.base_qty
                )
                if order_submitted:
                    self._in_position = False
                    self._entry_side  = None

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
        )

    async def _submit_order(
        self, symbol: str, side: str, qty: float
    ) -> bool:
        """Submit order via OrderManager or dry-run log."""
        if self.cfg.dry_run:
            logger.info(
                f"[DRY RUN] bar={self._bar_idx} {side} {qty:.6f} {symbol}"
            )
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
            logger.error(f"IntegrationLoop: order submit failed: {exc}")
            return False

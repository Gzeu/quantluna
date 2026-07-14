"""
execution/capital_manager.py — Automated profit sweep + compounding (Sprint 48).

Manages the profit lifecycle:
  1. Monitor daily PnL via DailyPnLTracker
  2. When PnL exceeds sweep_threshold_pct:
     - sweep_fraction → spot wallet (protection)
     - compound_fraction → reinvest into trading capital
  3. Maintain a reserve target in spot for downside protection

Answers the key question: "Should profit be reinvested or moved to spot?"
Answer: BOTH, in a configurable split (default: 50% spot / 50% compound).

Usage::

    cfg = CapitalManagerConfig()
    mgr = CapitalManager(cfg, transfer_mgr, pnl_tracker)

    # In daily cycle:
    result = await mgr.evaluate()
    # result.swept_to_spot, result.compounded, result.reserve_balance
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class CapitalManagerConfig:
    """Profit management configuration — override via env or constructor."""

    # ── Profit sweep ───────────────────────────────────────────────────────
    profit_sweep_enabled: bool = True
    sweep_threshold_pct: float = 0.03      # sweep when daily PnL > 3%
    sweep_fraction: float = 0.50           # move 50% of excess to spot
    min_sweep_usdt: float = 10.0           # minimum sweep amount

    # ── Compounding ────────────────────────────────────────────────────────
    compound_enabled: bool = True
    compound_fraction: float = 0.50        # reinvest 50% of remaining profit
    max_compound_per_cycle: float = 0.10   # never compound >10% of equity

    # ── Reserve ────────────────────────────────────────────────────────────
    reserve_target_pct: float = 0.20       # aim for 20% in reserve/spot
    reserve_min_usdt: float = 200.0

    # ── Cycle ──────────────────────────────────────────────────────────────
    daily_cycle_hour_utc: int = 23
    daily_cycle_minute_utc: int = 55

    enabled: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# Results
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SweepResult:
    """Output of one evaluation cycle."""

    swept_to_spot: float = 0.0
    compounded: float = 0.0
    reserve_balance: float = 0.0
    futures_balance: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    action: str = "HOLD"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "swept_to_spot": self.swept_to_spot,
            "compounded": self.compounded,
            "reserve_balance": self.reserve_balance,
            "futures_balance": self.futures_balance,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": self.daily_pnl_pct,
            "action": self.action,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CapitalManager
# ═══════════════════════════════════════════════════════════════════════════════


class CapitalManager:
    """
    Automated profit sweep to spot wallet + profit compounding.

    Integrates with InternalTransferManager for Bybit internal transfers
    and DailyPnLTracker for PnL reporting.
    """

    def __init__(
        self,
        cfg: Optional[CapitalManagerConfig] = None,
        transfer_mgr=None,        # InternalTransferManager (S32)
        pnl_tracker=None,         # DailyPnLTracker
        notifier_bus=None,
        initial_capital: float = 0.0,
    ) -> None:
        self._cfg = cfg or CapitalManagerConfig()
        self._transfer_mgr = transfer_mgr
        self._pnl_tracker = pnl_tracker
        self._bus = notifier_bus
        self._initial_capital = initial_capital
        self._compounded_total: float = 0.0
        self._swept_total: float = 0.0
        self._last_result: Optional[SweepResult] = None
        self._running: bool = False

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def last_result(self) -> Optional[SweepResult]:
        return self._last_result

    async def evaluate(self) -> SweepResult:
        """
        Run one evaluation cycle.  Checks PnL and decides whether to
        sweep profit to spot, compound into trading capital, or hold.
        """
        if not self._cfg.enabled:
            return SweepResult(action="DISABLED")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Get daily PnL
        daily_pnl = 0.0
        total_equity = self._initial_capital
        try:
            if self._pnl_tracker is not None:
                summary = await self._pnl_tracker.get_daily_summary(today)
                daily_pnl = float(summary.get("realised_pnl_usdt", 0.0))
                total_equity = float(summary.get("total_equity_usdt", self._initial_capital))
        except Exception as exc:
            logger.warning("[CapitalManager] PnL fetch failed: {}", exc)

        pnl_pct = (daily_pnl / total_equity) if total_equity > 0 else 0.0
        swept = 0.0
        compounded = 0.0

        if daily_pnl > 0 and pnl_pct >= self._cfg.sweep_threshold_pct:
            excess = daily_pnl - (total_equity * self._cfg.sweep_threshold_pct)
            if excess > 0:
                # Split excess: sweep_fraction → spot, remainder → assess for compounding
                sweep_amount = excess * self._cfg.sweep_fraction
                compound_amount = excess * self._cfg.compound_fraction

                # Cap compound to max_compound_per_cycle
                max_compound = total_equity * self._cfg.max_compound_per_cycle
                compound_amount = min(compound_amount, max_compound)

                # Execute sweep (if transfer manager available)
                if sweep_amount >= self._cfg.min_sweep_usdt and self._transfer_mgr is not None:
                    try:
                        await self._transfer_mgr.futures_to_spot(
                            amount_usdt=sweep_amount,
                            reason=f"daily_profit_sweep_{today}",
                        )
                        swept = sweep_amount
                        self._swept_total += swept
                        logger.info(
                            "[CapitalManager] Swept {:.2f} USDT to spot ({} total)",
                            swept, self._swept_total,
                        )
                    except Exception as exc:
                        logger.warning("[CapitalManager] Sweep transfer failed: {}", exc)

                # Track compounding (applied by adjusting initial_capital in runner)
                if self._cfg.compound_enabled and compound_amount > 0:
                    compounded = compound_amount
                    self._compounded_total += compounded
                    logger.info(
                        "[CapitalManager] Compounding {:.2f} USDT ({} total)",
                        compounded, self._compounded_total,
                    )

                action = "SWEEP_AND_COMPOUND"
            else:
                action = "BELOW_THRESHOLD"
        elif daily_pnl < 0:
            action = "LOSS_DAY"
        else:
            action = "HOLD"

        # Get current reserve balance
        reserve_balance = 0.0
        try:
            if self._transfer_mgr is not None:
                # Spot balance approximation
                reserve_balance = self._swept_total
        except Exception:
            pass

        result = SweepResult(
            swept_to_spot=swept,
            compounded=compounded,
            reserve_balance=reserve_balance,
            futures_balance=total_equity - swept,
            daily_pnl=daily_pnl,
            daily_pnl_pct=pnl_pct,
            action=action,
        )
        self._last_result = result

        # Notify
        if action in ("SWEEP_AND_COMPOUND",) and self._bus is not None:
            msg = (
                f"💰 *CapitalManager* {today}\n"
                f"PNL: {daily_pnl:+.2f} USDT ({pnl_pct:+.2%})\n"
                f"→ Spot: {swept:.2f} USDT\n"
                f"→ Compound: {compounded:.2f} USDT"
            )
            try:
                await self._bus.send_alert(msg, level="info")
            except Exception:
                pass

        return result

    # ── Loop ────────────────────────────────────────────────────────────────

    async def run_loop(self) -> None:
        """Run daily evaluation cycle automatically."""
        self._running = True
        logger.info(
            "[CapitalManager] Loop started — daily cycle at {:02d}:{:02d} UTC",
            self._cfg.daily_cycle_hour_utc,
            self._cfg.daily_cycle_minute_utc,
        )
        while self._running:
            try:
                await self._wait_until_cycle()
                if not self._running:
                    break
                await self.evaluate()
                await asyncio.sleep(70)  # avoid double-trigger
            except asyncio.CancelledError:
                logger.info("[CapitalManager] Loop cancelled")
                return
            except Exception as exc:
                logger.error("[CapitalManager] Loop error: {}", exc)
                await asyncio.sleep(300)

    def stop(self) -> None:
        self._running = False

    def snapshot(self) -> dict:
        """Return state for API/dashboard."""
        return {
            "swept_total": self._swept_total,
            "compounded_total": self._compounded_total,
            "initial_capital": self._initial_capital,
            "effective_capital": self._initial_capital + self._compounded_total,
            "last_result": self._last_result.as_dict() if self._last_result else None,
            "config": {
                "sweep_threshold_pct": self._cfg.sweep_threshold_pct,
                "sweep_fraction": self._cfg.sweep_fraction,
                "compound_fraction": self._cfg.compound_fraction,
                "reserve_target_pct": self._cfg.reserve_target_pct,
            },
        }

    # ── Internal ────────────────────────────────────────────────────────────

    async def _wait_until_cycle(self) -> None:
        """Sleep until the daily cycle time."""
        while self._running:
            now = datetime.now(timezone.utc)
            target = now.replace(
                hour=self._cfg.daily_cycle_hour_utc,
                minute=self._cfg.daily_cycle_minute_utc,
                second=0, microsecond=0,
            )
            if now >= target:
                import datetime as dt
                target += dt.timedelta(days=1)
            wait_s = (target - now).total_seconds()
            try:
                await asyncio.sleep(min(wait_s, 60))
            except asyncio.CancelledError:
                return
            now = datetime.now(timezone.utc)
            if (
                now.hour == self._cfg.daily_cycle_hour_utc
                and now.minute == self._cfg.daily_cycle_minute_utc
            ):
                return

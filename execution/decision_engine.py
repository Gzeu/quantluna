"""
execution/decision_engine.py  —  QuantLuna DecisionEngine v2.0

Sprint S45 (2026-07-12):
  Integreaza SizingEngine in pipeline-ul de decizie.
  Inainte de ENTER, apeleaza SizingEngine.compute_qty() cu:
    - streak curent din ProfitStreakTracker
    - drawdown curent din WatchdogMetrics / PnLTracker
    - ATR din spread monitor sau parametru extern
    - equity din CapitalAllocator / PnLTracker

  Output SignalDecision include:
    - action: ENTER | EXIT | HOLD | PARTIAL_EXIT | SCALE_IN
    - qty_y, qty_x: cantitati calculate de SizingEngine
    - sizing_multiplier: pentru audit / dashboard
    - reason: explicatie detaliata
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from loguru import logger


class DecisionAction(str, Enum):
    ENTER         = "ENTER"
    EXIT          = "EXIT"
    HOLD          = "HOLD"
    PARTIAL_EXIT  = "PARTIAL_EXIT"
    SCALE_IN      = "SCALE_IN"
    WAIT          = "WAIT"


@dataclass
class SignalDecision:
    action:            DecisionAction
    qty_y:             float = 0.0
    qty_x:             float = 0.0
    sizing_multiplier: float = 1.0
    z_score:           float = 0.0
    reason:            str   = ""
    pair:              str   = ""
    extra:             Dict[str, Any] = field(default_factory=dict)


class DecisionEngine:
    """
    Evalueaza semnale z-score si produce decizii de trading cu
    sizing dinamic integrat via SizingEngine.

    Reguli z-score (configurabile din .env):
        ENTER_SHORT_Y  : z >= +entry_zscore
        ENTER_LONG_Y   : z <= -entry_zscore
        EXIT           : |z| <= exit_zscore
        PARTIAL_EXIT   : exit_zscore < |z| <= partial_exit_zscore
        SCALE_IN       : |z| >= scale_in_zscore (dupa prima intrare)
    """

    def __init__(
        self,
        entry_zscore:        float = 2.0,
        exit_zscore:         float = 0.5,
        partial_exit_zscore: float = 1.0,
        scale_in_zscore:     float = 3.0,
        base_qty_y:          float = 0.01,
        base_qty_x:          float = 0.01,
        sizing_engine=None,          # SizingEngine (optional, injectat)
        pnl_tracker=None,
        capital_allocator=None,
    ) -> None:
        self._entry_z         = entry_zscore
        self._exit_z          = exit_zscore
        self._partial_z       = partial_exit_zscore
        self._scale_z         = scale_in_zscore
        self._base_qty_y      = base_qty_y
        self._base_qty_x      = base_qty_x
        self._sizing          = sizing_engine
        self._tracker         = pnl_tracker
        self._allocator       = capital_allocator
        self._in_position     = False
        self._current_streak  = 0
        self._current_dd      = 0.0

    @classmethod
    def from_env(
        cls,
        sizing_engine=None,
        pnl_tracker=None,
        capital_allocator=None,
    ) -> "DecisionEngine":
        return cls(
            entry_zscore=float(os.getenv("ENTRY_ZSCORE",        "2.0")),
            exit_zscore=float(os.getenv("EXIT_ZSCORE",          "0.5")),
            partial_exit_zscore=float(os.getenv("PARTIAL_EXIT_ZSCORE", "1.0")),
            scale_in_zscore=float(os.getenv("SCALE_IN_ZSCORE",  "3.0")),
            base_qty_y=float(os.getenv("BASE_QTY_Y",            "0.01")),
            base_qty_x=float(os.getenv("BASE_QTY_X",            "0.01")),
            sizing_engine=sizing_engine,
            pnl_tracker=pnl_tracker,
            capital_allocator=capital_allocator,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_streak(self, streak: int) -> None:
        """Apelat de ProfitOptimizer sau TradeLogger dupa fiecare trade inchis."""
        self._current_streak = streak

    def update_drawdown(self, drawdown_pct: float) -> None:
        """Apelat de MonitoringWatchdog la fiecare ciclu."""
        self._current_dd = drawdown_pct

    async def decide(
        self,
        z_score:         float,
        pair:            str   = "",
        equity_usdt:     float = 0.0,
        entry_price_y:   float = 0.0,
        entry_price_x:   float = 0.0,
        atr_pct:         float = 0.0,
        in_position:     Optional[bool] = None,
    ) -> SignalDecision:
        """
        Evalueza z_score si returneaza SignalDecision.

        Parameters
        ----------
        z_score       : z-score curent al spread-ului
        pair          : "BTCUSDT-ETHUSDT"
        equity_usdt   : equity total (pentru SizingEngine Kelly cap)
        entry_price_y : pretul Y la intrare (pentru Kelly)
        entry_price_x : pretul X la intrare (pentru Kelly)
        atr_pct       : ATR relativ (0.012 = 1.2%)
        in_position   : override pt starea pozitiei (None = foloseste intern)
        """
        in_pos = in_position if in_position is not None else self._in_position
        abs_z  = abs(z_score)

        # 1. EXIT — inchidere completa cand z revine la 0
        if in_pos and abs_z <= self._exit_z:
            self._in_position = False
            return SignalDecision(
                action=DecisionAction.EXIT,
                z_score=z_score,
                reason=f"EXIT: |z|={abs_z:.3f} <= {self._exit_z}",
                pair=pair,
            )

        # 2. PARTIAL_EXIT — iesire partiala la z intermediar
        if in_pos and abs_z <= self._partial_z:
            return SignalDecision(
                action=DecisionAction.PARTIAL_EXIT,
                qty_y=self._base_qty_y * 0.5,
                qty_x=self._base_qty_x * 0.5,
                z_score=z_score,
                reason=f"PARTIAL_EXIT: |z|={abs_z:.3f} <= {self._partial_z}",
                pair=pair,
            )

        # 3. SCALE_IN — marire pozitie la z extrem
        if in_pos and abs_z >= self._scale_z:
            qty_y, qty_x, mult = await self._get_sized_qty(
                equity_usdt=equity_usdt,
                entry_price=entry_price_y,
                atr_pct=atr_pct,
                symbol=pair,
            )
            return SignalDecision(
                action=DecisionAction.SCALE_IN,
                qty_y=qty_y * 0.5,
                qty_x=qty_x * 0.5,
                sizing_multiplier=mult,
                z_score=z_score,
                reason=f"SCALE_IN: |z|={abs_z:.3f} >= {self._scale_z}",
                pair=pair,
            )

        # 4. ENTER — semnal de intrare
        if not in_pos and abs_z >= self._entry_z:
            qty_y, qty_x, mult = await self._get_sized_qty(
                equity_usdt=equity_usdt,
                entry_price=entry_price_y,
                atr_pct=atr_pct,
                symbol=pair,
            )
            direction = "SHORT_Y" if z_score > 0 else "LONG_Y"
            self._in_position = True
            return SignalDecision(
                action=DecisionAction.ENTER,
                qty_y=qty_y,
                qty_x=qty_x,
                sizing_multiplier=mult,
                z_score=z_score,
                reason=(
                    f"ENTER {direction}: z={z_score:.3f} "
                    f"streak={self._current_streak} "
                    f"mult={mult:.2f}x dd={self._current_dd:.1%}"
                ),
                pair=pair,
            )

        # 5. HOLD
        return SignalDecision(
            action=DecisionAction.HOLD,
            z_score=z_score,
            reason=f"HOLD: z={z_score:.3f} (entry={self._entry_z})",
            pair=pair,
        )

    # ------------------------------------------------------------------
    # Sizing integration
    # ------------------------------------------------------------------

    async def _get_sized_qty(
        self,
        equity_usdt: float,
        entry_price: float,
        atr_pct: float,
        symbol: str,
    ):
        """
        Returneaza (qty_y, qty_x, multiplier).
        Daca SizingEngine e disponibil, aplica ajustarile dinamice.
        """
        if self._sizing is None:
            return self._base_qty_y, self._base_qty_x, 1.0

        # Equity live din PnLTracker daca nu e furnizat
        if equity_usdt <= 0 and self._tracker is not None:
            try:
                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                summary = await self._tracker.get_daily_summary(today)
                equity_usdt = float(summary.get("total_equity_usdt", 0.0))
            except Exception:
                pass

        qty_y = await self._sizing.compute_qty(
            base_qty=self._base_qty_y,
            symbol=symbol,
            streak=self._current_streak,
            equity_usdt=equity_usdt,
            drawdown_pct=self._current_dd,
            atr_pct=atr_pct,
            entry_price_usdt=entry_price,
        )
        qty_x = await self._sizing.compute_qty(
            base_qty=self._base_qty_x,
            symbol=symbol,
            streak=self._current_streak,
            equity_usdt=equity_usdt,
            drawdown_pct=self._current_dd,
            atr_pct=atr_pct,
            entry_price_usdt=entry_price,
        )
        return qty_y, qty_x, self._sizing.last_multiplier

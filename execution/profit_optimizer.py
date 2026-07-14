"""
QuantLuna — Profit Optimizer (Sprint 17)

Manages open adopted positions with:
  - Take-profit (TP) and stop-loss (SL) monitoring
  - Break-even SL move after position reaches profit trigger
  - Profit ladder: partial closes at configurable levels
  - Trailing stop from peak price

Usage:
    opt = ProfitOptimizer(exchange)
    opt.register(adoption_result, current_price=50000.0)

    # On each price tick:
    actions = await opt.tick(prices={"BTC/USDT:USDT": 51200.0})
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


class ActionType(str, Enum):
    HOLD          = "hold"
    FULL_CLOSE    = "full_close"
    PARTIAL_CLOSE = "partial_close"
    MOVE_SL       = "move_sl"


@dataclass
class OptAction:
    action_type: ActionType
    symbol:      str
    reason:      str = ""
    close_qty:   float = 0.0
    new_sl:      Optional[float] = None


@dataclass
class TrackedPosition:
    """Internal state for an adopted position being managed."""
    symbol:      str
    side:        str
    qty:         float
    entry_price: float
    tp_price:    float
    sl_price:    float
    trailing_pct: float = 0.015

    # Break-even
    break_even_trigger_pct: float = 0.015  # move SL to BE when profit >= this
    sl_moved_to_be: bool = False

    # Profit ladder: list of (trigger_pct, close_fraction) tuples
    ladder: List[Tuple[float, float]] = field(default_factory=list)
    ladder_executed: int = 0

    # Trailing stop
    trailing_activation_pct: float = 0.02
    peak_price: Optional[float] = None
    trailing_active: bool = False


class ProfitOptimizer:
    """
    Monitors open positions and emits close/move-SL actions on price ticks.

    Parameters
    ----------
    exchange : async CCXT exchange object
    """

    def __init__(self, exchange: Any) -> None:
        self._exchange  = exchange
        self._positions: Dict[str, TrackedPosition] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        result: Any,  # AdoptionResult
        current_price: float,
    ) -> None:
        """
        Register an adopted position for monitoring.

        Parameters
        ----------
        result        : AdoptionResult from AdoptionEngine
        current_price : current mark price
        """
        pos = result.position
        tracked = TrackedPosition(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price=pos.entry_price,
            tp_price=result.tp_price or pos.entry_price * 1.04,
            sl_price=result.sl_price or pos.entry_price * 0.97,
            trailing_pct=result.trailing_pct,
            peak_price=current_price,
        )
        self._positions[pos.symbol] = tracked
        logger.info(
            f"ProfitOptimizer: registered {pos.symbol} tp={tracked.tp_price:.4f} "
            f"sl={tracked.sl_price:.4f} trail={tracked.trailing_pct:.2%}"
        )

    def unregister(self, symbol: str) -> None:
        self._positions.pop(symbol, None)

    @property
    def active_count(self) -> int:
        return len(self._positions)

    async def on_price_tick(self, prices: Dict[str, float]) -> List[OptAction]:
        """Alias for tick() — used by WorkflowOrchestrator optimizer loop."""
        return await self.tick(prices)

    async def tick(self, prices: Dict[str, float]) -> List[OptAction]:
        """
        Evaluate all tracked positions against current prices.
        Returns list of actions to execute.
        """
        actions: List[OptAction] = []
        for symbol, tracked in list(self._positions.items()):
            price = prices.get(symbol)
            if price is None:
                continue
            action = self._evaluate(tracked, price)
            if action.action_type != ActionType.HOLD:
                actions.append(action)
                await self._execute(action, tracked, price)
                if action.action_type == ActionType.FULL_CLOSE:
                    self.unregister(symbol)
        return actions

    # ------------------------------------------------------------------
    # Evaluation logic
    # ------------------------------------------------------------------

    def _evaluate(self, t: TrackedPosition, price: float) -> OptAction:
        """Evaluate a single position. Returns the recommended action."""
        is_long = t.side == "long"

        # Update peak
        if t.peak_price is None:
            t.peak_price = price
        if is_long:
            t.peak_price = max(t.peak_price, price)
        else:
            t.peak_price = min(t.peak_price, price)

        # 1. SL hit
        if is_long and price <= t.sl_price:
            return OptAction(ActionType.FULL_CLOSE, t.symbol, f"SL hit at {price:.4f}")
        if not is_long and price >= t.sl_price:
            return OptAction(ActionType.FULL_CLOSE, t.symbol, f"SL hit at {price:.4f}")

        # 2. TP hit
        if is_long and price >= t.tp_price:
            return OptAction(ActionType.FULL_CLOSE, t.symbol, f"TP hit at {price:.4f}")
        if not is_long and price <= t.tp_price:
            return OptAction(ActionType.FULL_CLOSE, t.symbol, f"TP hit at {price:.4f}")

        # 3. Break-even move
        if not t.sl_moved_to_be:
            profit_pct = (
                (price - t.entry_price) / t.entry_price if is_long
                else (t.entry_price - price) / t.entry_price
            )
            if profit_pct >= t.break_even_trigger_pct:
                # Move SL to entry + 1 tick
                new_sl = t.entry_price * (1.0 + 0.0001) if is_long else t.entry_price * (1.0 - 0.0001)
                t.sl_price       = new_sl
                t.sl_moved_to_be = True
                logger.info(f"ProfitOptimizer: BE move {t.symbol} new_sl={new_sl:.4f}")
                return OptAction(ActionType.MOVE_SL, t.symbol, "Break-even SL move", new_sl=new_sl)

        # 4. Profit ladder
        if t.ladder and t.ladder_executed < len(t.ladder):
            trigger_pct, close_frac = t.ladder[t.ladder_executed]
            profit_pct = (
                (price - t.entry_price) / t.entry_price if is_long
                else (t.entry_price - price) / t.entry_price
            )
            if profit_pct >= trigger_pct:
                close_qty = t.qty * close_frac
                t.qty             -= close_qty
                t.ladder_executed += 1
                logger.info(
                    f"ProfitOptimizer: ladder L{t.ladder_executed} {t.symbol} "
                    f"close {close_frac:.0%} qty={close_qty:.6f}"
                )
                return OptAction(
                    ActionType.PARTIAL_CLOSE, t.symbol,
                    f"Ladder L{t.ladder_executed}",
                    close_qty=close_qty,
                )

        # 5. Trailing stop
        activation_pct = t.trailing_activation_pct
        peak = t.peak_price
        if is_long:
            activated = (peak - t.entry_price) / t.entry_price >= activation_pct
            trail_sl  = peak * (1.0 - t.trailing_pct)
            if activated and price <= trail_sl:
                return OptAction(
                    ActionType.FULL_CLOSE, t.symbol,
                    f"Trailing stop hit at {price:.4f} (trail from {peak:.4f})",
                )
        else:
            activated = (t.entry_price - peak) / t.entry_price >= activation_pct
            trail_sl  = peak * (1.0 + t.trailing_pct)
            if activated and price >= trail_sl:
                return OptAction(
                    ActionType.FULL_CLOSE, t.symbol,
                    f"Trailing stop hit at {price:.4f} (trail from {peak:.4f})",
                )

        return OptAction(ActionType.HOLD, t.symbol, "hold")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(self, action: OptAction, tracked: TrackedPosition, price: float) -> None:
        """Execute the action against the exchange."""
        if action.action_type == ActionType.HOLD:
            return

        side = "sell" if tracked.side == "long" else "buy"

        if action.action_type == ActionType.FULL_CLOSE:
            try:
                await self._exchange.create_order(
                    tracked.symbol, "market", side, tracked.qty,
                    params={"reduceOnly": True},
                )
                logger.info(f"ProfitOptimizer: FULL_CLOSE {tracked.symbol} reason={action.reason}")
            except Exception as exc:
                logger.error(f"ProfitOptimizer: FULL_CLOSE failed {tracked.symbol}: {exc}")

        elif action.action_type == ActionType.PARTIAL_CLOSE:
            try:
                await self._exchange.create_order(
                    tracked.symbol, "market", side, action.close_qty,
                    params={"reduceOnly": True},
                )
                logger.info(
                    f"ProfitOptimizer: PARTIAL_CLOSE {tracked.symbol} "
                    f"qty={action.close_qty:.6f} reason={action.reason}"
                )
            except Exception as exc:
                logger.error(f"ProfitOptimizer: PARTIAL_CLOSE failed {tracked.symbol}: {exc}")

        elif action.action_type == ActionType.MOVE_SL:
            # SL moves are managed locally (no exchange cancel/replace needed for market SL)
            pass

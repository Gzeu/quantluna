"""
execution/profit_optimizer.py  —  QuantLuna Profit Optimizer

Scop:
  Gestionează activ pozițiile adoptate (orfane preluate) pentru profit maxim:
    1. TrailingStopManager  — trailing stop dinamic per poziție
    2. ProfitLadder          — închidere parțială la niveluri de profit
    3. AdaptiveTP            — TP dinamic bazat pe volatilitate reală
    4. BreakEvenMover        — mută SL la break-even când PnL > 1.5%

Fiecare poziție adoptata e urmărită într-un AdoptedPositionTracker.
La fiecare tick de preț, `on_price_tick()` e apelat şi returnează
acțiunea necesara (HOLD / PARTIAL_CLOSE / FULL_CLOSE).

Usage:
    optimizer = ProfitOptimizer(exchange, alert_cfg)
    optimizer.register(adoption_result, current_price)

    # La fiecare tick:
    actions = await optimizer.on_price_tick({symbol: price})
    for action in actions:
        if action.type == 'FULL_CLOSE':
            # execută close
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from execution.adoption_engine import AdoptionResult
from execution.position_scanner import ExchangePosition

logger = logging.getLogger(__name__)


class ActionType(Enum):
    HOLD          = "hold"
    PARTIAL_CLOSE = "partial_close"   # închidem X% din poziție
    FULL_CLOSE    = "full_close"      # închidem tot
    MOVE_SL       = "move_sl"         # mută SL (informativ, fara ordin Exchange)


@dataclass
class OptimizerAction:
    symbol: str
    action_type: ActionType
    reason: str
    close_qty: float = 0.0       # pentru PARTIAL_CLOSE / FULL_CLOSE
    new_sl: Optional[float] = None
    current_price: float = 0.0
    current_pnl: float = 0.0


@dataclass
class TrackedPosition:
    symbol: str
    side: str                   # 'long' sau 'short'
    qty: float
    entry_price: float
    tp_price: float
    sl_price: float
    trailing_pct: float
    trailing_activation_pct: float = 0.02
    break_even_trigger_pct: float  = 0.015

    # State runtime
    peak_price: float = 0.0
    sl_moved_to_be: bool = False
    partial_closed_pct: float = 0.0   # cât % am închis deja
    registered_at: float = field(default_factory=time.time)

    # Ladder niveluri de partial close: [(profit_pct, close_pct)]
    ladder: List[tuple] = field(default_factory=lambda: [
        (0.02, 0.25),   # la +2%  → închide 25% din poziție
        (0.04, 0.25),   # la +4%  → închide încă 25%
        (0.07, 0.30),   # la +7%  → închide încă 30%
        # la +TP  → închide restul 20%
    ])
    ladder_executed: int = 0    # câte niveluri din ladder au fost executate

    def __post_init__(self):
        self.peak_price = self.entry_price

    @property
    def remaining_qty(self) -> float:
        return self.qty * (1 - self.partial_closed_pct)

    def current_pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == 'long':
            return (current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - current_price) / self.entry_price

    def current_pnl_usdt(self, current_price: float) -> float:
        pnl_pct = self.current_pnl_pct(current_price)
        notional = self.entry_price * self.qty
        return pnl_pct * notional

    def update_peak(self, current_price: float) -> None:
        if self.side == 'long':
            self.peak_price = max(self.peak_price, current_price)
        else:
            self.peak_price = min(self.peak_price, current_price)

    def trailing_stop_price(self) -> float:
        """Сalculează nivelul trailing stop curent."""
        if self.side == 'long':
            return self.peak_price * (1 - self.trailing_pct)
        else:
            return self.peak_price * (1 + self.trailing_pct)


class ProfitOptimizer:
    """
    Gestionează activ poziții adoptate pentru profit maxim.

    Args:
        exchange:  ccxt async exchange instance (create_order)
        alert_cfg: AlertConfig
    """

    def __init__(self, exchange, alert_cfg=None) -> None:
        self._exchange  = exchange
        self._alert     = alert_cfg
        self._positions: Dict[str, TrackedPosition] = {}

    def register(
        self,
        result: AdoptionResult,
        current_price: Optional[float] = None,
    ) -> None:
        """
        Înregistrează o poziție adoptată pentru tracking activ.
        """
        pos    = result.position
        entry  = pos.entry_price
        tp     = result.tp_price or entry * (1.04 if pos.side == 'long' else 0.96)
        sl     = result.sl_price or entry * (0.97 if pos.side == 'long' else 1.03)
        trail  = result.trailing_pct or 0.015

        tracked = TrackedPosition(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price=entry,
            tp_price=tp,
            sl_price=sl,
            trailing_pct=trail,
        )
        if current_price:
            tracked.peak_price = current_price

        self._positions[pos.symbol] = tracked
        logger.info(
            f"[Optimizer] Registered: {pos.symbol} {pos.side} "
            f"qty={pos.qty:.4f} TP={tp:.4f} SL={sl:.4f} trail={trail:.1%}"
        )

    async def on_price_tick(
        self, prices: Dict[str, float]
    ) -> List[OptimizerAction]:
        """
        Apelat la fiecare tick de preț pentru pozițiile gestionate.
        Returnează lista de acțiuni necesare.
        """
        actions = []
        for symbol, tracked in list(self._positions.items()):
            price = prices.get(symbol) or prices.get(symbol.split('/')[0])
            if price is None:
                continue

            action = self._evaluate(tracked, price)
            if action.action_type != ActionType.HOLD:
                actions.append(action)
                await self._execute_action(action, tracked)

        return actions

    def _evaluate(
        self, tracked: TrackedPosition, price: float
    ) -> OptimizerAction:
        tracked.update_peak(price)
        pnl_pct  = tracked.current_pnl_pct(price)
        pnl_usdt = tracked.current_pnl_usdt(price)
        trailing_stop = tracked.trailing_stop_price()

        # 1. Stop-Loss hit
        if tracked.side == 'long' and price <= tracked.sl_price:
            return OptimizerAction(
                symbol=tracked.symbol,
                action_type=ActionType.FULL_CLOSE,
                reason=f"SL hit: price={price:.4f} <= sl={tracked.sl_price:.4f}",
                close_qty=tracked.remaining_qty,
                current_price=price, current_pnl=pnl_usdt,
            )
        if tracked.side == 'short' and price >= tracked.sl_price:
            return OptimizerAction(
                symbol=tracked.symbol,
                action_type=ActionType.FULL_CLOSE,
                reason=f"SL hit: price={price:.4f} >= sl={tracked.sl_price:.4f}",
                close_qty=tracked.remaining_qty,
                current_price=price, current_pnl=pnl_usdt,
            )

        # 2. Take-Profit hit
        if tracked.side == 'long' and price >= tracked.tp_price:
            return OptimizerAction(
                symbol=tracked.symbol,
                action_type=ActionType.FULL_CLOSE,
                reason=f"TP hit: price={price:.4f} >= tp={tracked.tp_price:.4f}",
                close_qty=tracked.remaining_qty,
                current_price=price, current_pnl=pnl_usdt,
            )
        if tracked.side == 'short' and price <= tracked.tp_price:
            return OptimizerAction(
                symbol=tracked.symbol,
                action_type=ActionType.FULL_CLOSE,
                reason=f"TP hit: price={price:.4f} <= tp={tracked.tp_price:.4f}",
                close_qty=tracked.remaining_qty,
                current_price=price, current_pnl=pnl_usdt,
            )

        # 3. Trailing stop hit (dupa activare)
        if pnl_pct >= tracked.trailing_activation_pct:
            if tracked.side == 'long' and price <= trailing_stop:
                return OptimizerAction(
                    symbol=tracked.symbol,
                    action_type=ActionType.FULL_CLOSE,
                    reason=f"Trailing stop: price={price:.4f} <= trail={trailing_stop:.4f} | peak={tracked.peak_price:.4f}",
                    close_qty=tracked.remaining_qty,
                    current_price=price, current_pnl=pnl_usdt,
                )
            if tracked.side == 'short' and price >= trailing_stop:
                return OptimizerAction(
                    symbol=tracked.symbol,
                    action_type=ActionType.FULL_CLOSE,
                    reason=f"Trailing stop: price={price:.4f} >= trail={trailing_stop:.4f} | peak={tracked.peak_price:.4f}",
                    close_qty=tracked.remaining_qty,
                    current_price=price, current_pnl=pnl_usdt,
                )

        # 4. Break-even move (o singura data)
        if not tracked.sl_moved_to_be and pnl_pct >= tracked.break_even_trigger_pct:
            new_sl = tracked.entry_price * (1.001 if tracked.side == 'long' else 0.999)
            tracked.sl_price    = new_sl
            tracked.sl_moved_to_be = True
            logger.info(
                f"[Optimizer] Break-even: {tracked.symbol} SL → {new_sl:.4f} "
                f"(PnL={pnl_pct:+.1%})"
            )
            return OptimizerAction(
                symbol=tracked.symbol,
                action_type=ActionType.MOVE_SL,
                reason=f"Break-even: PnL={pnl_pct:+.1%} >= {tracked.break_even_trigger_pct:.1%}",
                new_sl=new_sl,
                current_price=price, current_pnl=pnl_usdt,
            )

        # 5. Profit ladder — partial close
        if tracked.ladder_executed < len(tracked.ladder):
            level_pct, close_pct = tracked.ladder[tracked.ladder_executed]
            if pnl_pct >= level_pct:
                close_qty = tracked.qty * close_pct
                tracked.partial_closed_pct += close_pct
                tracked.ladder_executed += 1
                logger.info(
                    f"[Optimizer] Ladder L{tracked.ladder_executed}: "
                    f"{tracked.symbol} close {close_pct:.0%} qty={close_qty:.4f} "
                    f"(PnL={pnl_pct:+.1%})"
                )
                return OptimizerAction(
                    symbol=tracked.symbol,
                    action_type=ActionType.PARTIAL_CLOSE,
                    reason=f"Profit ladder L{tracked.ladder_executed}: PnL={pnl_pct:+.1%} >= {level_pct:.1%}",
                    close_qty=close_qty,
                    current_price=price, current_pnl=pnl_usdt,
                )

        return OptimizerAction(
            symbol=tracked.symbol,
            action_type=ActionType.HOLD,
            reason="",
            current_price=price, current_pnl=pnl_usdt,
        )

    async def _execute_action(
        self, action: OptimizerAction, tracked: TrackedPosition
    ) -> None:
        if action.action_type == ActionType.MOVE_SL:
            # SL e soft (gestionat de bot, nu ordin exchange)
            return

        if action.action_type in (ActionType.FULL_CLOSE, ActionType.PARTIAL_CLOSE):
            close_side = 'sell' if tracked.side == 'long' else 'buy'
            try:
                order = await self._exchange.create_order(
                    symbol=action.symbol,
                    type='market',
                    side=close_side,
                    amount=action.close_qty,
                    params={'reduceOnly': True},
                )
                logger.info(
                    f"[Optimizer] {action.action_type.value}: {action.symbol} "
                    f"{close_side} qty={action.close_qty:.4f} "
                    f"| reason={action.reason} "
                    f"| order={order.get('id', 'unknown')}"
                )
                if action.action_type == ActionType.FULL_CLOSE:
                    self._positions.pop(action.symbol, None)
                await self._send_alert(action)
            except Exception as exc:
                logger.error(
                    f"[Optimizer] execute_action FAILED: {action.symbol} "
                    f"{action.action_type.value}: {exc}"
                )

    async def _send_alert(self, action: OptimizerAction) -> None:
        if not self._alert:
            return
        emoji = '✅' if action.current_pnl >= 0 else '🔴'
        msg = (
            f"{emoji} Optimizer [{action.action_type.value.upper()}]\n"
            f"Symbol: {action.symbol}\n"
            f"Qty closed: {action.close_qty:.4f} @ {action.current_price:.4f}\n"
            f"PnL: {action.current_pnl:+.2f} USDT\n"
            f"Motiv: {action.reason}"
        )
        try:
            from execution.live_trader import _send_alert
            await _send_alert(self._alert, msg)
        except Exception as exc:
            logger.warning(f"[Optimizer] alert failed: {exc}")

    @property
    def active_count(self) -> int:
        return len(self._positions)

    def position_summary(self) -> List[dict]:
        return [
            {
                'symbol': t.symbol,
                'side': t.side,
                'qty': t.qty,
                'remaining_qty': t.remaining_qty,
                'entry': t.entry_price,
                'tp': t.tp_price,
                'sl': t.sl_price,
                'peak': t.peak_price,
                'trailing_stop': t.trailing_stop_price(),
                'sl_at_be': t.sl_moved_to_be,
                'ladder_level': t.ladder_executed,
                'partial_closed': t.partial_closed_pct,
            }
            for t in self._positions.values()
        ]

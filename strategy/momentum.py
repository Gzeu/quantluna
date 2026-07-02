"""
Module: strategy/momentum.py
Sprint: 31 — T (Multi-Strategy Engine)
Description:
    MomentumStrategy: breakout entry based on spread ROC (Rate of
    Change), ATR-based trailing stop, and ROC signal filter.
    Entry:  ROC > roc_threshold (LONG) or ROC < -roc_threshold (SHORT)
    Exit:   ATR trailing stop breached

Usage:
    strat = MomentumStrategy("mom_BTCETH", params={"roc_period": 14})
    signal = await strat.generate_signal({"spread": [...], "atr": 0.02})
"""

from __future__ import annotations

import logging
from typing import Any

from strategy.base_strategy import BaseStrategy, SignalDirection, SignalResult, StrategyMetrics

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """Spread breakout + ATR trailing stop momentum strategy."""

    def __init__(self, strategy_id: str, params: dict[str, Any] | None = None) -> None:
        super().__init__(strategy_id, params)
        p = self.params
        self._roc_period: int = int(p.get("roc_period", 14))
        self._roc_threshold: float = float(p.get("roc_threshold", 0.02))
        self._atr_multiplier: float = float(p.get("atr_multiplier", 2.0))
        self._position: str | None = None
        self._entry_price: float = 0.0
        self._trail_stop: float = 0.0

    async def generate_signal(self, data: dict[str, Any]) -> SignalResult:
        symbol: str = data.get("symbol", "")
        spread: list[float] = data.get("spread", [])
        atr: float = float(data.get("atr", 0.0))
        current_price: float = float(data.get("current_price", spread[-1] if spread else 0.0))

        if not self.is_active() or len(spread) < self._roc_period + 1:
            return SignalResult(direction=SignalDirection.FLAT, symbol=symbol)

        roc = self._compute_roc(spread)

        if self._position is None:
            if roc > self._roc_threshold:
                self._position = "LONG"
                self._entry_price = current_price
                self._trail_stop = current_price - self._atr_multiplier * atr
                logger.info("[MOM] %s LONG entry roc=%.4f", symbol, roc)
                return SignalResult(direction=SignalDirection.LONG, symbol=symbol,
                                    strength=min(roc / self._roc_threshold, 1.0))
            if roc < -self._roc_threshold:
                self._position = "SHORT"
                self._entry_price = current_price
                self._trail_stop = current_price + self._atr_multiplier * atr
                logger.info("[MOM] %s SHORT entry roc=%.4f", symbol, roc)
                return SignalResult(direction=SignalDirection.SHORT, symbol=symbol,
                                    strength=min(abs(roc) / self._roc_threshold, 1.0))
        else:
            # Update trailing stop
            if self._position == "LONG":
                new_stop = current_price - self._atr_multiplier * atr
                self._trail_stop = max(self._trail_stop, new_stop)
                if current_price <= self._trail_stop:
                    logger.info("[MOM] %s EXIT (trail stop hit)", symbol)
                    self._position = None
                    return SignalResult(direction=SignalDirection.EXIT, symbol=symbol)
            elif self._position == "SHORT":
                new_stop = current_price + self._atr_multiplier * atr
                self._trail_stop = min(self._trail_stop, new_stop)
                if current_price >= self._trail_stop:
                    logger.info("[MOM] %s EXIT (trail stop hit)", symbol)
                    self._position = None
                    return SignalResult(direction=SignalDirection.EXIT, symbol=symbol)

        return SignalResult(direction=SignalDirection.FLAT, symbol=symbol)

    def _compute_roc(self, spread: list[float]) -> float:
        n = self._roc_period
        if len(spread) < n + 1:
            return 0.0
        prev = spread[-(n + 1)]
        curr = spread[-1]
        if prev == 0:
            return 0.0
        return (curr - prev) / abs(prev)

    async def on_fill(self, fill_event: dict[str, Any]) -> None:
        pnl = float(fill_event.get("pnl", 0.0))
        self._record_trade(pnl, fill_event)

    async def on_position_update(self, position_event: dict[str, Any]) -> None:
        size = float(position_event.get("size", 0))
        if size == 0:
            self._position = None

    def get_metrics(self) -> StrategyMetrics:
        wins = [p for p in self._pnl_history if p > 0]
        return StrategyMetrics(
            strategy_id=self.strategy_id,
            state=self.state,
            sharpe=self._compute_sharpe(),
            total_trades=len(self._trades),
            win_rate=len(wins) / max(len(self._pnl_history), 1),
            total_pnl=sum(self._pnl_history),
        )

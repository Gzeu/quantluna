"""
Module: strategy/mean_reversion.py
Sprint: 31 — T (Multi-Strategy Engine)
Description:
    MeanReversionStrategy: classic z-score stat-arb entry/exit.
    Extracted and refactored from live_trader.py.
    Entry: |z| > entry_threshold
    Exit:  |z| < exit_threshold  OR  |z| > stop_threshold

Usage:
    strat = MeanReversionStrategy("mr_BTCETH", params={"entry_z": 2.0})
    signal = await strat.generate_signal({"spread": [...], "symbol": "BTCUSDT"})
"""

from __future__ import annotations

import logging
from typing import Any

from strategy.base_strategy import BaseStrategy, SignalDirection, SignalResult, StrategyMetrics

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """Z-score mean-reversion strategy (stat-arb core)."""

    def __init__(self, strategy_id: str, params: dict[str, Any] | None = None) -> None:
        super().__init__(strategy_id, params)
        p = self.params
        self._entry_z: float = p.get("entry_z", 2.0)
        self._exit_z: float = p.get("exit_z", 0.5)
        self._stop_z: float = p.get("stop_z", 3.5)
        self._position: str | None = None  # "LONG" | "SHORT" | None

    async def generate_signal(self, data: dict[str, Any]) -> SignalResult:
        symbol: str = data.get("symbol", "")
        z: float = float(data.get("z_score", 0.0))

        if not self.is_active():
            return SignalResult(direction=SignalDirection.FLAT, symbol=symbol)

        if self._position is None:
            if z > self._entry_z:
                self._position = "SHORT"
                logger.info("[MR] %s SHORT entry z=%.3f", symbol, z)
                return SignalResult(direction=SignalDirection.SHORT, symbol=symbol,
                                    strength=min(abs(z) / self._entry_z, 1.0))
            if z < -self._entry_z:
                self._position = "LONG"
                logger.info("[MR] %s LONG entry z=%.3f", symbol, z)
                return SignalResult(direction=SignalDirection.LONG, symbol=symbol,
                                    strength=min(abs(z) / self._entry_z, 1.0))
        else:
            if abs(z) < self._exit_z or abs(z) > self._stop_z:
                logger.info("[MR] %s EXIT z=%.3f", symbol, z)
                self._position = None
                return SignalResult(direction=SignalDirection.EXIT, symbol=symbol)

        return SignalResult(direction=SignalDirection.FLAT, symbol=symbol)

    async def on_fill(self, fill_event: dict[str, Any]) -> None:
        pnl = float(fill_event.get("pnl", 0.0))
        self._record_trade(pnl, fill_event)
        logger.info("[MR] Fill recorded pnl=%.4f", pnl)

    async def on_position_update(self, position_event: dict[str, Any]) -> None:
        side = position_event.get("side", "")
        size = float(position_event.get("size", 0))
        if size == 0:
            self._position = None
        else:
            self._position = side

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

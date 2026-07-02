"""
Module: strategy/stat_arb.py
Sprint: 31 — T (Multi-Strategy Engine)
Description:
    StatArbStrategy: cointegration-based pair trading strategy using
    dynamic hedge ratio (OLS rolling) and VECM error-correction signal.
    Entry:  |ec_signal| > entry_threshold
    Exit:   |ec_signal| < exit_threshold

Usage:
    strat = StatArbStrategy("sa_BTCETH", params={"entry_ec": 0.02})
    signal = await strat.generate_signal({"prices_a": [...], "prices_b": [...]})
"""

from __future__ import annotations

import logging
from typing import Any

from strategy.base_strategy import BaseStrategy, SignalDirection, SignalResult, StrategyMetrics

logger = logging.getLogger(__name__)


class StatArbStrategy(BaseStrategy):
    """Cointegration + VECM error-correction stat-arb strategy."""

    def __init__(self, strategy_id: str, params: dict[str, Any] | None = None) -> None:
        super().__init__(strategy_id, params)
        p = self.params
        self._entry_ec: float = float(p.get("entry_ec", 0.02))
        self._exit_ec: float = float(p.get("exit_ec", 0.005))
        self._hedge_window: int = int(p.get("hedge_window", 60))
        self._position: str | None = None
        self._hedge_ratio: float = 1.0

    async def generate_signal(self, data: dict[str, Any]) -> SignalResult:
        symbol: str = data.get("symbol", "")
        prices_a: list[float] = data.get("prices_a", [])
        prices_b: list[float] = data.get("prices_b", [])

        if not self.is_active() or len(prices_a) < self._hedge_window:
            return SignalResult(direction=SignalDirection.FLAT, symbol=symbol)

        self._hedge_ratio = self._rolling_hedge_ratio(prices_a, prices_b)
        spread = [a - self._hedge_ratio * b for a, b in zip(prices_a, prices_b)]
        ec_signal = self._vecm_error_correction(spread)

        if self._position is None:
            if ec_signal > self._entry_ec:
                self._position = "SHORT"
                logger.info("[STAT_ARB] %s SHORT ec=%.5f hedge=%.4f", symbol, ec_signal, self._hedge_ratio)
                return SignalResult(direction=SignalDirection.SHORT, symbol=symbol,
                                    strength=min(ec_signal / self._entry_ec, 1.0),
                                    metadata={"hedge_ratio": self._hedge_ratio})
            if ec_signal < -self._entry_ec:
                self._position = "LONG"
                logger.info("[STAT_ARB] %s LONG ec=%.5f hedge=%.4f", symbol, ec_signal, self._hedge_ratio)
                return SignalResult(direction=SignalDirection.LONG, symbol=symbol,
                                    strength=min(abs(ec_signal) / self._entry_ec, 1.0),
                                    metadata={"hedge_ratio": self._hedge_ratio})
        else:
            if abs(ec_signal) < self._exit_ec:
                logger.info("[STAT_ARB] %s EXIT ec=%.5f", symbol, ec_signal)
                self._position = None
                return SignalResult(direction=SignalDirection.EXIT, symbol=symbol)

        return SignalResult(direction=SignalDirection.FLAT, symbol=symbol)

    def _rolling_hedge_ratio(self, prices_a: list[float], prices_b: list[float]) -> float:
        """OLS hedge ratio over last hedge_window bars."""
        n = self._hedge_window
        a = prices_a[-n:]
        b = prices_b[-n:]
        if len(a) < 2 or len(b) < 2:
            return 1.0
        mean_b = sum(b) / len(b)
        mean_a = sum(a) / len(a)
        cov = sum((bi - mean_b) * (ai - mean_a) for ai, bi in zip(a, b))
        var_b = sum((bi - mean_b) ** 2 for bi in b)
        return cov / var_b if var_b > 0 else 1.0

    def _vecm_error_correction(self, spread: list[float]) -> float:
        """Simplified VECM: error correction = deviation from spread mean."""
        if not spread:
            return 0.0
        n = min(self._hedge_window, len(spread))
        window = spread[-n:]
        mean = sum(window) / len(window)
        return spread[-1] - mean

    async def on_fill(self, fill_event: dict[str, Any]) -> None:
        self._record_trade(float(fill_event.get("pnl", 0.0)), fill_event)

    async def on_position_update(self, position_event: dict[str, Any]) -> None:
        if float(position_event.get("size", 0)) == 0:
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
            extra={"hedge_ratio": self._hedge_ratio},
        )

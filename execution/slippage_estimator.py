"""
execution/slippage_estimator.py — estimare slippage inainte de plasarea ordinului.

Foloseste bid/ask spread si adancimea cartii de ordine (daca este disponibila)
pentru a estima costul real al executiei.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class SlippageEstimate:
    expected_slippage_pct: float
    expected_slippage_usdt: float
    spread_pct: float
    acceptable: bool
    details: str


class SlippageEstimator:
    """
    Estimeaza slippage-ul pentru un ordin market.

    Usage::

        est = SlippageEstimator(max_slippage_pct=0.10)
        result = est.estimate(
            notional_usdt=500.0,
            bid=29900.0,
            ask=29902.0,
        )
        if not result.acceptable:
            logger.warning("Slippage prea mare: %s", result.details)
    """

    def __init__(self, max_slippage_pct: float = 0.10) -> None:
        self._max_slippage_pct = max_slippage_pct

    def estimate(
        self,
        notional_usdt: float,
        bid: float,
        ask: float,
        orderbook_bids: Optional[List[Tuple[float, float]]] = None,
        orderbook_asks: Optional[List[Tuple[float, float]]] = None,
    ) -> SlippageEstimate:
        mid = (bid + ask) / 2.0
        if mid <= 0:
            return SlippageEstimate(0.0, 0.0, 0.0, True, "mid price zero")

        spread_pct = (ask - bid) / mid * 100.0
        base_slippage_pct = spread_pct / 2.0

        impact_pct = 0.0
        if orderbook_asks and notional_usdt > 0:
            impact_pct = self._market_impact(notional_usdt, orderbook_asks, mid, "ask")
        elif orderbook_bids and notional_usdt > 0:
            impact_pct = self._market_impact(notional_usdt, orderbook_bids, mid, "bid")

        total_pct = base_slippage_pct + impact_pct
        total_usdt = notional_usdt * total_pct / 100.0
        acceptable = total_pct <= self._max_slippage_pct

        details = (
            f"spread={spread_pct:.4f}% impact={impact_pct:.4f}% "
            f"total={total_pct:.4f}% ({total_usdt:.4f} USDT) "
            f"max={self._max_slippage_pct:.4f}%"
        )

        return SlippageEstimate(
            expected_slippage_pct=round(total_pct, 6),
            expected_slippage_usdt=round(total_usdt, 4),
            spread_pct=round(spread_pct, 6),
            acceptable=acceptable,
            details=details,
        )

    def _market_impact(
        self,
        notional_usdt: float,
        levels: List[Tuple[float, float]],
        mid: float,
        side: str,
    ) -> float:
        remaining = notional_usdt
        filled_value = 0.0
        filled_qty = 0.0
        for price, qty in levels:
            if remaining <= 0:
                break
            take = min(remaining, price * qty)
            filled_qty += take / price
            filled_value += take
            remaining -= take
        if filled_qty < 1e-12:
            return 0.0
        avg_price = filled_value / filled_qty
        impact = (
            (avg_price - mid) / mid * 100.0
            if side == "ask"
            else (mid - avg_price) / mid * 100.0
        )
        return max(0.0, impact)

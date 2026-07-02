"""
QuantLuna — PaperAccount
Sprint 24

Simulated account for paper trading mode.
Tracks positions, P&L, and trade history without real orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class PaperTrade:
    side:     int
    qty_y:    float
    qty_x:    float
    entry_price_y: float
    entry_price_x: float
    exit_price_y:  Optional[float] = None
    exit_price_x:  Optional[float] = None
    pnl:      float = 0.0
    entry_ts: Optional[datetime] = None
    exit_ts:  Optional[datetime] = None


class PaperAccount:
    """
    Paper trading account.

    Parameters
    ----------
    capital_usdt : initial capital in USDT
    fee_rate     : taker fee per side (default 0.0004 = 0.04%)
    """

    def __init__(self, capital_usdt: float = 1000.0, fee_rate: float = 0.0004) -> None:
        self.initial_capital = capital_usdt
        self.capital         = capital_usdt
        self.fee_rate        = fee_rate
        self._open:  Optional[PaperTrade] = None
        self._history: List[PaperTrade]   = []
        self._realised_pnl: float = 0.0

    def open_position(
        self,
        side: int,
        qty_y: float,
        qty_x: float,
        price_y: float,
        price_x: float,
    ) -> None:
        if self._open is not None:
            return   # already in position
        fee = (qty_y * price_y + qty_x * price_x) * self.fee_rate
        self.capital -= fee
        self._open = PaperTrade(
            side=side, qty_y=qty_y, qty_x=qty_x,
            entry_price_y=price_y, entry_price_x=price_x,
            entry_ts=datetime.now(timezone.utc),
        )

    def close_position(self, price_y: float, price_x: float) -> float:
        if self._open is None:
            return 0.0
        t = self._open
        gross_pnl = (
            (price_y - t.entry_price_y) * t.qty_y * t.side
            - (price_x - t.entry_price_x) * t.qty_x * t.side
        )
        fee = (t.qty_y * price_y + t.qty_x * price_x) * self.fee_rate
        net_pnl = gross_pnl - fee
        self.capital += net_pnl
        t.exit_price_y = price_y
        t.exit_price_x = price_x
        t.pnl    = round(net_pnl, 6)
        t.exit_ts = datetime.now(timezone.utc)
        self._history.append(t)
        self._realised_pnl += net_pnl
        self._open = None
        return net_pnl

    @property
    def realised_pnl(self) -> float:
        return round(self._realised_pnl, 6)

    @property
    def n_trades(self) -> int:
        return len(self._history)

    @property
    def win_rate(self) -> float:
        if not self._history:
            return 0.0
        return sum(1 for t in self._history if t.pnl > 0) / len(self._history)

    def summary(self) -> Dict:
        return {
            "capital": round(self.capital, 4),
            "realised_pnl": self.realised_pnl,
            "n_trades": self.n_trades,
            "win_rate": round(self.win_rate, 4),
            "in_position": self._open is not None,
        }

"""
QuantLuna — Paper Order Types
Sprint 30

PaperOrder dataclass si enums pentru simulatorul paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT  = "limit"


class OrderSide(str, Enum):
    BUY  = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING   = "pending"
    FILLED    = "filled"
    PARTIAL   = "partial"
    CANCELLED = "cancelled"
    REJECTED  = "rejected"


@dataclass
class PaperOrder:
    order_id:   str
    symbol:     str
    side:       OrderSide
    order_type: OrderType
    qty:        float                  # in contracts/coins
    price:      Optional[float]        # None pt. market orders

    # Fill info
    filled_qty:   float   = 0.0
    avg_fill_price: float = 0.0
    status:       OrderStatus = OrderStatus.PENDING
    commission:   float   = 0.0
    slippage:     float   = 0.0

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at:  Optional[datetime] = None

    # Metadata
    pair:       str  = ""  # perechea de trading (ex: BTC/ETH)
    reduce_only: bool = False

    @property
    def notional(self) -> float:
        return self.avg_fill_price * self.filled_qty

    @property
    def is_done(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)

    def to_dict(self) -> dict:
        return {
            "order_id":        self.order_id,
            "symbol":          self.symbol,
            "side":            self.side.value,
            "order_type":      self.order_type.value,
            "qty":             self.qty,
            "price":           self.price,
            "filled_qty":      self.filled_qty,
            "avg_fill_price":  self.avg_fill_price,
            "status":          self.status.value,
            "commission":      round(self.commission, 6),
            "slippage":        round(self.slippage, 6),
            "created_at":      self.created_at.isoformat(),
            "filled_at":       self.filled_at.isoformat() if self.filled_at else None,
            "pair":            self.pair,
            "notional":        round(self.notional, 4),
        }

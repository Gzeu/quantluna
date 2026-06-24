"""
execution/  —  QuantLuna Sprint 4 v2

Public API:
    OrderManager    — async pair execution engine (market + limit, retry, paper mode)
    Fill            — single leg fill metadata
    FillPair        — both legs + analytics (total_cost, net_notional, execution_lag_ms)
    LiveTrader      — real WebSocket live trading engine (Bybit / Binance)
    TraderState     — state machine enum for LiveTrader
    PriceTick       — WS tick dataclass
    LiveConfig      — LiveTrader configuration
    ExecutionConfig — OrderManager configuration
"""

from .order_manager import (
    OrderManager,
    Fill,
    FillPair,
    ExecutionConfig,
)
from .live_trader import (
    LiveTrader,
    TraderState,
    PriceTick,
    LiveConfig,
)

__all__ = [
    "OrderManager",
    "Fill",
    "FillPair",
    "ExecutionConfig",
    "LiveTrader",
    "TraderState",
    "PriceTick",
    "LiveConfig",
]

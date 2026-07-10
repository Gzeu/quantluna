"""
execution package — public exports
"""
from execution.order_manager import (
    OrderManager,
    OrderManagerConfig,
    OrderRequest,
    OrderRecord,
    OrderStatus,
    OrderSide,
    OrderType,
)
from execution.position_scanner import PositionScanner, ExchangePosition, ScanReport
from execution.adoption_engine import AdoptionEngine, AdoptionConfig, AdoptionDecision, AdoptionResult
from execution.profit_optimizer import ProfitOptimizer, TrackedPosition, ActionType, OptAction
from execution.checkpoint import PositionCheckpoint  # type: ignore[attr-defined]
from execution.rate_limiter import RateLimiter  # type: ignore[attr-defined]
from execution.exchange_factory import get_order_router, get_ws_feed  # type: ignore[attr-defined]

__all__ = [
    "OrderManager", "OrderManagerConfig", "OrderRequest", "OrderRecord",
    "OrderStatus", "OrderSide", "OrderType",
    "PositionScanner", "ExchangePosition", "ScanReport",
    "AdoptionEngine", "AdoptionConfig", "AdoptionDecision", "AdoptionResult",
    "ProfitOptimizer", "TrackedPosition", "ActionType", "OptAction",
    "Checkpoint", "RateLimiter", "ExchangeFactory",
]
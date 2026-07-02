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
from execution.checkpoint import Checkpoint  # type: ignore[attr-defined]
from execution.rate_limiter import RateLimiter  # type: ignore[attr-defined]
from execution.exchange_factory import ExchangeFactory  # type: ignore[attr-defined]

__all__ = [
    # Order management
    "OrderManager", "OrderManagerConfig", "OrderRequest", "OrderRecord",
    "OrderStatus", "OrderSide", "OrderType",
    # Position management
    "PositionScanner", "ExchangePosition", "ScanReport",
    "AdoptionEngine", "AdoptionConfig", "AdoptionDecision", "AdoptionResult",
    "ProfitOptimizer", "TrackedPosition", "ActionType", "OptAction",
    # Infrastructure
    "Checkpoint", "RateLimiter", "ExchangeFactory",
]

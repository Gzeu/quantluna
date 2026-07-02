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
from execution.checkpoint import PositionCheckpoint, PositionState
from execution.rate_limiter import RateLimiter
from execution.exchange_factory import ExchangeFactory

# Alias backward-compat
Checkpoint = PositionCheckpoint

__all__ = [
    # Order management
    "OrderManager", "OrderManagerConfig", "OrderRequest", "OrderRecord",
    "OrderStatus", "OrderSide", "OrderType",
    # Position management
    "PositionScanner", "ExchangePosition", "ScanReport",
    "AdoptionEngine", "AdoptionConfig", "AdoptionDecision", "AdoptionResult",
    "ProfitOptimizer", "TrackedPosition", "ActionType", "OptAction",
    # Infrastructure
    "PositionCheckpoint", "PositionState", "Checkpoint",
    "RateLimiter", "ExchangeFactory",
]

"""
risk package — public exports
"""
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, TripReason, TripEvent
from risk.kelly import KellySizer, KellySimpleResult, KellyCrossPair, KellyConfig

try:
    from risk.portfolio_risk import PortfolioRisk  # type: ignore[attr-defined]
except Exception:
    PortfolioRisk = None  # type: ignore[assignment]

__all__ = [
    "CircuitBreaker", "CircuitBreakerConfig", "TripReason", "TripEvent",
    "KellySizer", "KellySimpleResult", "KellyCrossPair", "KellyConfig",
    "PortfolioRisk",
]

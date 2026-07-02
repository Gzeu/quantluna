"""
risk package — public exports
"""
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, TripReason, TripEvent

try:
    from risk.kelly import KellySizer  # type: ignore[attr-defined]
except Exception:
    KellySizer = None  # type: ignore[assignment]

try:
    from risk.portfolio_risk import PortfolioRisk  # type: ignore[attr-defined]
except Exception:
    PortfolioRisk = None  # type: ignore[assignment]

__all__ = [
    "CircuitBreaker", "CircuitBreakerConfig", "TripReason", "TripEvent",
    "KellySizer",
    "PortfolioRisk",
]

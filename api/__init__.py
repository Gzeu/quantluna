"""
api/__init__.py  —  QuantLuna API package
Exporta routerele principale pentru import direct.
"""
from api.backtest      import router as backtest_router
from api.data          import router as data_router
from api.health        import router as health_router
from api.live          import router as live_router
from api.notifications import router as notifications_router
from api.optimize      import router as optimize_router
from api.optimizer     import optimizer_router
from api.pairs         import router as pairs_router
from api.risk          import router as risk_router
from api.services      import services_router
from api.sizing        import router as sizing_router
from api.strategy      import router as strategy_router
from api.watchdog      import watchdog_router

__all__ = [
    "backtest_router",
    "data_router",
    "health_router",
    "live_router",
    "notifications_router",
    "optimize_router",
    "optimizer_router",
    "pairs_router",
    "risk_router",
    "services_router",
    "sizing_router",
    "strategy_router",
    "watchdog_router",
]

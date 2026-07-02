"""
api/__init__.py  —  QuantLuna API package
Sprint 21: strategy router added to public exports.
"""
from api.backtest import router as backtest_router          # noqa: F401
from api.strategy import router as strategy_router          # noqa: F401
from api.strategy import register_selector, clear_selector  # noqa: F401
from api.backtest import (
    CompareResponse,  # noqa: F401
    JobSummary,       # noqa: F401
    RadarData,        # noqa: F401
    RadarSeries,      # noqa: F401
    DiffMatrix,       # noqa: F401
    ParamField,       # noqa: F401
)

__all__ = [
    "backtest_router",
    "strategy_router",
    "register_selector",
    "clear_selector",
    "CompareResponse",
    "JobSummary",
    "RadarData",
    "RadarSeries",
    "DiffMatrix",
    "ParamField",
]

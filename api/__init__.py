"""
api/__init__.py  —  QuantLuna API package

[FIX-5] __all__ declarat explicit pentru tree-shaking și IDE autocomplete clar.
"""
from api.backtest import router as backtest_router  # noqa: F401
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
    "CompareResponse",
    "JobSummary",
    "RadarData",
    "RadarSeries",
    "DiffMatrix",
    "ParamField",
]

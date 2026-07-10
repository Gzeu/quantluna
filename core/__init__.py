"""
core package — public exports
"""
from core.kalman_filter import KalmanHedgeRatio  # type: ignore[attr-defined]
from core.spread import SpreadEngine  # type: ignore[attr-defined]
from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig, SpreadHealthReport, AlertType
from core.volatility_regime import VolatilityRegime  # type: ignore[attr-defined]
from core.cointegration import CointegrationTest  # type: ignore[attr-defined]

__all__ = [
    "KalmanFilter",
    "SpreadCalculator",
    "SpreadMonitor", "SpreadMonitorConfig", "SpreadHealthReport", "AlertType",
    "VolatilityRegime",
    "CointegrationAnalyzer",
]

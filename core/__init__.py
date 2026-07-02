"""
core package — public exports

All imports use the *actual* class names from each module.
Backward-compatible aliases are provided for names that were previously
referenced elsewhere in the codebase (e.g. KalmanFilter, SpreadCalculator).
"""

# Kalman filter
from core.kalman_filter import KalmanHedgeRatio, KalmanState
KalmanFilter = KalmanHedgeRatio  # backward-compat alias

# Spread engine
from core.spread import SpreadEngine
SpreadCalculator = SpreadEngine  # backward-compat alias

# Spread monitor
try:
    from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig, SpreadHealthReport, AlertType
except ImportError:
    SpreadMonitor = SpreadMonitorConfig = SpreadHealthReport = AlertType = None  # type: ignore

# Volatility regime
try:
    from core.volatility_regime import VolatilityRegime, VolRegimeConfig, RegimeLabel
except ImportError:
    VolatilityRegime = VolRegimeConfig = RegimeLabel = None  # type: ignore

# Cointegration
try:
    from core.cointegration import CointegrationAnalyzer
except ImportError:
    CointegrationAnalyzer = None  # type: ignore

__all__ = [
    # Kalman
    "KalmanHedgeRatio",
    "KalmanFilter",      # alias
    "KalmanState",
    # Spread
    "SpreadEngine",
    "SpreadCalculator",  # alias
    # Spread monitor
    "SpreadMonitor",
    "SpreadMonitorConfig",
    "SpreadHealthReport",
    "AlertType",
    # Volatility
    "VolatilityRegime",
    "VolRegimeConfig",
    "RegimeLabel",
    # Cointegration
    "CointegrationAnalyzer",
]

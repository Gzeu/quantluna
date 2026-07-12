"""
core package — public exports
"""
from core.kalman_filter import KalmanHedgeRatio          # type: ignore[attr-defined]
from core.spread import SpreadEngine                       # type: ignore[attr-defined]
from core.spread_monitor import (                          # type: ignore[attr-defined]
    SpreadMonitor,
    SpreadMonitorConfig,
    SpreadHealthReport,
    AlertType,
)
from core.volatility_regime import VolatilityRegime        # type: ignore[attr-defined]
from core.cointegration import CointegrationTest           # type: ignore[attr-defined]
from core.monitoring_watchdog import (                     # S46 — G2 fix
    MonitoringWatchdog,
    PairThreshold,
    WatchdogAlert,
)
from core.workflow_orchestrator import (                   # S46 — G2 fix
    WorkflowOrchestrator,
    StartupContext,
)

__all__ = [
    # existing
    "KalmanHedgeRatio",
    "SpreadEngine",
    "SpreadMonitor", "SpreadMonitorConfig", "SpreadHealthReport", "AlertType",
    "VolatilityRegime",
    "CointegrationTest",
    # S46 — monitoring & orchestration
    "MonitoringWatchdog", "PairThreshold", "WatchdogAlert",
    "WorkflowOrchestrator", "StartupContext",
]

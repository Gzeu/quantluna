"""
core/workflow_orchestrator.py  —  SHIM / re-export layer

⚠ï¸  DEPRECATED — nu importa direct din acest modul.

Locul canonic al WorkflowOrchestrator (startup workflow cu 5 faze) este:
    execution/workflow_orchestrator.py

Acest fisier exista exclusiv pentru backward-compatibility cu codul care
importa din `core.workflow_orchestrator`. Va fi sters in Sprint 32.

Migration guide::

    # Vechi (deprecat)
    from core.workflow_orchestrator import WorkflowOrchestrator

    # Nou (canonic)
    from execution.workflow_orchestrator import WorkflowOrchestrator

Nota arhitecturala:
    `execution/workflow_orchestrator.py` contine orchestratorul de STARTUP
    (HealthCheck -> PositionScanner -> ResumeManager -> AdoptionEngine ->
    ProfitOptimizer -> BybitLiveRunner). Acesta este folosit de `main.py`.

    Orchestratorul multi-market v2.2 (MonitoringWatchdog, AutoReoptimizer,
    multi-gather) a fost redenumit `MultiMarketOrchestrator` si ramane in
    `core/multi_market_orchestrator.py` (Sprint 32).
"""
from __future__ import annotations

import warnings

warnings.warn(
    "Importul din 'core.workflow_orchestrator' este deprecat si va fi sters "
    "in Sprint 32. Foloseste 'from execution.workflow_orchestrator import "
    "WorkflowOrchestrator' in loc. "
    "Vezi core/workflow_orchestrator.py pentru migration guide.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-exporta tot ce e public din locul canonic
from execution.workflow_orchestrator import (  # noqa: E402, F401
    WorkflowOrchestrator,
    StartupContext,
)

__all__ = [
    "WorkflowOrchestrator",
    "StartupContext",
]

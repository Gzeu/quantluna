"""
execution/workflow_orchestrator.py  —  Backward-compatibility shim

CANONICAL LOCATION: core/workflow_orchestrator.py (v2.2)

This shim exists solely so that the import in main.py continues to work:

    from execution.workflow_orchestrator import WorkflowOrchestrator

New code MUST import from core directly:

    from core.workflow_orchestrator import WorkflowOrchestrator, StartupContext

BACKGROUND
----------
Sprint 28 introduced this file as the primary orchestrator.
Sprint S44b promoted core/workflow_orchestrator.py to v2.2 with
MonitoringWatchdog integration (gather loop), AutoReoptimizer, and
AlertDispatcher support.

The two files diverged:
  - execution/ (v2, Sprint 28) — StartupContext with should_halt, 5-phase
    startup workflow (HealthCheck -> Scan -> Reconcile -> Adopt -> Runner)
  - core/ (v2.2, S44b) — richer StartupContext, MonitoringWatchdog,
    AutoReoptimizer gather(), from_env() factory, api/services registration

RESOLUTION (fix #19)
--------------------
core/workflow_orchestrator.py is the canonical implementation going forward.
This file re-exports everything from core/ so existing callers are unaffected.
The Sprint 28 startup phases (HealthCheck, Scan, Reconcile, Adopt) are
preserved in execution/startup_phases.py for use by WorkflowOrchestrator
when called from main.py via from_runner_cfg().

To be REMOVED: Sprint 31 (after all callers migrated to core/ import).
"""
from __future__ import annotations

import warnings

warnings.warn(
    "[QuantLuna] Importing WorkflowOrchestrator from 'execution.workflow_orchestrator' "
    "is deprecated. Use 'from core.workflow_orchestrator import WorkflowOrchestrator' "
    "instead. This shim will be removed in Sprint 31.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export canonical implementation
from core.workflow_orchestrator import (  # noqa: F401, E402
    WorkflowOrchestrator,
    StartupContext,
)

__all__ = ["WorkflowOrchestrator", "StartupContext"]

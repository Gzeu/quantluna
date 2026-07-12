"""
core/multi_market_orchestrator.py  —  MultiMarketOrchestrator v2.2 (stub)

Sprint S45 — stub creat pentru a preveni ImportError la enable_spot=True
sau force_multi_market=True pana la implementarea completa in Sprint 32.

Contextul arhitectural:
    In Sprint S44b `core/workflow_orchestrator.py` continea un orchestrator
    multi-market complet (MonitoringWatchdog, AutoReoptimizer, asyncio.gather
    cu 3 taskuri). Acel fisier a fost rescris ca shim catre
    `execution/workflow_orchestrator.py` (orchestratorul de startup canonic).

    Orchestratorul multi-market v2.2 este extras aici si va fi implementat
    complet in Sprint 32 cand multi-market mode devine prioritate de productie.

Sprint 32 TODO:
    - Extrage complet logica din fostul core/workflow_orchestrator.py v2.2
    - Implementeaza MultiMarketOrchestrator.from_env()
    - Integreaza in api/main.py lifespan alaturi de WorkflowOrchestrator
    - Adauga teste in tests/test_multi_market_orchestrator.py

Usage (dupa Sprint 32)::

    from core.multi_market_orchestrator import MultiMarketOrchestrator
    orch = MultiMarketOrchestrator.from_env(dispatcher=alert_dispatcher)
    await orch.start_runner()
"""
from __future__ import annotations

from loguru import logger


class MultiMarketOrchestrator:
    """
    Stub MultiMarketOrchestrator — Sprint 32.

    Gestioneaza orchestrarea multi-market (futures + spot + margin) cu:
    - MonitoringWatchdog ca task autonom
    - AutoReoptimizer WFO saptamanal
    - asyncio.gather() cu 3 task-uri paralele

    Aceasta clasa este un STUB. Instantierea este permisa dar
    `start_runner()` va ridica NotImplementedError pana la Sprint 32.
    """

    VERSION = "2.2.0-stub"

    def __init__(self, runner_cfg=None, notifier_bus=None, dispatcher=None) -> None:
        self._runner_cfg = runner_cfg
        self._bus = notifier_bus
        self._dispatcher = dispatcher
        logger.warning(
            "[MultiMarketOrchestrator] STUB v2.2 — implementare completa in Sprint 32. "
            "Pentru trading live foloseste execution.workflow_orchestrator.WorkflowOrchestrator."
        )

    @classmethod
    def from_env(cls, dispatcher=None) -> "MultiMarketOrchestrator":
        """Builder din env vars — STUB."""
        import os
        import types
        cfg = types.SimpleNamespace(
            pairs=os.getenv("PAIRS", "BTCUSDT-ETHUSDT").split(","),
            enable_spot=os.getenv("ENABLE_SPOT", "false").lower() == "true",
            enable_margin=os.getenv("ENABLE_MARGIN", "false").lower() == "true",
            enable_reoptimizer=os.getenv("ENABLE_REOPTIMIZER", "true").lower() == "true",
            enable_watchdog=os.getenv("WATCHDOG_ENABLED", "true").lower() == "true",
        )
        return cls(runner_cfg=cfg, dispatcher=dispatcher)

    async def start_runner(self) -> None:
        """STUB — ridica NotImplementedError pana la Sprint 32."""
        raise NotImplementedError(
            "MultiMarketOrchestrator.start_runner() nu este implementat. "
            "Implementare completa planificata in Sprint 32. "
            "Foloseste execution.workflow_orchestrator.WorkflowOrchestrator pentru trading live."
        )

    async def stop_runner(self) -> None:
        """STUB — no-op."""
        logger.info("[MultiMarketOrchestrator] stop_runner() — stub no-op")

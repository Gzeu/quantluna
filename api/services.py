"""
api/services.py  -  QuantLuna Services Control API v1.0

Sprint S41 (2026-07-12):
  Endpoint-uri FastAPI pentru control servicii din dashboard:

    GET  /api/services/list          - lista tuturor serviciilor + status
    GET  /api/services/{name}/status - status detaliat serviciu
    POST /api/services/{name}/start  - porneste serviciu
    POST /api/services/{name}/stop   - opreste serviciu
    POST /api/services/{name}/restart- restart serviciu
    GET  /api/services/ws            - WebSocket status live (1s refresh)

  Servicii gestionate:
    - futures_runner   : BybitLiveRunner (Futures Linear)
    - spot_runner      : SpotOrderRouter + SpotWalletScanner
    - margin_guard     : MarginRiskGuard
    - capital_allocator: CapitalAllocator
    - auto_reoptimizer : AutoReoptimizer (scheduler WFO)
    - hedge_{pair}     : SingleHedgeManager per pereche
    - withdrawal_guard : WithdrawalGuard

  Fiecare serviciu expune: name, status, uptime, last_error, config_summary
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
except ImportError:
    raise RuntimeError("fastapi necesar")

from loguru import logger

services_router = APIRouter(tags=["services"])

# ---------------------------------------------------------------------------
# Registru global de servicii - populat din WorkflowOrchestrator
# ---------------------------------------------------------------------------

class ServiceEntry:
    def __init__(
        self,
        name: str,
        display_name: str,
        description: str,
        component: Any = None,
        can_toggle: bool = True,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.description = description
        self.component = component
        self.can_toggle = can_toggle
        self.status: str = "stopped"   # running | stopped | error | starting | stopping
        self.enabled: bool = False
        self.started_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        self.restart_count: int = 0

    def uptime_s(self) -> Optional[float]:
        if self.started_at and self.status == "running":
            return time.monotonic() - self.started_at
        return None

    def to_dict(self) -> Dict[str, Any]:
        uptime = self.uptime_s()
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "status": self.status,
            "enabled": self.enabled,
            "can_toggle": self.can_toggle,
            "uptime_s": round(uptime, 1) if uptime is not None else None,
            "uptime_human": _fmt_uptime(uptime),
            "last_error": self.last_error,
            "restart_count": self.restart_count,
        }


def _fmt_uptime(s: Optional[float]) -> str:
    if s is None:
        return "-"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


# Registru global
_SERVICES: Dict[str, ServiceEntry] = {}


def register_service(
    name: str,
    display_name: str,
    description: str,
    component: Any = None,
    enabled: bool = False,
    can_toggle: bool = True,
) -> ServiceEntry:
    """Inregistreaza un serviciu in registru. Apelat din WorkflowOrchestrator."""
    entry = ServiceEntry(
        name=name,
        display_name=display_name,
        description=description,
        component=component,
        can_toggle=can_toggle,
    )
    entry.enabled = enabled
    entry.status = "running" if enabled else "stopped"
    if enabled:
        entry.started_at = time.monotonic()
    _SERVICES[name] = entry
    return entry


def update_service_status(name: str, status: str, error: str = None) -> None:
    """Actualizeaza statusul unui serviciu (apelat din componente)."""
    if name in _SERVICES:
        _SERVICES[name].status = status
        if status == "running":
            _SERVICES[name].started_at = time.monotonic()
            _SERVICES[name].last_error = None
        elif status == "error" and error:
            _SERVICES[name].last_error = error


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@services_router.get("/list")
async def list_services() -> Dict[str, Any]:
    """Lista tuturor serviciilor inregistrate cu statusul curent."""
    services = [s.to_dict() for s in _SERVICES.values()]
    running = sum(1 for s in _SERVICES.values() if s.status == "running")
    return {
        "services": services,
        "total": len(services),
        "running": running,
        "stopped": len(services) - running,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@services_router.get("/{name}/status")
async def get_service_status(name: str) -> Dict[str, Any]:
    if name not in _SERVICES:
        raise HTTPException(status_code=404, detail=f"Serviciu '{name}' negasit")
    return _SERVICES[name].to_dict()


@services_router.post("/{name}/start")
async def start_service(name: str) -> Dict[str, Any]:
    """Porneste un serviciu oprit."""
    if name not in _SERVICES:
        raise HTTPException(status_code=404, detail=f"Serviciu '{name}' negasit")
    svc = _SERVICES[name]
    if not svc.can_toggle:
        raise HTTPException(
            status_code=403,
            detail=f"Serviciu '{name}' nu poate fi pornit/oprit manual"
        )
    if svc.status == "running":
        return {"status": "already_running", "service": svc.to_dict()}

    svc.status = "starting"
    logger.info("[ServicesAPI] START {} requestat", name)

    # Porneste componenta daca are metoda start/run_loop
    comp = svc.component
    if comp is not None:
        try:
            if hasattr(comp, "start"):
                svc.task = asyncio.create_task(_run_service(svc, comp.start()))
            elif hasattr(comp, "run_loop"):
                svc.task = asyncio.create_task(_run_service(svc, comp.run_loop()))
            elif hasattr(comp, "watch_loop"):
                svc.task = asyncio.create_task(_run_service(svc, comp.watch_loop()))
            else:
                svc.status = "error"
                svc.last_error = "Componenta nu are metoda start/run_loop/watch_loop"
                raise HTTPException(status_code=500, detail=svc.last_error)
        except HTTPException:
            raise
        except Exception as exc:
            svc.status = "error"
            svc.last_error = str(exc)
            raise HTTPException(status_code=500, detail=str(exc))
    else:
        # Serviciu fara componenta -> doar marcam running
        svc.status = "running"
        svc.started_at = time.monotonic()
        svc.enabled = True

    return {"status": "started", "service": svc.to_dict()}


@services_router.post("/{name}/stop")
async def stop_service(name: str) -> Dict[str, Any]:
    """Opreste un serviciu activ."""
    if name not in _SERVICES:
        raise HTTPException(status_code=404, detail=f"Serviciu '{name}' negasit")
    svc = _SERVICES[name]
    if not svc.can_toggle:
        raise HTTPException(
            status_code=403,
            detail=f"Serviciu '{name}' nu poate fi oprit manual"
        )
    if svc.status == "stopped":
        return {"status": "already_stopped", "service": svc.to_dict()}

    svc.status = "stopping"
    logger.info("[ServicesAPI] STOP {} requestat", name)

    comp = svc.component
    if comp is not None and hasattr(comp, "stop"):
        try:
            comp.stop()
        except Exception as exc:
            logger.warning("[ServicesAPI] stop() error {}: {}", name, exc)

    if svc.task and not svc.task.done():
        svc.task.cancel()

    svc.status = "stopped"
    svc.enabled = False
    svc.started_at = None
    return {"status": "stopped", "service": svc.to_dict()}


@services_router.post("/{name}/restart")
async def restart_service(name: str) -> Dict[str, Any]:
    """Restart: stop + start."""
    await stop_service(name)
    await asyncio.sleep(0.5)
    result = await start_service(name)
    _SERVICES[name].restart_count += 1
    return {"status": "restarted", "service": _SERVICES[name].to_dict()}


# ---------------------------------------------------------------------------
# WebSocket live status
# ---------------------------------------------------------------------------

@services_router.websocket("/ws")
async def services_ws(websocket: WebSocket):
    """
    WebSocket: trimite statusul tuturor serviciilor la fiecare 1s.
    Clientul se poate deconecta oricand.
    """
    await websocket.accept()
    try:
        while True:
            data = {
                "services": [s.to_dict() for s in _SERVICES.values()],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await websocket.send_json(data)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("[ServicesWS] {}", exc)


async def _run_service(svc: ServiceEntry, coro) -> None:
    """Wrapper asyncio task pentru un serviciu."""
    svc.status = "running"
    svc.started_at = time.monotonic()
    svc.enabled = True
    try:
        await coro
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        svc.status = "error"
        svc.last_error = str(exc)
        logger.error("[ServiceTask] {} eroare: {}", svc.name, exc)
        return
    svc.status = "stopped"
    svc.enabled = False

"""
api/diagnostics.py — Operational diagnostics endpoints (S48 P0).

Endpoints:
  GET  /api/diagnostics/bybit-traffic  — rate limits, circuit state, WS health
  POST /api/bybit/traffic/pause-noncritical — pause non-critical traffic
  POST /api/bybit/traffic/resume-noncritical — resume non-critical traffic
  GET  /api/health/services — all service health
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

diagnostics_router = APIRouter(tags=["diagnostics"])

_TRAFFIC_STATE: Dict[str, Any] = {"controller": None, "lock": None}


def set_traffic_state(state: Dict[str, Any]) -> None:
    _TRAFFIC_STATE.update(state)


@diagnostics_router.get("/api/diagnostics/bybit-traffic")
async def bybit_traffic():
    """Full traffic diagnostics: rate limits, circuit state, WS health."""
    ctrl = _TRAFFIC_STATE.get("controller")
    if ctrl is None:
        return {"status": "no_controller", "message": "Traffic controller not initialized"}

    return ctrl.snapshot()


@diagnostics_router.get("/api/health/services")
async def service_health():
    """All service health statuses."""
    ctrl = _TRAFFIC_STATE.get("controller")
    lock = _TRAFFIC_STATE.get("lock")

    services = {
        "api": "UP",
        "runner": "UNKNOWN",
        "public_ws": "UNKNOWN",
        "private_ws": "UNKNOWN",
        "state_store": "UP",
        "scanner": "IDLE",
        "optimizer": "IDLE",
        "notifier": "UP",
    }

    if ctrl is not None:
        snap = ctrl.snapshot()
        services["bybit_rest"] = snap["circuit_breaker"]
        services["entries"] = "ENABLED" if snap["config"].get("entries_enabled") else "DISABLED"
        services["sync_only"] = snap["config"].get("sync_only", True)

    if lock is not None:
        owner = lock.get_owner()
        services["singleton_owner"] = f"PID {owner.pid}" if owner else "none"

    return {
        "services": services,
        "timestamp": __import__("time").time(),
    }


@diagnostics_router.post("/api/bybit/traffic/pause-noncritical")
async def pause_non_critical():
    """Pause all non-critical Bybit traffic (market data, UI diagnostics)."""
    ctrl = _TRAFFIC_STATE.get("controller")
    if ctrl is None:
        raise HTTPException(status_code=503, detail="No traffic controller")
    await ctrl.pause_non_critical()
    return {"status": "paused", "message": "Non-critical Bybit traffic paused"}


@diagnostics_router.post("/api/bybit/traffic/resume-noncritical")
async def resume_non_critical():
    """Resume non-critical Bybit traffic."""
    ctrl = _TRAFFIC_STATE.get("controller")
    if ctrl is None:
        raise HTTPException(status_code=503, detail="No traffic controller")
    await ctrl.resume_non_critical()
    return {"status": "resumed", "message": "Non-critical Bybit traffic resumed"}

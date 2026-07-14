"""
api/services.py — S37 new
GET /api/services/list — status procese QuantLuna.
Raspunde cu lista de servicii si statusul lor.
Foloseste psutil daca disponibil, altfel fallback static.
"""
from __future__ import annotations

import os
import time
from typing import List, Dict, Any

try:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("fastapi necesar")

services_router = APIRouter(tags=["services"])

# Procesele asteptate sa ruleze in productie
_EXPECTED_PROCESSES = [
    "main.py",
    "dashboard/server.py",
    "risk/dashboard_engine.py",
    "execution/bybit_live_runner.py",
    "execution/bybit_ws_feed.py",
    "core/spread_monitor.py",
    "strategy/regime_filter.py",
]


def _get_services_psutil() -> List[Dict[str, Any]]:
    """Interogheaza procesele Python active via psutil."""
    import psutil
    import sys

    services = []
    python_procs: Dict[str, psutil.Process] = {}

    for proc in psutil.process_iter(["pid", "name", "cmdline", "status", "cpu_percent",
                                      "memory_info", "create_time"]):
        try:
            cmd = proc.info.get("cmdline") or []
            if not cmd:
                continue
            cmd_str = " ".join(cmd)
            for expected in _EXPECTED_PROCESSES:
                if expected in cmd_str:
                    python_procs[expected] = proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    for name in _EXPECTED_PROCESSES:
        proc = python_procs.get(name)
        if proc:
            try:
                mem  = proc.memory_info().rss / 1024 / 1024  # MB
                cpu  = proc.cpu_percent(interval=0.1)
                uptime_s = time.time() - proc.create_time()
                h, rem   = divmod(int(uptime_s), 3600)
                m, s     = divmod(rem, 60)
                uptime   = f"{h:02d}:{m:02d}:{s:02d}"
                services.append({
                    "name":   name.split("/")[-1].replace(".py", ""),
                    "status": "running",
                    "pid":    proc.pid,
                    "uptime": uptime,
                    "cpu":    round(cpu, 1),
                    "mem":    round(mem, 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                services.append({
                    "name":   name.split("/")[-1].replace(".py", ""),
                    "status": "stopped",
                })
        else:
            services.append({
                "name":   name.split("/")[-1].replace(".py", ""),
                "status": "stopped",
            })
    return services


def _get_services_static() -> List[Dict[str, Any]]:
    """Fallback static — psutil indisponibil."""
    return [
        {"name": name.split("/")[-1].replace(".py", ""), "status": "unknown"}
        for name in _EXPECTED_PROCESSES
    ]


@services_router.get("/list")
async def list_services() -> JSONResponse:
    """Returneaza statusul tuturor serviciilor QuantLuna."""
    try:
        import psutil  # noqa
        services = _get_services_psutil()
    except ImportError:
        services = _get_services_static()
    except Exception as exc:
        services = _get_services_static()

    return JSONResponse(content={
        "services":   services,
        "ts":         time.time(),
        "total":      len(services),
        "running":    sum(1 for s in services if s["status"] == "running"),
    })

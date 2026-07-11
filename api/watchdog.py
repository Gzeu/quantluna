"""
api/watchdog.py  -  QuantLuna Watchdog API v1.0
Sprint S44 (2026-07-12)

Endpoints:
  GET  /api/watchdog/status              - stare watchdog + alerte recente
  GET  /api/watchdog/thresholds          - thresholds active per pereche
  POST /api/watchdog/thresholds/{pair}   - update threshold on-the-fly
  POST /api/watchdog/silence/{pair}      - mute alerte pentru N minute
  POST /api/watchdog/unsilence/{pair}    - anuleaza silence
  GET  /api/watchdog/alerts              - istoric alerte paginate
  POST /api/watchdog/test/{pair}         - trimite alerta test pe Telegram
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, HTTPException, Query
    from pydantic import BaseModel
except ImportError:
    raise RuntimeError("fastapi si pydantic necesare")

from loguru import logger

watchdog_router = APIRouter(tags=["watchdog"])

_WATCHDOG_STATE: Dict[str, Any] = {
    "watchdog":   None,
    "dispatcher": None,
}


def set_watchdog_state(state: Dict[str, Any]) -> None:
    _WATCHDOG_STATE.update(state)


def _get_wd():
    wd = _WATCHDOG_STATE.get("watchdog")
    if wd is None:
        raise HTTPException(
            status_code=503,
            detail="MonitoringWatchdog nu e initializat. Porneste WorkflowOrchestrator.",
        )
    return wd


# ── Schemas ───────────────────────────────────────────────────────────────────

class ThresholdUpdate(BaseModel):
    sharpe_min:    Optional[float] = None
    max_drawdown:  Optional[float] = None
    z_max:         Optional[float] = None
    hl_max:        Optional[float] = None
    loss_streak:   Optional[int]   = None
    action:        Optional[str]   = None   # ALERT_ONLY | REDUCE_SIZE | HALT


# ── Endpoints ──────────────────────────────────────────────────────────────────

@watchdog_router.get("/status")
async def get_watchdog_status() -> Dict[str, Any]:
    """Stare globala watchdog + ultimele 10 alerte."""
    wd = _get_wd()
    return wd.get_status()


@watchdog_router.get("/thresholds")
async def get_thresholds() -> Dict[str, Any]:
    """Thresholds active per pereche (cu silenced_until)."""
    wd = _get_wd()
    return {"thresholds": wd.get_thresholds()}


@watchdog_router.post("/thresholds/{pair}")
async def update_threshold(pair: str, body: ThresholdUpdate) -> Dict[str, Any]:
    """
    Actualizeaza threshold-urile pentru o pereche on-the-fly.
    Campurile None sunt ignorate (partial update).

    Exemplu body:
        {"sharpe_min": 0.5, "action": "HALT"}
    """
    wd = _get_wd()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Niciun camp de actualizat.")
    wd.update_threshold(pair, **updates)
    return {
        "pair": pair,
        "updated": updates,
        "current": wd.get_thresholds().get(pair),
    }


@watchdog_router.post("/silence/{pair}")
async def silence_pair(
    pair: str,
    minutes: int = Query(default=60, ge=1, le=1440),
) -> Dict[str, Any]:
    """Opreste alertele pentru o pereche N minute (max 24h)."""
    wd = _get_wd()
    wd.silence(pair, minutes)
    return {
        "pair":    pair,
        "silenced_minutes": minutes,
        "silenced_until": wd.get_thresholds().get(pair, {}).get("silenced_until"),
    }


@watchdog_router.post("/unsilence/{pair}")
async def unsilence_pair(pair: str) -> Dict[str, Any]:
    """Anuleaza silence pentru o pereche."""
    wd = _get_wd()
    wd.silence(pair, minutes=0)    # silence(0) seteaza silenced_until in trecut
    return {"pair": pair, "silenced_until": None}


@watchdog_router.get("/alerts")
async def get_alerts(
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    """Istoric alerte (cele mai recente first, limit max 200)."""
    wd = _get_wd()
    alerts = wd.get_alerts(limit)
    return {
        "alerts": list(reversed(alerts)),
        "total":  len(alerts),
    }


@watchdog_router.post("/test/{pair}")
async def send_test_alert(pair: str) -> Dict[str, Any]:
    """Trimite o alerta test pe Telegram pentru a verifica integrarea."""
    dispatcher = _WATCHDOG_STATE.get("dispatcher")
    if dispatcher is None:
        raise HTTPException(status_code=503, detail="Dispatcher ne-initializat.")
    try:
        from notifications.event_types import AlertEvent, EventType
        await dispatcher.emit(AlertEvent(
            event_type=EventType.RISK_ALERT,
            payload={
                "text": (
                    f"✅ <b>QuantLuna Watchdog — Test Alert</b>\n"
                    f"Pereche: <code>{pair}</code>\n"
                    f"Timestamp: <i>{datetime.now(timezone.utc).isoformat()}</i>\n"
                    f"Sistem OK — alerta de test trimisa cu succes."
                ),
                "pair": pair,
                "test": True,
            },
        ))
        return {"status": "sent", "pair": pair}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

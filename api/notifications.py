"""
QuantLuna — Notifications API
Sprint 29

Endpoints:
  GET  /notifications/status   — starea dispatcher (queue, sent, failed)
  POST /notifications/test     — trimite un alert test pe toate canalele
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from notifications.event_types import AlertEvent, EventType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

# dispatcher setat la startup din api/main.py
_dispatcher = None


def set_dispatcher(dispatcher) -> None:
    global _dispatcher
    _dispatcher = dispatcher


@router.get("/status")
def notifications_status():
    """
    GET /notifications/status
    Returneaza starea AlertDispatcher: cozile, sent/failed counts, canal status.
    """
    if _dispatcher is None:
        return {"status": "not_initialized", "telegram": False, "discord": False}
    return _dispatcher.status()


class TestAlertRequest(BaseModel):
    message: Optional[str] = "Test alert QuantLuna — Sprint 29"
    event_type: Optional[str] = "TEST"


@router.post("/test")
async def send_test_alert(req: TestAlertRequest):
    """
    POST /notifications/test
    Trimite un alert TEST pe toate canalele configurate.
    {"message": "...", "event_type": "TEST"}
    """
    if _dispatcher is None:
        return {"sent": False, "reason": "dispatcher not initialized"}

    try:
        ev_type = EventType(req.event_type or "TEST")
    except ValueError:
        ev_type = EventType.TEST

    event = AlertEvent(
        event_type=ev_type,
        payload={"message": req.message or "", "source": "api_test"},
    )
    sent = await _dispatcher.emit_sync(event)
    return {"sent": sent, "event_type": ev_type.value, "message": req.message}

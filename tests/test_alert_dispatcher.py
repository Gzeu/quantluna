"""
QuantLuna — Tests: notifications/alert_dispatcher.py
Sprint 29  |  10 tests
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notifications.alert_dispatcher import AlertDispatcher
from notifications.event_types import AlertEvent, EventType, Severity


@pytest.fixture
def dispatcher():
    tg = MagicMock()
    tg.enabled = True
    tg.send    = AsyncMock(return_value=True)
    tg.close   = AsyncMock()

    dc = MagicMock()
    dc.enabled = True
    dc.send    = AsyncMock(return_value=True)
    dc.close   = AsyncMock()

    return AlertDispatcher(telegram=tg, discord=dc)


def _event(ev_type=EventType.TEST, **kwargs) -> AlertEvent:
    return AlertEvent(event_type=ev_type, payload=kwargs)


class TestAlertDispatcher:

    @pytest.mark.asyncio
    async def test_emit_sync_sends_to_both(self, dispatcher):
        event  = _event()
        result = await dispatcher.emit_sync(event)
        assert result is True
        dispatcher.telegram.send.assert_awaited_once()
        dispatcher.discord.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sent_count_increments(self, dispatcher):
        await dispatcher.emit_sync(_event())
        await dispatcher.emit_sync(_event())
        assert dispatcher._sent_count == 2

    @pytest.mark.asyncio
    async def test_failed_count_on_error(self, dispatcher):
        dispatcher.telegram.send = AsyncMock(side_effect=Exception("boom"))
        dispatcher.discord.send  = AsyncMock(return_value=False)
        await dispatcher.emit_sync(_event())
        assert dispatcher._failed_count == 1

    @pytest.mark.asyncio
    async def test_rate_limit_dd_alert(self, dispatcher):
        e1 = _event(EventType.DD_ALERT, current_dd=0.10)
        e2 = _event(EventType.DD_ALERT, current_dd=0.11)
        await dispatcher.emit(e1)
        result = await dispatcher.emit(e2)   # trebuie rate-limited
        assert result is False

    @pytest.mark.asyncio
    async def test_halt_cascade_always_sent(self, dispatcher):
        e1 = _event(EventType.HALT_CASCADE, reason="DD")
        e2 = _event(EventType.HALT_CASCADE, reason="manual")
        r1 = await dispatcher.emit(e1)
        r2 = await dispatcher.emit(e2)
        assert r1 is True
        assert r2 is True   # halt cascade are min_interval=0

    @pytest.mark.asyncio
    async def test_queue_full_drops_event(self, dispatcher):
        # Umple queue fara worker
        disp = AlertDispatcher(telegram=dispatcher.telegram, discord=dispatcher.discord, queue_size=2)
        for _ in range(5):
            disp._last_sent.clear()  # bypass rate limit
            await disp.emit(_event())
        assert disp._queue.qsize() <= 2

    @pytest.mark.asyncio
    async def test_status_returns_dict(self, dispatcher):
        status = dispatcher.status()
        assert "running" in status
        assert "sent_count" in status
        assert "queue_size" in status

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, dispatcher):
        await dispatcher.start()
        assert dispatcher._running is True
        await dispatcher.stop()
        assert dispatcher._running is False

    @pytest.mark.asyncio
    async def test_trade_open_event_payload(self, dispatcher):
        event = AlertEvent(
            event_type=EventType.TRADE_OPEN,
            payload={"pair": "BTC/ETH", "notional_usdt": 1200.0, "leverage": 2.0},
        )
        result = await dispatcher.emit_sync(event)
        assert result is True
        call_arg = dispatcher.telegram.send.call_args[0][0]
        assert call_arg.event_type == EventType.TRADE_OPEN

    @pytest.mark.asyncio
    async def test_severity_critical_for_halt(self, dispatcher):
        event = AlertEvent(event_type=EventType.HALT_CASCADE, payload={})
        assert event.severity == Severity.CRITICAL
        assert event.color == 0xD50000

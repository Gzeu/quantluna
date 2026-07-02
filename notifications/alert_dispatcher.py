"""
QuantLuna — AlertDispatcher
Sprint 29

Dispatcher central pentru alerte:
  - Primeste AlertEvent
  - Fan-out la Telegram + Discord simultan (asyncio.gather)
  - Async queue intern (nu blocheaza trading loop)
  - Rate limit: min 2s intre mesaje acelasi tip (anti-spam)
  - Retry logic delegat fiecarui notifier
  - Logging complet pentru auditare

Usage:
    from notifications.alert_dispatcher import AlertDispatcher
    from notifications.event_types import AlertEvent, EventType

    dispatcher = AlertDispatcher()
    await dispatcher.start()   # porneste worker task

    # Din orice modul:
    await dispatcher.emit(AlertEvent(
        event_type=EventType.TRADE_OPEN,
        payload={"pair": "BTCUSDT/ETHUSDT", "notional_usdt": 1200.0, ...}
    ))

    await dispatcher.stop()   # graceful shutdown
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, Optional

from notifications.event_types import AlertEvent, EventType
from notifications.telegram_notifier import TelegramNotifier
from notifications.discord_notifier  import DiscordNotifier

logger = logging.getLogger(__name__)

_MIN_INTERVAL_S: Dict[EventType, float] = {
    EventType.TRADE_OPEN:    0.5,
    EventType.TRADE_CLOSE:   0.5,
    EventType.DD_ALERT:      30.0,   # max 1 DD alert la 30s
    EventType.SHARPE_DROP:   60.0,   # max 1 Sharpe alert la 60s
    EventType.HALT_CASCADE:  0.0,    # intotdeauna trimis
    EventType.SYSTEM_ERROR:  10.0,
    EventType.PAIR_START:    0.5,
    EventType.PAIR_STOP:     0.5,
    EventType.TEST:          0.0,
    EventType.SYSTEM_START:  0.0,
}


class AlertDispatcher:
    """
    Central alert dispatcher: Telegram + Discord fan-out, async queue.
    """

    def __init__(
        self,
        telegram: Optional[TelegramNotifier] = None,
        discord:  Optional[DiscordNotifier]  = None,
        queue_size: int = 200,
    ) -> None:
        self.telegram = telegram or TelegramNotifier()
        self.discord  = discord  or DiscordNotifier()
        self._queue:   asyncio.Queue[AlertEvent] = asyncio.Queue(maxsize=queue_size)
        self._last_sent: Dict[EventType, float]  = {}
        self._task:    Optional[asyncio.Task]     = None
        self._running: bool                       = False
        self._sent_count:   int = 0
        self._failed_count: int = 0

    async def start(self):
        """Porneste worker task de dispatch."""
        self._running = True
        self._task    = asyncio.create_task(self._worker(), name="alert-dispatcher")
        logger.info("AlertDispatcher started (Telegram=%s, Discord=%s)",
                    self.telegram.enabled, self.discord.enabled)

    async def stop(self):
        """Graceful shutdown: dreneaza queue + opreste worker."""
        self._running = False
        try:
            await asyncio.wait_for(self._queue.join(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("AlertDispatcher stop: timeout draining queue")
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.telegram.close()
        await self.discord.close()
        logger.info("AlertDispatcher stopped. sent=%d failed=%d",
                    self._sent_count, self._failed_count)

    async def emit(self, event: AlertEvent) -> bool:
        """
        Pune eveniment in queue. Non-blocking.
        Returns False daca queue e plina (drop) sau rate-limited.
        """
        # Rate limit check
        min_interval = _MIN_INTERVAL_S.get(event.event_type, 1.0)
        last = self._last_sent.get(event.event_type, 0.0)
        if min_interval > 0 and (time.monotonic() - last) < min_interval:
            logger.debug(f"Rate limit: skip {event.event_type.value}")
            return False

        try:
            self._queue.put_nowait(event)
            self._last_sent[event.event_type] = time.monotonic()
            return True
        except asyncio.QueueFull:
            logger.warning(f"AlertDispatcher queue full, drop {event.event_type.value}")
            return False

    async def emit_sync(self, event: AlertEvent) -> bool:
        """Dispatch direct (bypass queue) — per usage in test/sync context."""
        return await self._dispatch(event)

    def status(self) -> dict:
        return {
            "running":        self._running,
            "queue_size":     self._queue.qsize(),
            "sent_count":     self._sent_count,
            "failed_count":   self._failed_count,
            "telegram":       self.telegram.enabled,
            "discord":        self.discord.enabled,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _worker(self):
        while self._running or not self._queue.empty():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AlertDispatcher worker error: {e}")

    async def _dispatch(self, event: AlertEvent) -> bool:
        logger.info(f"[ALERT] {event.event_type.value} | {event.severity} | {event.payload}")
        try:
            results = await asyncio.gather(
                self.telegram.send(event),
                self.discord.send(event),
                return_exceptions=True,
            )
            ok = any(r is True for r in results)
            if ok:
                self._sent_count += 1
            else:
                self._failed_count += 1
            return ok
        except Exception as e:
            logger.error(f"AlertDispatcher dispatch error: {e}")
            self._failed_count += 1
            return False

"""
core/ws_reconnect.py — manager de reconectare WebSocket cu backoff exponential.

Furnizeaza logica de retry robusta cu jitter pentru conexiunile WebSocket
la exchange. Compatible cu orice client async.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WSReconnectManager:
    """
    Reconecteaza automat un WebSocket cu backoff exponential + jitter.

    Usage::

        async def connect():
            async with websockets.connect(url) as ws:
                await handle(ws)

        manager = WSReconnectManager(connect_fn=connect)
        await manager.run()
    """

    def __init__(
        self,
        connect_fn: Callable,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        max_retries: Optional[int] = None,
        jitter: float = 0.3,
        on_disconnect: Optional[Callable] = None,
        on_reconnect: Optional[Callable] = None,
    ) -> None:
        self._connect = connect_fn
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._max_retries = max_retries
        self._jitter = jitter
        self._on_disconnect = on_disconnect
        self._on_reconnect = on_reconnect
        self._attempt = 0
        self._running = False

    async def run(self) -> None:
        self._running = True
        self._attempt = 0

        while self._running:
            if self._max_retries is not None and self._attempt >= self._max_retries:
                logger.error("WSReconnect: max retries (%d) reached", self._max_retries)
                break

            try:
                if self._attempt > 0 and self._on_reconnect:
                    try:
                        await self._on_reconnect()
                    except Exception:
                        pass

                logger.info("WSReconnect: connecting (attempt %d)", self._attempt + 1)
                await self._connect()
                self._attempt = 0

            except asyncio.CancelledError:
                self._running = False
                break

            except Exception as exc:
                self._attempt += 1
                delay = self._backoff()

                if self._on_disconnect:
                    try:
                        await self._on_disconnect(exc)
                    except Exception:
                        pass

                logger.warning(
                    "WSReconnect: disconnected (%s), retry in %.1fs (attempt %d)",
                    exc, delay, self._attempt,
                )
                await asyncio.sleep(delay)

    def stop(self) -> None:
        self._running = False

    def _backoff(self) -> float:
        delay = min(self._initial_delay * (2 ** (self._attempt - 1)), self._max_delay)
        jitter = delay * self._jitter * (random.random() * 2 - 1)
        return max(0.1, delay + jitter)

    @property
    def attempt(self) -> int:
        return self._attempt

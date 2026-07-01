"""
execution/backoff.py  —  QuantLuna Exponential Backoff cu Jitter

Problema rezolvată:
  Reconnect imediat după fiecare eroare WS produce thundering herd:
  toți clienții reconectează simultan → ban de IP sau overload exchange.

Soluție: exponential backoff cu full-jitter:
  delay = random(0, min(cap, base * 2^attempt))

Usage:
    backoff = ExponentialBackoff(base_s=1.0, cap_s=60.0, jitter=True)
    async for delay in backoff:
        try:
            await connect()
            backoff.reset()
            break
        except Exception:
            pass  # backoff.advance() este apelat automat
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class ExponentialBackoff:
    """
    Exponential backoff cu full jitter pentru reconnect WS.

    Args:
        base_s:      delay inițial (secunde)
        cap_s:       delay maxim (secunde)
        jitter:      True = full jitter (recomandat)
        max_retries: None = infinit
    """

    def __init__(
        self,
        base_s: float = 1.0,
        cap_s: float = 60.0,
        jitter: bool = True,
        max_retries: int | None = None,
    ) -> None:
        self._base      = base_s
        self._cap       = cap_s
        self._jitter    = jitter
        self._max       = max_retries
        self._attempt   = 0

    def reset(self) -> None:
        """Apelat după o conexiune reuşită."""
        self._attempt = 0

    def next_delay(self) -> float:
        """Calculează delay-ul următor fără să avanseze contorul."""
        ceiling = min(self._cap, self._base * (2 ** self._attempt))
        if self._jitter:
            return random.uniform(0, ceiling)
        return ceiling

    def advance(self) -> float:
        """Avansează contorul şi returnează delay-ul corespunzător."""
        delay = self.next_delay()
        self._attempt += 1
        return delay

    @property
    def attempt(self) -> int:
        return self._attempt

    async def wait(self) -> float:
        """
        Aşteaptă delay-ul următor şi avansează contorul.
        Returnează delay-ul efectiv aşteptat.
        """
        delay = self.advance()
        if delay > 0:
            logger.info(
                f"[Backoff] attempt #{self._attempt} — waiting {delay:.1f}s "
                f"(cap={self._cap}s)"
            )
            await asyncio.sleep(delay)
        return delay

    async def __aiter__(self) -> AsyncIterator[float]:
        """
        Async generator pentru loop de reconnect:

            async for delay in backoff:
                try:
                    await connect()
                    backoff.reset()
                    return
                except Exception as e:
                    logger.warning(f'reconnect failed: {e}')
                    # delay-ul următor va fi calculat automat
        """
        attempt = 0
        while self._max is None or attempt < self._max:
            ceiling = min(self._cap, self._base * (2 ** attempt))
            delay   = random.uniform(0, ceiling) if self._jitter else ceiling
            if attempt > 0:
                logger.info(
                    f"[Backoff] reconnect attempt #{attempt} — waiting {delay:.1f}s"
                )
                await asyncio.sleep(delay)
            yield delay
            attempt += 1
        raise StopAsyncIteration

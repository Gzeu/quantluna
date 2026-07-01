"""
execution/circuit_breaker.py  —  QuantLuna Circuit Breaker

Problema rezolvată:
  WS reconnect loopul poate reconecta la infinit dacă exchange-ul
  are o pană sistematică sau API-ul e revocat. Fără circuit breaker →
  spam de log-uri, consum de resurse, potențial ban de IP.

Soluție: implementare clasică Closed → Open → Half-Open:
  CLOSED: funcționează normal, număr erori consecutive.
  OPEN:   blochează apeluri, aşteaptă `recovery_timeout_s`.
  HALF-OPEN: lasă un singur apel • succes → CLOSED, eśec → OPEN.

Usage:
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout_s=60)
    async with cb:
        await connect_ws()   # ridică CircuitOpenError dacă e OPEN
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED    = "closed"      # normal
    OPEN      = "open"        # blocat
    HALF_OPEN = "half_open"   # test recovery


class CircuitOpenError(Exception):
    """Ridicată când circuitul e OPEN şi nu acceptă apeluri."""
    pass


class CircuitBreaker:
    """
    Circuit breaker async-safe pentru conexiuni WebSocket / CCXT.

    Args:
        failure_threshold:   număr de eśecuri consecutive pentru a deschide circuitul
        recovery_timeout_s:  secunde de aşteptare în starea OPEN
        half_open_max_calls: apeluri permise în HALF_OPEN (default 1)
        name:                label pentru logging
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 60.0,
        half_open_max_calls: int = 1,
        name: str = "circuit",
    ) -> None:
        self._threshold   = failure_threshold
        self._timeout     = recovery_timeout_s
        self._max_half    = half_open_max_calls
        self._name        = name
        self._state       = CircuitState.CLOSED
        self._failures    = 0
        self._last_opened: Optional[float] = None
        self._half_calls  = 0
        self._lock        = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    async def __aenter__(self):
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - (self._last_opened or 0)
                if elapsed >= self._timeout:
                    logger.info(f"[CB:{self._name}] OPEN → HALF_OPEN (elapsed={elapsed:.0f}s)")
                    self._state    = CircuitState.HALF_OPEN
                    self._half_calls = 0
                else:
                    raise CircuitOpenError(
                        f"[CB:{self._name}] Circuit OPEN — "
                        f"{self._timeout - elapsed:.0f}s până la retry"
                    )
            if self._state == CircuitState.HALF_OPEN:
                if self._half_calls >= self._max_half:
                    raise CircuitOpenError(
                        f"[CB:{self._name}] Half-open slot ocupat"
                    )
                self._half_calls += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        async with self._lock:
            if exc_type is None:
                # succes
                if self._state == CircuitState.HALF_OPEN:
                    logger.info(f"[CB:{self._name}] HALF_OPEN → CLOSED (recovery OK)")
                self._state    = CircuitState.CLOSED
                self._failures = 0
            else:
                self._failures += 1
                if self._state == CircuitState.HALF_OPEN:
                    logger.warning(
                        f"[CB:{self._name}] HALF_OPEN → OPEN (eśec în recovery)"
                    )
                    self._state       = CircuitState.OPEN
                    self._last_opened = time.monotonic()
                elif self._failures >= self._threshold:
                    logger.error(
                        f"[CB:{self._name}] CLOSED → OPEN "
                        f"({self._failures} eśecuri consecutive)"
                    )
                    self._state       = CircuitState.OPEN
                    self._last_opened = time.monotonic()
                else:
                    logger.warning(
                        f"[CB:{self._name}] eśec {self._failures}/{self._threshold}"
                    )
        return False  # nu suprimăm excepția

    def record_success(self) -> None:
        """Apelat manual în afara context manager, la nevoie."""
        self._failures = 0
        if self._state != CircuitState.CLOSED:
            logger.info(f"[CB:{self._name}] manual reset → CLOSED")
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Apelat manual la nevoie."""
        self._failures += 1
        if self._failures >= self._threshold:
            self._state       = CircuitState.OPEN
            self._last_opened = time.monotonic()
            logger.error(
                f"[CB:{self._name}] manual trip → OPEN "
                f"({self._failures} eśecuri)"
            )

    def is_available(self) -> bool:
        """Non-blocking check — True dacă acceptă apeluri."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - (self._last_opened or 0)
            return elapsed >= self._timeout
        return self._half_calls < self._max_half  # HALF_OPEN

    def reset(self) -> None:
        """Reset forțat — ex: după un restart manual."""
        self._failures  = 0
        self._state     = CircuitState.CLOSED
        self._last_opened = None
        logger.info(f"[CB:{self._name}] reset forțat → CLOSED")

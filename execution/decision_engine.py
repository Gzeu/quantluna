"""
execution/decision_engine.py  —  DecisionEngine

Extracted from bybit_live_runner.py (Sprint 28 SRP refactor).
Encapsulates the z-score decision matrix: entry / exit / hold.

Usage::

    engine = DecisionEngine(
        entry_zscore=2.0,
        exit_zscore=0.5,
        market_trade_enabled=True,
    )
    action = engine.decide(zscore, circuit_breaker, order_manager)
    # action: 'entry_long' | 'entry_short' | 'exit' | None
"""
from __future__ import annotations

from typing import Optional

from loguru import logger


class DecisionEngine:
    """
    Pure-logic decision matrix (no side effects, no I/O).

    Rules
    -----
    - Circuit breaker open        → None (no trade)
    - market_trade_enabled=False  → None (dry observation only)
    - |z| >= entry_zscore         → entry_long (z<0) or entry_short (z>0)
    - |z| <= exit_zscore          → exit (only when position open)
    - otherwise                   → None
    """

    def __init__(
        self,
        entry_zscore: float = 2.0,
        exit_zscore:  float = 0.5,
        market_trade_enabled: bool = True,
    ) -> None:
        self._entry_z = entry_zscore
        self._exit_z  = exit_zscore
        self._enabled = market_trade_enabled

    def decide(
        self,
        zscore: float,
        circuit_breaker,
        order_manager,
    ) -> Optional[str]:
        """
        Compute action for current bar.

        Parameters
        ----------
        zscore:
            Current spread z-score.
        circuit_breaker:
            ``CircuitBreaker`` instance to check gate status.
        order_manager:
            ``OrderManager`` instance to check position status.

        Returns
        -------
        str or None
            One of ``'entry_long'``, ``'entry_short'``, ``'exit'``, or ``None``.
        """
        if not self._enabled:
            return None
        if circuit_breaker.is_open():
            return None

        if abs(zscore) >= self._entry_z:
            action = "entry_short" if zscore > 0 else "entry_long"
            logger.debug("DecisionEngine: {} | z={:.4f}", action, zscore)
            return action

        if abs(zscore) <= self._exit_z and order_manager.has_position():
            logger.debug("DecisionEngine: exit | z={:.4f}", zscore)
            return "exit"

        return None

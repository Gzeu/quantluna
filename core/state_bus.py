"""
core/state_bus.py  —  QuantLuna State Bus (canonical location)

Sprint 13 FIX: moved from root state_bus.py to core/state_bus.py.
state_bus.py in root is kept as a compatibility shim.

The StateBus is the single source of truth for live trading state:
  - Thread-safe async queue for inter-module communication
  - Snapshot dict for dashboard API
  - Position tracking
  - Equity curve
  - Recent trades ring buffer
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    pair: str
    direction: str  # 'LONG' | 'SHORT'
    qty_y: float
    qty_x: float
    entry_price_y: float
    entry_price_x: float
    entry_ts: Optional[str] = None
    hedge_ratio: float = 1.0
    notional_usdt: float = 0.0


class StateBus:
    """
    Central async state bus.

    All live modules write here via put().
    Dashboard server reads via snapshot_dict(), get_positions(), etc.
    """

    def __init__(self, maxlen: int = 10_000) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._state: Dict[str, Any] = {
            "status": "IDLE",
            "pair": None,
            "beta": None,
            "zscore": None,
            "pnl_usdt": 0.0,
            "drawdown": 0.0,
            "n_trades": 0,
            "last_update": None,
        }
        self._positions: List[Position] = []
        self._equity_curve: Deque[Dict] = deque(maxlen=maxlen)
        self._recent_trades: Deque[Dict] = deque(maxlen=500)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def put(self, event: Dict[str, Any]) -> None:
        """Async put — non-blocking."""
        await self._queue.put(event)

    def put_nowait(self, event: Dict[str, Any]) -> None:
        """Sync put — use when not inside async context."""
        self._queue.put_nowait(event)

    def update_state(self, **kwargs) -> None:
        """Merge kwargs into state dict."""
        self._state.update(kwargs)

    def add_position(self, pos: Position) -> None:
        self._positions.append(pos)
        logger.debug(f"StateBus: position added {pos.pair} {pos.direction}")

    def remove_position(self, pair: str) -> None:
        self._positions = [p for p in self._positions if p.pair != pair]

    def add_equity_point(self, ts: str, equity: float) -> None:
        self._equity_curve.append({"ts": ts, "equity": equity})

    def add_trade(self, trade: Dict) -> None:
        self._recent_trades.append(trade)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def snapshot_dict(self) -> Dict[str, Any]:
        """Returns copy of current state (safe for JSON serialization)."""
        return dict(self._state)

    def get_positions(self) -> List[Position]:
        return list(self._positions)

    def get_equity_curve(self) -> List[Dict]:
        return list(self._equity_curve)

    def get_recent_trades(self) -> List[Dict]:
        return list(self._recent_trades)

    # ------------------------------------------------------------------
    # Queue consumer
    # ------------------------------------------------------------------

    async def get(self) -> Dict[str, Any]:
        """Async get from queue."""
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()


# Module-level singleton — import from here
bus = StateBus()

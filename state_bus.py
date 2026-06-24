"""
state_bus.py  —  QuantLuna Sprint 5

Single source of truth for all runtime state.
Fed by: LiveTrader, SignalGenerator, PortfolioRisk, OrderManager.
Broadcasts JSON snapshots to WebSocket subscribers.

Usage:
    bus = StateBus()
    # producer:
    bus.update({"zscore": 1.42, "hedge_ratio": 0.87})
    # consumer (FastAPI WS handler):
    async for snapshot in bus.subscribe():
        await ws.send_json(snapshot)
"""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class StateSnapshot:
    # --- Identity ---
    sym_y: str = ""
    sym_x: str = ""
    exchange: str = ""
    trader_state: str = "idle"

    # --- Prices ---
    price_y: float = 0.0
    price_x: float = 0.0
    timestamp_utc: str = ""

    # --- Kalman / Signal ---
    hedge_ratio: float = 0.0
    kalman_gain: float = 0.0
    kalman_uncertainty: float = 0.0
    zscore: float = 0.0
    spread: float = 0.0
    half_life: float = 0.0
    regime: str = "unknown"         # 'cointegrated' | 'breakdown' | 'unknown'

    # --- Funding ---
    funding_rate_y: float = 0.0     # current 8h rate
    funding_rate_x: float = 0.0
    next_funding_ts_y: str = ""
    next_funding_ts_x: str = ""

    # --- P&L ---
    realized_pnl: float = 0.0
    open_pnl: float = 0.0
    daily_pnl: float = 0.0
    total_fees_usdt: float = 0.0
    trade_count: int = 0

    # --- Position ---
    in_position: bool = False
    entry_side_y: str = ""
    entry_side_x: str = ""
    entry_price_y: float = 0.0
    entry_price_x: float = 0.0
    qty_y: float = 0.0
    qty_x: float = 0.0

    # --- Recent trades (last 50) ---
    recent_trades: list = field(default_factory=list)

    # --- Series (last 500 points) ---
    zscore_series: list = field(default_factory=list)   # [{ts, v}]
    pnl_series: list = field(default_factory=list)      # [{ts, v}]

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        return d


class StateBus:
    """
    Thread-safe (asyncio) state bus.
    update() is non-blocking; broadcast is async.
    """
    MAX_SERIES_LEN = 500
    MAX_TRADES_LEN = 50

    def __init__(self):
        self._snapshot = StateSnapshot()
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def update(self, patch: Dict[str, Any]) -> None:
        """
        Merge patch dict into snapshot. Schedules broadcast.
        Call from sync or async context (fire-and-forget).
        """
        for k, v in patch.items():
            if hasattr(self._snapshot, k):
                setattr(self._snapshot, k, v)
            else:
                logger.debug(f"StateBus: unknown field '{k}' ignored")

        # Append to series if present
        if "zscore" in patch:
            self._append_series(
                self._snapshot.zscore_series,
                patch["zscore"],
            )
        if "realized_pnl" in patch or "open_pnl" in patch:
            self._append_series(
                self._snapshot.pnl_series,
                self._snapshot.realized_pnl + self._snapshot.open_pnl,
            )

        asyncio.get_event_loop().call_soon_threadsafe(self._schedule_broadcast)

    def record_trade(self, trade: Dict[str, Any]) -> None:
        """Append a completed trade to recent_trades ring buffer."""
        self._snapshot.recent_trades.append({
            "ts": trade.get("ts", ""),
            "side": trade.get("side", ""),
            "pnl": trade.get("pnl", 0.0),
            "entry_y": trade.get("entry_y", 0.0),
            "entry_x": trade.get("entry_x", 0.0),
            "exit_y": trade.get("exit_y", 0.0),
            "exit_x": trade.get("exit_x", 0.0),
            "fees": trade.get("fees", 0.0),
        })
        if len(self._snapshot.recent_trades) > self.MAX_TRADES_LEN:
            self._snapshot.recent_trades = self._snapshot.recent_trades[-self.MAX_TRADES_LEN:]
        self._schedule_broadcast()

    def snapshot(self) -> Dict[str, Any]:
        """Return current snapshot as dict (deep copy)."""
        return deepcopy(self._snapshot.to_dict())

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    async def subscribe(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Async generator — yields a snapshot dict on every update.
        Usage:
            async for snap in bus.subscribe():
                ...
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        async with self._lock:
            self._subscribers.add(q)
        try:
            # Send current state immediately on subscribe
            await q.put(self.snapshot())
            while True:
                snap = await q.get()
                yield snap
        finally:
            async with self._lock:
                self._subscribers.discard(q)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _schedule_broadcast(self) -> None:
        asyncio.ensure_future(self._broadcast())

    async def _broadcast(self) -> None:
        if not self._subscribers:
            return
        snap = self.snapshot()
        dead: Set[asyncio.Queue] = set()
        for q in self._subscribers:
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                dead.add(q)  # slow consumer — evict
        if dead:
            async with self._lock:
                self._subscribers -= dead
            logger.warning(f"StateBus: evicted {len(dead)} slow subscriber(s)")

    def _append_series(self, series: list, value: float) -> None:
        series.append({"ts": time.time(), "v": round(value, 6)})
        if len(series) > self.MAX_SERIES_LEN:
            del series[:-self.MAX_SERIES_LEN]


# Singleton — import and use anywhere
bus = StateBus()

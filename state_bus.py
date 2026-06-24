"""
state_bus.py  —  QuantLuna Sprint 7

Single source of truth for all runtime state.
Fed by: LiveTrader, SignalGenerator, PortfolioRisk, OrderManager,
        FundingMonitor, PnLReconciler, WsWatchdog.
Broadcasts JSON snapshots to WebSocket subscribers.

Changes Sprint 6:
  - Funding live: funding_y, funding_x, funding_net (annualized)
  - P&L reconciliation: reconciled_open_pnl, pnl_drift_usd, pnl_drift_alert
  - Position detail: position_size_y, position_size_x (from fetch_positions)
  - Renamed open_pnl → open_pnl_usd for clarity (local mark-price estimate)

Changes Sprint 7:
  - WS health: ws_stale, ws_last_tick_age_s, ws_stale_alert
  - snapshot() now returns StateSnapshot object directly (not dict) for
    internal consumers; to_dict() still available for JSON serialization

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

    # --- Funding (Sprint 6 — populat de FundingMonitor) ---
    funding_y: float = 0.0          # annualized rate leg Y
    funding_x: float = 0.0          # annualized rate leg X
    funding_net: float = 0.0        # funding_y - funding_x
    # Câmpuri legacy menținute pentru compatibilitate backward
    funding_rate_y: float = 0.0     # current 8h rate (raw, neanualizat)
    funding_rate_x: float = 0.0
    next_funding_ts_y: str = ""
    next_funding_ts_x: str = ""

    # --- P&L ---
    realized_pnl: float = 0.0
    open_pnl_usd: float = 0.0       # Sprint 6: redenumit din open_pnl (local WS estimate)
    open_pnl: float = 0.0           # alias backward-compat (== open_pnl_usd)
    daily_pnl: float = 0.0
    total_fees_usdt: float = 0.0
    trade_count: int = 0

    # --- P&L Reconciliation (Sprint 6 — populat de PnLReconciler) ---
    reconciled_open_pnl: float = 0.0    # unrealizedPnl de pe exchange via fetch_positions
    pnl_drift_usd: float = 0.0          # |reconciled - local|
    pnl_drift_alert: bool = False        # True dacă drift > threshold

    # --- Position ---
    in_position: bool = False
    entry_side_y: str = ""
    entry_side_x: str = ""
    entry_price_y: float = 0.0
    entry_price_x: float = 0.0
    qty_y: float = 0.0
    qty_x: float = 0.0
    # Sprint 6: position sizes confirmate de exchange
    position_size_y: float = 0.0
    position_size_x: float = 0.0

    # --- WS Health (Sprint 7 — populat de WsWatchdog) ---
    ws_stale: bool = False              # True dacă ultimul tick e mai vechi de threshold
    ws_last_tick_age_s: float = 0.0     # secunde de la ultimul on_tick()
    ws_stale_alert: bool = False        # True dacă stale persists > stale_critical_s

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
        Accepts partial dicts — only provided fields are updated.
        Call from sync or async context (fire-and-forget).
        """
        for k, v in patch.items():
            if hasattr(self._snapshot, k):
                setattr(self._snapshot, k, v)
            else:
                logger.debug(f"StateBus: unknown field '{k}' ignored")

        # Keep open_pnl alias in sync with open_pnl_usd
        if "open_pnl_usd" in patch:
            self._snapshot.open_pnl = self._snapshot.open_pnl_usd
        elif "open_pnl" in patch:
            self._snapshot.open_pnl_usd = self._snapshot.open_pnl

        # Append to series if present
        if "zscore" in patch:
            self._append_series(
                self._snapshot.zscore_series,
                patch["zscore"],
            )
        if "realized_pnl" in patch or "open_pnl_usd" in patch or "open_pnl" in patch:
            self._append_series(
                self._snapshot.pnl_series,
                self._snapshot.realized_pnl + self._snapshot.open_pnl_usd,
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

    def snapshot(self) -> StateSnapshot:
        """
        Return current StateSnapshot (deep copy).
        Sprint 7: returns StateSnapshot object, not dict.
        Use snapshot().to_dict() for JSON serialization.
        """
        return deepcopy(self._snapshot)

    def snapshot_dict(self) -> Dict[str, Any]:
        """Return snapshot as dict — for JSON serialization (dashboard, /state endpoint)."""
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
            await q.put(self.snapshot_dict())
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
        snap = self.snapshot_dict()
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

"""
core/state_bus.py  —  QuantLuna State Bus (canonical location)
Sprint S20 FIX — 2026-07-11

Changelog S20:
  - publish(topic, payload)  — scrie payload pentru un topic (thread-safe)
  - get_latest(topic)        — citește ultimul payload pentru topic (sync)
  - subscribe(topic)         — returnează asyncio.Queue per topic (async listeners)
  - _topic_latest            — dict {topic: payload} cu ultimul mesaj per topic
  - _topic_queues            — dict {topic: List[Queue]} pentru broadcast async
  Backward compatible: put(), put_nowait(), update_state(), snapshot_dict() intacte.

Sprint 13 / 28 (original):
  The StateBus is the single source of truth for live trading state:
  - Thread-safe async queue for inter-module communication
  - Snapshot dict for dashboard API
  - Position tracking
  - Equity curve
  - Recent trades ring buffer

Dashboard wiring (Sprint 28 fix)::

    # In bot (main.py / BybitLiveRunner):
    from core.state_bus import bus
    from risk.dashboard_engine import RiskDashboardEngine
    engine = RiskDashboardEngine(initial_capital=cfg.initial_capital)
    bus.set_risk_engine(engine)       # makes engine available to API

    # In API (api/risk.py):
    from core.state_bus import bus
    engine = bus.risk_engine           # always up-to-date
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Lightweight position snapshot stored on the bus."""

    pair: str
    direction: str          # 'LONG' | 'SHORT'
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

    All live modules write here via ``put()`` / ``update_state()`` / ``publish()``.
    Dashboard server reads via ``snapshot_dict()``, ``get_positions()``,
    ``get_latest()``, ``subscribe()``.

    Topic pub/sub (S20)
    -------------------
    Runner publishes per-topic payloads::

        bus.publish("bar", {"ts": ..., "zscore": ..., "pnl": ...})
        bus.publish("warmup_status", {"bars_done": 42, "pct": 0.42, ...})

    Dashboard reads latest value synchronously::

        payload = bus.get_latest("bar")          # → dict or None

    Async listener (dashboard background task)::

        queue = bus.subscribe("bar")
        payload = await queue.get()              # blocks until new publish

    RiskDashboardEngine wiring
    --------------------------
    The bot sets the engine once at startup::

        bus.set_risk_engine(my_engine)

    All API endpoints then read via::

        engine = bus.risk_engine   # never None after bot sets it
    """

    def __init__(self, maxlen: int = 10_000) -> None:
        """Create a new StateBus with ring-buffers of `maxlen` items."""
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
        self._risk_engine = None   # injected by bot at startup

        # S20: topic pub/sub
        self._topic_lock = threading.Lock()
        self._topic_latest: Dict[str, Any] = {}          # ultimul payload per topic
        self._topic_queues: Dict[str, List[asyncio.Queue]] = {}  # subscribers async

    # ------------------------------------------------------------------
    # RiskDashboardEngine wiring
    # ------------------------------------------------------------------

    def set_risk_engine(self, engine) -> None:
        """
        Inject a RiskDashboardEngine instance.

        Called once by the bot at startup so all API endpoints share
        the same live engine without cross-process IPC.
        """
        self._risk_engine = engine
        logger.debug("StateBus: RiskDashboardEngine injected")

    @property
    def risk_engine(self):
        """
        Return the injected engine, or a fresh empty one as fallback.

        API endpoints can always call ``bus.risk_engine.snapshot()``
        safely — worst case they get an empty dashboard until the bot
        injects a real engine.
        """
        if self._risk_engine is None:
            from risk.dashboard_engine import RiskDashboardEngine
            self._risk_engine = RiskDashboardEngine()
            logger.warning(
                "StateBus: risk_engine not set by bot — returning empty engine. "
                "Call bus.set_risk_engine(engine) at bot startup."
            )
        return self._risk_engine

    # ------------------------------------------------------------------
    # S20: Topic pub/sub — publish / get_latest / subscribe
    # ------------------------------------------------------------------

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        """
        Publish un payload pentru un topic (sync, thread-safe).

        Stochează ultimul mesaj în _topic_latest[topic] și îl pune în
        toate queue-urile async ale subscriber-ilor pentru topic.

        Args:
            topic:   Ex. "bar", "warmup_status", "trade", "alert"
            payload: Dict cu datele de publicat

        Usage::

            bus.publish("bar", {"ts": 1720000000, "zscore": 1.23, "pnl": 42.0})
            bus.publish("warmup_status", {"bars_done": 50, "pct": 0.5, "ready": False})
        """
        with self._topic_lock:
            self._topic_latest[topic] = payload
            queues = list(self._topic_queues.get(topic, []))

        # Pune în queue-urile async în afara lock-ului pentru a evita deadlock
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Scoate cel mai vechi element dacă coada e plină
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass
            except Exception as exc:
                logger.debug(f"StateBus.publish: queue put failed for topic '{topic}': {exc}")

    def get_latest(self, topic: str) -> Optional[Dict[str, Any]]:
        """
        Returnează ultimul payload publicat pentru topic (sync, thread-safe).

        Returns None dacă niciun payload nu a fost publicat încă pentru topic.

        Args:
            topic: Ex. "bar", "warmup_status"

        Returns:
            Dict payload sau None

        Usage::

            bar = bus.get_latest("bar")
            if bar:
                zscore = bar.get("zscore", 0.0)
        """
        with self._topic_lock:
            return self._topic_latest.get(topic)

    def subscribe(self, topic: str, maxsize: int = 500) -> asyncio.Queue:
        """
        Creează și returnează un asyncio.Queue dedicat pentru topic.

        Fiecare apel returnează o Queue nouă — potrivit pentru un singur
        listener async. Apelează unsubscribe() când listener-ul se oprește.

        Args:
            topic:   Topicul de ascultat
            maxsize: Dimensiunea maximă a queue-ului (default 500)

        Returns:
            asyncio.Queue care va primi toate payload-urile viitoare

        Usage::

            queue = bus.subscribe("bar")
            try:
                while True:
                    payload = await queue.get()
                    # procesează payload
            finally:
                bus.unsubscribe("bar", queue)
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._topic_lock:
            if topic not in self._topic_queues:
                self._topic_queues[topic] = []
            self._topic_queues[topic].append(q)
        logger.debug(f"StateBus: new subscriber for topic '{topic}'")
        return q

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        """
        Elimină un subscriber queue pentru topic.

        Apelează întotdeauna când listener-ul async se oprește pentru
        a evita leak-uri de memorie.
        """
        with self._topic_lock:
            queues = self._topic_queues.get(topic, [])
            if queue in queues:
                queues.remove(queue)
        logger.debug(f"StateBus: subscriber removed for topic '{topic}'")

    def get_topics(self) -> List[str]:
        """Returnează lista de topicuri pentru care s-a publicat cel puțin un mesaj."""
        with self._topic_lock:
            return list(self._topic_latest.keys())

    # ------------------------------------------------------------------
    # Write (original API — backward compatible)
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
        """Register an open position."""
        self._positions.append(pos)
        logger.debug("StateBus: position added {} {}", pos.pair, pos.direction)

    def remove_position(self, pair: str) -> None:
        """Remove position for pair (on close)."""
        self._positions = [p for p in self._positions if p.pair != pair]

    def add_equity_point(self, ts: str, equity: float) -> None:
        """Append one equity curve data point."""
        self._equity_curve.append({"ts": ts, "equity": equity})

    def add_trade(self, trade: Dict) -> None:
        """Append a completed trade to the recent-trades buffer."""
        self._recent_trades.append(trade)

    # ------------------------------------------------------------------
    # Read (original API — backward compatible)
    # ------------------------------------------------------------------

    def snapshot_dict(self) -> Dict[str, Any]:
        """Return a copy of the current state (safe for JSON serialization)."""
        return dict(self._state)

    def get_positions(self) -> List[Position]:
        """Return a copy of open positions list."""
        return list(self._positions)

    def get_equity_curve(self) -> List[Dict]:
        """Return equity curve as a list."""
        return list(self._equity_curve)

    def get_recent_trades(self) -> List[Dict]:
        """Return recent trades as a list."""
        return list(self._recent_trades)

    # ------------------------------------------------------------------
    # Queue consumer (original API)
    # ------------------------------------------------------------------

    async def get(self) -> Dict[str, Any]:
        """Async get from queue."""
        return await self._queue.get()

    def task_done(self) -> None:
        """Mark queue task as done."""
        self._queue.task_done()


# Module-level singleton — import from here
bus = StateBus()

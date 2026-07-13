"""
core/state_bus.py  —  QuantLuna State Bus (canonical location)

Sprint 13 FIX: moved from root state_bus.py to core/state_bus.py.
root state_bus.py is kept as a compatibility shim.

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

    @classmethod
    def from_bybit_position(
        cls,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        unrealised_pnl: float = 0.0,
        pair_id: str = "",
    ) -> "Position":
        """
        Creeaza un obiect Position dintr-o pozitie Bybit.

        Args:
            symbol: simbolul Bybit (ex: "BTCUSDT")
            side:   "Buy" | "Sell" (Bybit API)
            size:   cantitatea
            entry_price: pretul mediu de intrare
            unrealised_pnl: PnL nerealizat
            pair_id: ID pereche (ex: "BTCUSDT-ETHUSDT")

        Returns:
            Position
        """
        direction = "LONG" if side.lower() in ("buy", "long") else "SHORT"
        return cls(
            pair=pair_id or symbol,
            direction=direction,
            qty_y=size,
            qty_x=0.0,
            entry_price_y=entry_price,
            entry_price_x=0.0,
            notional_usdt=size * entry_price,
        )


class StateBus:
    """
    Central async state bus.

    All live modules write here via ``put()`` / ``update_state()``.
    Dashboard server reads via ``snapshot_dict()``, ``get_positions()``, etc.

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

    async def put(self, event: Dict[str, Any]) -> None:
        await self._queue.put(event)

    def put_nowait(self, event: Dict[str, Any]) -> None:
        self._queue.put_nowait(event)

    def update_state(self, **kwargs) -> None:
        self._state.update(kwargs)

    def add_position(self, pos: Position) -> None:
        """Register an open position."""
        self._positions.append(pos)
        logger.debug("StateBus: position added %s %s", pos.pair, pos.direction)

    def add_bybit_position(
        self,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        unrealised_pnl: float = 0.0,
        pair_id: str = "",
    ) -> None:
        """
        Register a position from Bybit data.

        Args:
            symbol: Bybit symbol (ex: "BTCUSDT")
            side:   "Buy" | "Sell"
            size:   quantity
            entry_price: average entry price
            unrealised_pnl: unrealised PnL
            pair_id: optional pair ID for multi-pair trading
        """
        pos = Position.from_bybit_position(
            symbol=symbol,
            side=side,
            size=size,
            entry_price=entry_price,
            unrealised_pnl=unrealised_pnl,
            pair_id=pair_id,
        )
        self.add_position(pos)

    def remove_position(self, pair: str) -> None:
        """Remove position for pair (on close)."""
        self._positions = [p for p in self._positions if p.pair != pair]

    def add_equity_point(self, ts: str, equity: float) -> None:
        self._equity_curve.append({"ts": ts, "equity": equity})

    def add_trade(self, trade: Dict) -> None:
        self._recent_trades.append(trade)

    def snapshot_dict(self) -> Dict[str, Any]:
        return dict(self._state)

    def get_positions(self) -> List[Position]:
        return list(self._positions)

    def get_equity_curve(self) -> List[Dict]:
        return list(self._equity_curve)

    def get_recent_trades(self) -> List[Dict]:
        return list(self._recent_trades)

    async def get(self) -> Dict[str, Any]:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()


bus = StateBus()

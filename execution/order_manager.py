"""
QuantLuna — Order Manager (Sprint 17 + Sprint 28 + Sprint 21)

Centralised order lifecycle management across all venues (Bybit, Binance, OKX).
Abstracts over individual routers — callers use OrderManager instead of
calling each router directly.

Responsibilities:
  - Route orders to the correct exchange based on symbol / config
  - Track open orders with local state (pending, filled, cancelled, failed)
  - Automatic cancel-on-timeout for stale open orders
  - Emit events via StateBus on fill / cancel / error
  - Thread-safe for use from async tasks
  - [Sprint 28] on_fill / on_close callback hooks for cycle restart
  - [Sprint 21] adopt_position() — injecteaza pozitie externa detectata la startup

Usage:
    mgr = OrderManager(config)
    await mgr.connect_all()
    order_id = await mgr.submit(OrderRequest(symbol="BTCUSDT", side="buy", qty=0.01, venue="bybit"))
    status   = mgr.get_status(order_id)

    # Register restart callback
    mgr.register_on_fill(my_callback)   # called when FILLED
    mgr.register_on_close(my_callback)  # called when CANCELLED / TIMED_OUT / FAILED

    # Sprint 21: adopt pozitie existenta la restart
    mgr.adopt_position(symbol_y="BTCUSDT", symbol_x="ETHUSDT", y_side="long",
                       x_side="short", y_qty=0.01, x_qty=0.15,
                       y_entry_price=65000.0, x_entry_price=3400.0)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger


class OrderStatus(str, Enum):
    PENDING    = "pending"
    SUBMITTED  = "submitted"
    FILLED     = "filled"
    PARTIALLY  = "partially_filled"
    CANCELLED  = "cancelled"
    FAILED     = "failed"
    TIMED_OUT  = "timed_out"


class OrderSide(str, Enum):
    BUY  = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT  = "limit"


@dataclass
class OrderRequest:
    """Describes an order to be placed."""
    symbol:      str
    side:        str            # 'buy' | 'sell'
    qty:         float
    venue:       str            # 'bybit' | 'binance' | 'okx'
    order_type:  str = "market"  # 'market' | 'limit'
    price:       Optional[float] = None
    reduce_only: bool = False
    post_only:   bool = False
    client_id:   Optional[str] = None
    tag:         str = ""        # e.g. 'pair_y', 'pair_x', 'close_y', 'adopted'


@dataclass
class OrderRecord:
    """Tracks lifecycle of a submitted order."""
    local_id:    str
    request:     OrderRequest
    status:      OrderStatus = OrderStatus.PENDING
    exchange_id: Optional[str] = None
    submitted_at: float = field(default_factory=time.time)
    filled_at:   Optional[float] = None
    fill_price:  Optional[float] = None
    fill_qty:    float = 0.0
    error:       Optional[str] = None
    raw_response: Optional[Dict] = None

    @property
    def age_seconds(self) -> float:
        return time.time() - self.submitted_at

    def as_dict(self) -> Dict:
        return {
            "local_id":    self.local_id,
            "exchange_id": self.exchange_id,
            "symbol":      self.request.symbol,
            "venue":       self.request.venue,
            "side":        self.request.side,
            "qty":         self.request.qty,
            "order_type":  self.request.order_type,
            "status":      self.status.value,
            "fill_price":  self.fill_price,
            "fill_qty":    self.fill_qty,
            "age_s":       round(self.age_seconds, 2),
            "error":       self.error,
            "tag":         self.request.tag,
        }


@dataclass
class OrderManagerConfig:
    # Max seconds to wait for a market order fill before timing out
    market_timeout_s: float = 30.0
    # Max seconds to wait for a limit order fill before cancelling
    limit_timeout_s: float = 120.0
    # How often (seconds) to check for stale orders
    monitor_interval_s: float = 5.0
    # Max records kept in history (oldest dropped)
    max_history: int = 1000
    # Dry-run: log orders but never actually submit
    dry_run: bool = False


# Type alias for async callbacks
_AsyncCallback = Callable[[OrderRecord], Coroutine]


class OrderManager:
    """
    Centralised order router and lifecycle tracker.

    Parameters
    ----------
    config   : OrderManagerConfig
    routers  : dict of venue_name → router object (must have place_market_order /
               place_limit_order / cancel_order coroutines)

    Sprint 28 additions
    -------------------
    register_on_fill(cb)  — cb(record) called every time an order reaches FILLED status
    register_on_close(cb) — cb(record) called when order reaches CANCELLED/FAILED/TIMED_OUT
    These are useful for triggering a new trade cycle after a position closes.

    Sprint 21 additions
    -------------------
    adopt_position()  — injecteaza o pozitie externa (detectata la startup via
                        PositionReconciler sau CheckpointManager) in state.
                        Dupa adoptare, has_position() returneaza True si _decide()
                        poate genera semnal de exit.
    """

    def __init__(
        self,
        config: Optional[OrderManagerConfig] = None,
        routers: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.cfg     = config or OrderManagerConfig()
        self.routers: Dict[str, Any] = routers or {}
        self._orders: Dict[str, OrderRecord] = {}  # local_id → record
        self._lock   = asyncio.Lock()
        self._monitor_task: Optional[asyncio.Task] = None

        # Sprint 28: lifecycle callbacks
        self._on_fill_callbacks:  List[_AsyncCallback] = []
        self._on_close_callbacks: List[_AsyncCallback] = []

        # Sprint 21: pozitie adoptata la startup
        self._adopted_record: Optional[Any] = None  # AdoptedPosition
        self._adopted: bool = False

        # Pozitia curenta (set de _execute_action din runner)
        self.current_position: Optional[Any] = None
        self.current_pnl: Optional[float] = None

    # ------------------------------------------------------------------
    # Sprint 21: Position adoption (FEAT-3)
    # ------------------------------------------------------------------

    def adopt_position(
        self,
        symbol_y: str,
        symbol_x: str,
        y_side: str,
        x_side: str,
        y_qty: float,
        x_qty: float,
        y_entry_price: float,
        x_entry_price: float,
    ) -> None:
        """
        Sprint 21: Adopta o pozitie externa detectata la startup.

        Apelata de bybit_live_runner._reconcile_positions() dupa ce
        PositionReconciler sau CheckpointManager detecteaza o pozitie deschisa.
        Dupa apel, has_position() returneaza True si _decide() poate genera
        semnal de exit cand zscore revine la zero.

        Parametri
        ---------
        symbol_y, symbol_x : simbolurile perechii tranzactionate
        y_side, x_side     : 'long' | 'short' | 'none'
        y_qty, x_qty       : cantitate absoluta per leg
        y_entry_price,
        x_entry_price      : pretul mediu de intrare per leg
        """

        class _AdoptedPos:
            """Structura minima compatibila cu current_position din runner."""
            pass

        pos = _AdoptedPos()
        pos.y_side = y_side
        pos.x_side = x_side
        pos.y_qty  = y_qty
        pos.x_qty  = x_qty
        pos.y_entry = y_entry_price
        pos.x_entry = x_entry_price
        pos.pnl     = 0.0

        self.current_position = pos
        self._adopted = True
        self._adopted_record = pos

        logger.info(
            f"OrderManager: pozitie adoptata — "
            f"{symbol_y} {y_side} {y_qty:.6f} @ {y_entry_price:.4f} | "
            f"{symbol_x} {x_side} {x_qty:.6f} @ {x_entry_price:.4f}"
        )

    def release_adopted_position(self) -> None:
        """
        Sprint 21: Elibereaza pozitia adoptata dupa exit complet.
        Apelata de runner dupa record_exit() reusit.
        """
        self._adopted = False
        self._adopted_record = None
        self.current_position = None
        self.current_pnl = None
        logger.info("OrderManager: pozitie adoptata eliberata dupa exit")

    # ------------------------------------------------------------------
    # Sprint 28: Callback registration
    # ------------------------------------------------------------------

    def register_on_fill(self, callback: _AsyncCallback) -> None:
        """
        Register an async callback invoked when any order reaches FILLED status.
        Useful for: placing TP/SL after entry fill, starting a new cycle.

        Example
        -------
        async def on_fill(record: OrderRecord):
            if record.request.tag in ("adopted", "entry_y"):
                await protection_manager.place_tp_sl(record)
        mgr.register_on_fill(on_fill)
        """
        self._on_fill_callbacks.append(callback)
        logger.debug(f"OrderManager: registered on_fill callback [{callback.__name__}]")

    def register_on_close(self, callback: _AsyncCallback) -> None:
        """
        Register an async callback invoked when any order reaches a terminal
        non-fill state: CANCELLED, FAILED, TIMED_OUT.
        Useful for: restarting the trade cycle after a position closes externally.

        Example
        -------
        async def on_close(record: OrderRecord):
            if record.request.tag == "tp" or record.request.tag == "sl":
                await cycle_manager.restart_cycle(record.request.symbol)
        mgr.register_on_close(on_close)
        """
        self._on_close_callbacks.append(callback)
        logger.debug(f"OrderManager: registered on_close callback [{callback.__name__}]")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register_router(self, venue: str, router: Any) -> None:
        """Register (or replace) a venue router at runtime."""
        self.routers[venue] = router
        logger.info(f"OrderManager: registered router for venue='{venue}'")

    async def start_monitor(self) -> None:
        """Start background task that cancels timed-out orders."""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._timeout_loop())
            logger.info("OrderManager: monitor task started")

    async def stop_monitor(self) -> None:
        """Stop background monitor task."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("OrderManager: monitor task stopped")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def has_position(self) -> bool:
        """
        Sprint 21: Returneaza True daca exista o pozitie activa — fie adoptata
        la startup (self._adopted), fie deschisa in sesiunea curenta
        (self.current_position setat de record_entry_long/short).
        """
        if self._adopted and self._adopted_record is not None:
            return True
        if self.current_position is not None:
            return True
        return bool(self.open_orders())

    def record_entry_long(
        self, qty: float, price_y: float, price_x: float
    ) -> None:
        """Inregistreaza intrarea intr-o pozitie long in state local."""
        class _Pos:
            pass
        pos = _Pos()
        pos.y_side = "long"
        pos.x_side = "short"
        pos.y_qty  = qty
        pos.x_qty  = qty
        pos.y_entry = price_y
        pos.x_entry = price_x
        self.current_position = pos
        self._adopted = False  # pozitie proprie, nu adoptata
        logger.info(f"OrderManager: record_entry_long qty={qty} y@{price_y} x@{price_x}")

    def record_entry_short(
        self, qty: float, price_y: float, price_x: float
    ) -> None:
        """Inregistreaza intrarea intr-o pozitie short in state local."""
        class _Pos:
            pass
        pos = _Pos()
        pos.y_side = "short"
        pos.x_side = "long"
        pos.y_qty  = qty
        pos.x_qty  = qty
        pos.y_entry = price_y
        pos.x_entry = price_x
        self.current_position = pos
        self._adopted = False
        logger.info(f"OrderManager: record_entry_short qty={qty} y@{price_y} x@{price_x}")

    def record_exit(self, price_y: float, price_x: float) -> None:
        """Inregistreaza iesirea din pozitie si calculeaza PnL."""
        if self.current_position is None:
            logger.warning("OrderManager: record_exit apelat fara pozitie activa")
            return
        pos = self.current_position
        if pos.y_side == "long":
            pnl = (price_y - pos.y_entry) * pos.y_qty - (price_x - pos.x_entry) * pos.x_qty
        else:
            pnl = (pos.y_entry - price_y) * pos.y_qty + (price_x - pos.x_entry) * pos.x_qty
        self.current_pnl = pnl
        self.current_position = None
        self._adopted = False
        self._adopted_record = None
        logger.info(f"OrderManager: record_exit PnL={pnl:.4f} USDT")

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def get_status(self, local_id: str) -> Optional[OrderStatus]:
        rec = self._orders.get(local_id)
        return rec.status if rec else None

    def get_record(self, local_id: str) -> Optional[OrderRecord]:
        return self._orders.get(local_id)

    def open_orders(self) -> List[OrderRecord]:
        """Return all orders in SUBMITTED or PARTIALLY state."""
        return [
            r for r in self._orders.values()
            if r.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY)
        ]

    def all_records(self) -> List[OrderRecord]:
        return list(self._orders.values())

    def summary(self) -> Dict:
        counts: Dict[str, int] = {}
        for r in self._orders.values():
            counts[r.status.value] = counts.get(r.status.value, 0) + 1
        return {"total": len(self._orders), "by_status": counts}

    async def submit(self, req: OrderRequest) -> str:
        """
        Submit an order and return a local_id for tracking.

        Parameters
        ----------
        req : OrderRequest

        Returns
        -------
        local_id : str  (use this to query status)
        """
        local_id = req.client_id or str(uuid.uuid4())[:16]
        record = OrderRecord(local_id=local_id, request=req)

        async with self._lock:
            self._orders[local_id] = record

        if self.cfg.dry_run:
            logger.info(f"[DRY-RUN] OrderManager: {req.side} {req.qty} {req.symbol} @ {req.venue}")
            record.status      = OrderStatus.FILLED
            record.fill_price  = req.price or 0.0
            record.fill_qty    = req.qty
            record.filled_at   = time.time()
            await self._notify_fill(record)
            return local_id

        router = self.routers.get(req.venue)
        if router is None:
            record.status = OrderStatus.FAILED
            record.error  = f"No router registered for venue='{req.venue}'"
            logger.error(record.error)
            await self._notify_close(record)
            return local_id

        try:
            record.status = OrderStatus.SUBMITTED
            if req.order_type == OrderType.LIMIT and req.price is not None:
                resp = await router.place_limit_order(
                    req.symbol, req.side, req.qty, req.price,
                    reduce_only=req.reduce_only,
                    post_only=req.post_only,
                    client_order_id=local_id,
                )
            else:
                resp = await router.place_market_order(
                    req.symbol, req.side, req.qty,
                    reduce_only=req.reduce_only,
                    client_order_id=local_id,
                )

            record.exchange_id   = resp.get("id") or resp.get("orderId")
            record.raw_response  = resp
            # Assume market orders fill immediately
            if req.order_type == OrderType.MARKET:
                record.status    = OrderStatus.FILLED
                record.fill_price = (
                    resp.get("average") or resp.get("price") or req.price or 0.0
                )
                record.fill_qty  = float(resp.get("filled") or req.qty)
                record.filled_at = time.time()
                await self._notify_fill(record)
            logger.info(
                f"OrderManager: submitted {req.side} {req.qty} {req.symbol} "
                f"venue={req.venue} local_id={local_id} exch_id={record.exchange_id}"
            )
        except Exception as exc:
            record.status = OrderStatus.FAILED
            record.error  = str(exc)
            logger.error(f"OrderManager: order failed local_id={local_id} err={exc}")
            await self._notify_close(record)

        return local_id

    async def submit_pair(
        self,
        req_y: OrderRequest,
        req_x: OrderRequest,
    ) -> Dict[str, str]:
        """Submit both legs of a pairs trade concurrently."""
        id_y, id_x = await asyncio.gather(
            self.submit(req_y),
            self.submit(req_x),
        )
        return {"leg_y": id_y, "leg_x": id_x}

    async def cancel(self, local_id: str) -> bool:
        """Cancel an open order by local_id. Returns True if cancel was sent."""
        record = self._orders.get(local_id)
        if record is None:
            logger.warning(f"OrderManager: cancel — unknown local_id={local_id}")
            return False
        if record.status not in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY):
            return False

        router = self.routers.get(record.request.venue)
        if router is None or record.exchange_id is None:
            record.status = OrderStatus.CANCELLED
            await self._notify_close(record)
            return True

        try:
            await router.cancel_order(record.exchange_id, record.request.symbol)
            record.status = OrderStatus.CANCELLED
            logger.info(f"OrderManager: cancelled local_id={local_id}")
            await self._notify_close(record)
            return True
        except Exception as exc:
            logger.error(f"OrderManager: cancel failed local_id={local_id} err={exc}")
            return False

    # ------------------------------------------------------------------
    # Sprint 28: Internal callback dispatchers
    # ------------------------------------------------------------------

    async def _notify_fill(self, record: OrderRecord) -> None:
        """
        Fire all on_fill callbacks for a FILLED order.
        Callbacks run sequentially; errors are logged but do not abort the chain.
        """
        for cb in self._on_fill_callbacks:
            try:
                await cb(record)
            except Exception as exc:
                logger.error(
                    f"OrderManager: on_fill callback [{getattr(cb, '__name__', cb)}] "
                    f"raised for local_id={record.local_id}: {exc}"
                )

    async def _notify_close(self, record: OrderRecord) -> None:
        """
        Fire all on_close callbacks for a non-fill terminal order
        (CANCELLED / FAILED / TIMED_OUT).
        """
        for cb in self._on_close_callbacks:
            try:
                await cb(record)
            except Exception as exc:
                logger.error(
                    f"OrderManager: on_close callback [{getattr(cb, '__name__', cb)}] "
                    f"raised for local_id={record.local_id}: {exc}"
                )

    # ------------------------------------------------------------------
    # Background timeout monitor
    # ------------------------------------------------------------------

    async def _timeout_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.monitor_interval_s)
            await self._check_timeouts()

    async def _check_timeouts(self) -> None:
        async with self._lock:
            stale = [
                r for r in self._orders.values()
                if r.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY)
            ]
        for record in stale:
            otype   = record.request.order_type
            timeout = (
                self.cfg.market_timeout_s
                if otype == OrderType.MARKET
                else self.cfg.limit_timeout_s
            )
            if record.age_seconds > timeout:
                logger.warning(
                    f"OrderManager: timeout local_id={record.local_id} "
                    f"age={record.age_seconds:.1f}s > {timeout}s — cancelling"
                )
                cancelled = await self.cancel(record.local_id)
                if not cancelled:
                    record.status = OrderStatus.TIMED_OUT
                    await self._notify_close(record)

    def _prune_history(self) -> None:
        """Keep only the most recent max_history terminal records."""
        terminal = [
            (lid, r) for lid, r in self._orders.items()
            if r.status in (
                OrderStatus.FILLED, OrderStatus.CANCELLED,
                OrderStatus.FAILED, OrderStatus.TIMED_OUT
            )
        ]
        if len(terminal) > self.cfg.max_history:
            terminal.sort(key=lambda x: x[1].submitted_at)
            for lid, _ in terminal[:len(terminal) - self.cfg.max_history]:
                del self._orders[lid]

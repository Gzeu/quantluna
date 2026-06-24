"""
execution/live_trader.py  —  QuantLuna Sprint 4 v2 + Sprint 5 StateBus integration

Real WebSocket live trading engine:
  - Bybit /v5/public/linear  OR  Binance fstream (auto-select via config)
  - asyncio.Queue[PriceTick] producer/consumer architecture
  - Explicit TraderState machine: IDLE→WARMING_UP→ACTIVE→IN_POSITION→CLOSING→HALTED
  - _close_position() executes actual legs via OrderManager
  - Kalman warm-up inhibit on WS reconnect (beta preserved)
  - Logging at real time intervals (not ts.second == 0)
  - Sprint 5: publish_state() broadcasts to StateBus on every tick
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from .order_manager import ExecutionConfig, FillPair, OrderManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class TraderState(Enum):
    IDLE        = "idle"
    WARMING_UP  = "warming_up"
    ACTIVE      = "active"
    IN_POSITION = "in_position"
    CLOSING     = "closing"
    HALTED      = "halted"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PriceTick:
    symbol: str
    price: float
    ts: pd.Timestamp
    volume_24h: float = 0.0
    bid: float = 0.0
    ask: float = 0.0


@dataclass
class LiveConfig:
    sym_y: str
    sym_x: str
    exchange: str = "bybit"
    capital_usdt: float = 10_000.0
    max_leverage: float = 2.0
    min_warmup_bars: int = 30
    log_interval_s: int = 60
    heartbeat_interval_s: int = 30
    max_daily_drawdown: float = 0.03
    exec_config: ExecutionConfig = field(default_factory=ExecutionConfig)
    state_bus_enabled: bool = True   # Sprint 5: publish to StateBus


# ---------------------------------------------------------------------------
# LiveTrader
# ---------------------------------------------------------------------------

class LiveTrader:
    """
    Async live trading engine for a single pair.
    Sprint 5: integrates with StateBus for dashboard broadcasting.
    """

    def __init__(self, config: LiveConfig, signal_gen, portfolio_risk, state_bus=None):
        self.cfg = config
        self.signal_gen = signal_gen
        self.portfolio = portfolio_risk
        self._bus = state_bus  # Optional[StateBus] — injected from main.py

        self._state: TraderState = TraderState.IDLE
        self._queue: asyncio.Queue[PriceTick] = asyncio.Queue(maxsize=1000)

        self._price_y: float = 0.0
        self._price_x: float = 0.0
        self._last_tick_y: Optional[PriceTick] = None
        self._last_tick_x: Optional[PriceTick] = None

        self._warming_bars: int = 0

        self._entry_side_y: Optional[str] = None
        self._entry_side_x: Optional[str] = None
        self._entry_qty_y: float = 0.0
        self._entry_qty_x: float = 0.0
        self._entry_fill: Optional[FillPair] = None

        self._last_log_ts: pd.Timestamp = pd.Timestamp.min
        self._log_interval = pd.Timedelta(seconds=config.log_interval_s)

        self._daily_pnl: float = 0.0
        self._realized_pnl: float = 0.0
        self._total_fees: float = 0.0
        self._trade_count: int = 0
        self._daily_reset_date: Optional[pd.Timestamp] = None

        self.orders: Optional[OrderManager] = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self):
        logger.info(
            f"LiveTrader starting | pair={self.cfg.sym_y}/{self.cfg.sym_x} "
            f"| exchange={self.cfg.exchange}"
        )
        if self._bus:
            self._bus.update({
                "sym_y": self.cfg.sym_y,
                "sym_x": self.cfg.sym_x,
                "exchange": self.cfg.exchange,
                "trader_state": TraderState.WARMING_UP.value,
            })
        self._state = TraderState.WARMING_UP

        async with OrderManager(self.cfg.exec_config) as orders:
            self.orders = orders
            try:
                await asyncio.gather(
                    self._ws_feed(),
                    self._consumer(),
                    self._heartbeat(),
                )
            except asyncio.CancelledError:
                logger.info("LiveTrader cancelled — shutting down")
            except Exception as exc:
                logger.exception(f"LiveTrader fatal: {exc}")
                self._state = TraderState.HALTED
                if self._bus:
                    self._bus.update({"trader_state": TraderState.HALTED.value})
            finally:
                self.orders = None

    # ------------------------------------------------------------------
    # WebSocket feeds
    # ------------------------------------------------------------------

    async def _ws_feed(self):
        if self.cfg.exchange == "binance":
            await self._ws_feed_binance()
        else:
            await self._ws_feed_bybit()

    async def _ws_feed_bybit(self):
        import websockets
        sym_y = self.cfg.sym_y.replace("/", "").replace(":USDT", "")
        sym_x = self.cfg.sym_x.replace("/", "").replace(":USDT", "")
        url = "wss://stream.bybit.com/v5/public/linear"
        sub_msg = json.dumps({
            "op": "subscribe",
            "args": [f"tickers.{sym_y}", f"tickers.{sym_x}"],
        })
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            await self._on_reconnect()
            try:
                await ws.send(sub_msg)
                async for raw in ws:
                    tick = self._parse_bybit_tick(raw)
                    if tick and not self._queue.full():
                        await self._queue.put(tick)
            except Exception as exc:
                logger.warning(f"Bybit WS error: {exc} — reconnecting in 2s")
                await asyncio.sleep(2)

    async def _ws_feed_binance(self):
        import websockets
        def fmt(sym: str) -> str:
            return sym.replace("/", "").replace(":USDT", "").lower()
        sy, sx = fmt(self.cfg.sym_y), fmt(self.cfg.sym_x)
        url = f"wss://fstream.binance.com/stream?streams={sy}@ticker/{sx}@ticker"
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            await self._on_reconnect()
            try:
                async for raw in ws:
                    tick = self._parse_binance_tick(raw)
                    if tick and not self._queue.full():
                        await self._queue.put(tick)
            except Exception as exc:
                logger.warning(f"Binance WS error: {exc} — reconnecting in 2s")
                await asyncio.sleep(2)

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_bybit_tick(self, raw: str) -> Optional[PriceTick]:
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            symbol_raw = data.get("symbol", "")
            last = data.get("lastPrice")
            if not last:
                return None
            symbol = f"{symbol_raw[:3]}/USDT:USDT" if len(symbol_raw) >= 3 else symbol_raw
            return PriceTick(
                symbol=symbol, price=float(last),
                ts=pd.Timestamp.now(tz="UTC"),
                bid=float(data.get("bid1Price") or last),
                ask=float(data.get("ask1Price") or last),
                volume_24h=float(data.get("volume24h") or 0),
            )
        except Exception:
            return None

    def _parse_binance_tick(self, raw: str) -> Optional[PriceTick]:
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            if data.get("e") != "24hrTicker":
                return None
            symbol_raw = data.get("s", "")
            last = data.get("c")
            if not last:
                return None
            symbol = f"{symbol_raw[:3]}/USDT:USDT" if len(symbol_raw) >= 3 else symbol_raw
            return PriceTick(
                symbol=symbol, price=float(last),
                ts=pd.Timestamp.now(tz="UTC"),
                bid=float(data.get("b") or last),
                ask=float(data.get("a") or last),
                volume_24h=float(data.get("v") or 0),
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Consumer
    # ------------------------------------------------------------------

    async def _consumer(self):
        while self._state != TraderState.HALTED:
            tick = await self._queue.get()
            self._update_prices(tick)
            if self._price_y > 0 and self._price_x > 0:
                await self._on_tick(tick.ts)
            self._queue.task_done()

    def _update_prices(self, tick: PriceTick):
        sym_base = tick.symbol.split("/")[0]
        y_base = self.cfg.sym_y.split("/")[0]
        x_base = self.cfg.sym_x.split("/")[0]
        if sym_base == y_base:
            self._price_y = tick.price
            self._last_tick_y = tick
        elif sym_base == x_base:
            self._price_x = tick.price
            self._last_tick_x = tick

    # ------------------------------------------------------------------
    # Core tick handler
    # ------------------------------------------------------------------

    async def _on_tick(self, ts: pd.Timestamp):
        self._reset_daily_pnl_if_needed(ts)

        if self._state == TraderState.WARMING_UP:
            self._warming_bars += 1
            if self._warming_bars >= self.cfg.min_warmup_bars:
                self._state = TraderState.ACTIVE
                logger.info(f"Warm-up complete ({self._warming_bars} bars) — ACTIVE")
                if self._bus:
                    self._bus.update({"trader_state": TraderState.ACTIVE.value})
            return

        if self._state not in (TraderState.ACTIVE, TraderState.IN_POSITION):
            return

        sig = self.signal_gen.on_tick(self._price_y, self._price_x, ts)
        if sig is None:
            return

        # Publish to StateBus
        self._publish_state(sig, ts)

        # Periodic logging
        if (ts - self._last_log_ts) >= self._log_interval:
            logger.info(
                f"[{ts}] state={self._state.value} | "
                f"z={getattr(sig, 'zscore', float('nan')):.3f} | "
                f"Y={self._price_y:.4f} X={self._price_x:.4f} | "
                f"daily_pnl={self._daily_pnl:.2f}"
            )
            self._last_log_ts = ts

        if self._state == TraderState.IN_POSITION and getattr(sig, "exit", False):
            await self._close_position(sig, ts)
            return

        if self._state == TraderState.ACTIVE and getattr(sig, "entry", False):
            await self._open_position(sig, ts)

    # ------------------------------------------------------------------
    # StateBus publish
    # ------------------------------------------------------------------

    def _publish_state(self, sig, ts: pd.Timestamp):
        """Publish current trading state to StateBus (Sprint 5)."""
        if not self._bus or not self.cfg.state_bus_enabled:
            return

        open_pnl = self._compute_open_pnl() if self._state == TraderState.IN_POSITION else 0.0

        self._bus.update({
            "trader_state": self._state.value,
            "price_y": self._price_y,
            "price_x": self._price_x,
            "timestamp_utc": ts.isoformat(),
            "zscore": getattr(sig, "zscore", 0.0),
            "spread": getattr(sig, "spread", 0.0),
            "hedge_ratio": getattr(sig, "hedge_ratio", 0.0),
            "kalman_gain": getattr(sig, "kalman_gain", 0.0),
            "kalman_uncertainty": getattr(sig, "kalman_uncertainty", 0.0),
            "half_life": getattr(sig, "half_life", 0.0),
            "regime": getattr(sig, "regime", "unknown"),
            "realized_pnl": self._realized_pnl,
            "open_pnl": open_pnl,
            "daily_pnl": self._daily_pnl,
            "total_fees_usdt": self._total_fees,
            "trade_count": self._trade_count,
            "in_position": self._state == TraderState.IN_POSITION,
            "entry_side_y": self._entry_side_y or "",
            "entry_side_x": self._entry_side_x or "",
            "entry_price_y": self._entry_fill.leg_y.fill_price if self._entry_fill else 0.0,
            "entry_price_x": self._entry_fill.leg_x.fill_price if self._entry_fill else 0.0,
            "qty_y": self._entry_qty_y,
            "qty_x": self._entry_qty_x,
        })

    def _compute_open_pnl(self) -> float:
        """Unrealized PnL based on current prices vs entry fill."""
        if not self._entry_fill:
            return 0.0
        gross = (
            (self._price_y - self._entry_fill.leg_y.fill_price)
            * self._entry_qty_y
            * (1 if self._entry_side_y == "buy" else -1)
        ) + (
            (self._price_x - self._entry_fill.leg_x.fill_price)
            * self._entry_qty_x
            * (1 if self._entry_side_x == "buy" else -1)
        )
        return gross

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    async def _open_position(self, sig, ts: pd.Timestamp):
        if not self.portfolio.is_active:
            logger.debug("Portfolio risk inactive — skipping entry")
            return

        if abs(self._daily_pnl) >= self.cfg.capital_usdt * self.cfg.max_daily_drawdown:
            logger.warning(f"Daily DD limit ({self._daily_pnl:.2f} USDT) — halting")
            self._state = TraderState.HALTED
            if self._bus:
                self._bus.update({"trader_state": TraderState.HALTED.value})
            return

        hedge_ratio = getattr(sig, "hedge_ratio", 1.0)
        notional_y = self.cfg.capital_usdt * self.cfg.max_leverage / 2
        qty_y = notional_y / self._price_y
        qty_x = qty_y * abs(hedge_ratio)

        if getattr(sig, "direction", 1) > 0:
            side_y, side_x = "buy", "sell"
        else:
            side_y, side_x = "sell", "buy"

        logger.info(
            f"ENTRY | {side_y} {self.cfg.sym_y} {qty_y:.4f}@{self._price_y:.4f} "
            f"| {side_x} {self.cfg.sym_x} {qty_x:.4f}@{self._price_x:.4f} "
            f"| z={getattr(sig, 'zscore', 0.0):.3f} | beta={hedge_ratio:.4f}"
        )

        self._state = TraderState.IN_POSITION
        if self._bus:
            self._bus.update({"trader_state": TraderState.IN_POSITION.value})

        try:
            fill_pair = await self.orders.execute_pair(
                self.cfg.sym_y, side_y, qty_y, self._price_y,
                self.cfg.sym_x, side_x, qty_x, self._price_x,
            )
            self._entry_side_y = side_y
            self._entry_side_x = side_x
            self._entry_qty_y = qty_y
            self._entry_qty_x = qty_x
            self._entry_fill = fill_pair
            self._total_fees += fill_pair.total_fee_usdt
        except Exception as exc:
            logger.error(f"ENTRY failed: {exc} — reverting to ACTIVE")
            self._state = TraderState.ACTIVE
            if self._bus:
                self._bus.update({"trader_state": TraderState.ACTIVE.value})

    async def _close_position(self, sig, ts: pd.Timestamp):
        self._state = TraderState.CLOSING
        if self._bus:
            self._bus.update({"trader_state": TraderState.CLOSING.value})

        close_side_y = "sell" if self._entry_side_y == "buy" else "buy"
        close_side_x = "sell" if self._entry_side_x == "buy" else "buy"

        logger.info(
            f"EXIT | {close_side_y} {self.cfg.sym_y} {self._entry_qty_y:.4f} "
            f"| {close_side_x} {self.cfg.sym_x} {self._entry_qty_x:.4f} "
            f"| reason={getattr(sig, 'reason', 'unknown')}"
        )

        try:
            exit_fill = await self.orders.execute_pair(
                self.cfg.sym_y, close_side_y, self._entry_qty_y, self._price_y,
                self.cfg.sym_x, close_side_x, self._entry_qty_x, self._price_x,
            )
            pnl = self._compute_pnl(exit_fill)
            self._daily_pnl += pnl
            self._realized_pnl += pnl
            self._total_fees += exit_fill.total_fee_usdt
            self._trade_count += 1
            self.portfolio.record_trade(pnl)

            # Record to StateBus trade history
            if self._bus:
                self._bus.record_trade({
                    "ts": time.time(),
                    "side": self._entry_side_y or "",
                    "pnl": pnl,
                    "entry_y": self._entry_fill.leg_y.fill_price if self._entry_fill else 0.0,
                    "entry_x": self._entry_fill.leg_x.fill_price if self._entry_fill else 0.0,
                    "exit_y": exit_fill.leg_y.fill_price,
                    "exit_x": exit_fill.leg_x.fill_price,
                    "fees": exit_fill.total_fee_usdt,
                })

            logger.info(
                f"EXIT complete | PnL={pnl:.2f} USDT | "
                f"realized={self._realized_pnl:.2f} | trades={self._trade_count}"
            )
        except Exception as exc:
            logger.error(f"EXIT failed: {exc} — position may still be open!")
        finally:
            self._state = TraderState.ACTIVE
            if self._bus:
                self._bus.update({"trader_state": TraderState.ACTIVE.value})
            self._entry_side_y = self._entry_side_x = None
            self._entry_qty_y = self._entry_qty_x = 0.0
            self._entry_fill = None

    def _compute_pnl(self, exit_fill: FillPair) -> float:
        if self._entry_fill is None:
            return 0.0
        gross = (
            (exit_fill.leg_y.fill_price - self._entry_fill.leg_y.fill_price)
            * self._entry_qty_y
            * (1 if self._entry_side_y == "buy" else -1)
        ) + (
            (exit_fill.leg_x.fill_price - self._entry_fill.leg_x.fill_price)
            * self._entry_qty_x
            * (1 if self._entry_side_x == "buy" else -1)
        )
        fees = exit_fill.total_fee_usdt + (self._entry_fill.total_fee_usdt if self._entry_fill else 0.0)
        return gross - fees

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat(self):
        while self._state != TraderState.HALTED:
            await asyncio.sleep(self.cfg.heartbeat_interval_s)
            now = pd.Timestamp.now(tz="UTC")
            logger.debug(
                f"Heartbeat | state={self._state.value} | "
                f"queue={self._queue.qsize()} | Y={self._price_y:.4f} X={self._price_x:.4f}"
            )
            if self._last_tick_y is not None:
                age = (now - self._last_tick_y.ts).total_seconds()
                if age > self.cfg.heartbeat_interval_s * 2:
                    logger.warning(
                        f"Stale feed {self.cfg.sym_y}: last tick {age:.0f}s ago"
                    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _on_reconnect(self):
        if self._state not in (TraderState.IDLE, TraderState.HALTED):
            logger.warning(
                "WS reconnected — resetting warm-up | beta preserved, entry inhibited "
                f"for {self.cfg.min_warmup_bars} bars"
            )
            self._warming_bars = 0
            if self._state != TraderState.IN_POSITION:
                self._state = TraderState.WARMING_UP
                if self._bus:
                    self._bus.update({"trader_state": TraderState.WARMING_UP.value})

    def _reset_daily_pnl_if_needed(self, ts: pd.Timestamp):
        date = ts.normalize()
        if self._daily_reset_date is None or date > self._daily_reset_date:
            if self._daily_reset_date is not None:
                logger.info(f"Daily PnL reset | prev={self._daily_pnl:.2f} | date={date.date()}")
            self._daily_pnl = 0.0
            self._daily_reset_date = date

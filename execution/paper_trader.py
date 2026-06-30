"""
execution/paper_trader.py  —  QuantLuna Paper Trading Engine

Sprint 11 — simulare realistă de paper trading:
  - Fill simulation cu slippage model configurabil (percentage + fixed spread)
  - Latency simulation (async sleep configurable)
  - Fee model realist: maker/taker per exchange
  - Position tracking identic cu LiveTrader (fără ordine reale pe exchange)
  - PnL tracking complet: realized, unrealized, daily, fees
  - SQLite trade persistence (același schema ca LiveTrader)
  - StateBus integration pentru dashboard live
  - Telegram notifications support
  - Funcționează cu același signal pipeline ca LiveTrader (drop-in replacement)
  - WebSocket feed real de la exchange (prețuri reale, fills simulate)

Slippage model:
  fill_price = mid_price * (1 ± slippage_pct) ± fixed_spread_usdt
  - buy:  mid * (1 + slippage_pct/2) + fixed_spread/2
  - sell: mid * (1 - slippage_pct/2) - fixed_spread/2

Fee model default (futures):
  - Bybit: taker 0.055%, maker 0.02%
  - Binance: taker 0.04%, maker 0.02%

Usage:
    from execution.paper_trader import PaperTrader, PaperConfig

    trader = PaperTrader(
        config=PaperConfig(
            sym_y="BTCUSDT",
            sym_x="ETHUSDT",
            exchange="bybit",
            capital_usdt=10_000.0,
            slippage_pct=0.001,   # 0.1% slippage
            latency_ms=50,         # 50ms fill latency simulation
        ),
        signal_gen=my_signal_gen,
    )
    await trader.run()
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Deque, Dict, List, Optional

import pandas as pd

from strategy.signal_adapter import LiveSignalAdapter
from risk import PortfolioAllocator, AllocatorConfig
from risk.drawdown_controller import DDLevel
from notifications.telegram_notifier import TelegramNotifier, NotifierConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fee models per exchange
# ---------------------------------------------------------------------------
_FEE_TAKER: Dict[str, float] = {
    "bybit":   0.00055,
    "binance": 0.00040,
}
_FEE_MAKER: Dict[str, float] = {
    "bybit":   0.00020,
    "binance": 0.00020,
}


class PaperState(Enum):
    IDLE        = "idle"
    WARMING_UP  = "warming_up"
    ACTIVE      = "active"
    IN_POSITION = "in_position"
    HALTED      = "halted"


@dataclass
class SimulatedFill:
    symbol: str
    side: str
    qty: float
    fill_price: float
    fee_usdt: float
    ts: pd.Timestamp


@dataclass
class SimulatedFillPair:
    leg_y: SimulatedFill
    leg_x: SimulatedFill

    @property
    def total_fee_usdt(self) -> float:
        return self.leg_y.fee_usdt + self.leg_x.fee_usdt


@dataclass
class PaperConfig:
    sym_y: str
    sym_x: str
    exchange: str = "bybit"
    capital_usdt: float = 10_000.0
    min_warmup_bars: int = 30
    log_interval_s: int = 60
    heartbeat_interval_s: int = 30
    max_daily_drawdown: float = 0.03
    # Slippage model
    slippage_pct: float = 0.0005       # 0.05% default (realistic futures)
    fixed_spread_usdt: float = 0.0     # fixed spread component
    use_taker_fees: bool = True        # taker (market orders)
    # Latency simulation
    latency_ms: float = 30.0           # milliseconds of fill delay simulation
    # Risk
    allocator_config: Optional[AllocatorConfig] = None
    queue_drop_halt_threshold: int = 100
    queue_maxsize: int = 1_000
    spread_buffer_size: int = 500
    # Persistence
    trade_db_path: str = "paper_trades.db"
    # Notifications
    notifier_config: Optional[NotifierConfig] = None
    state_bus_enabled: bool = True


class PaperTrader:
    """
    Paper Trading Engine — drop-in replacement pentru LiveTrader.

    Folosește prețuri reale de pe WebSocket (același feed ca LiveTrader)
    dar simulează fill-urile local cu slippage + fees realist.
    Zero ordine reale trimise la exchange.
    """

    def __init__(
        self,
        config: PaperConfig,
        signal_gen,
        allocator: Optional[PortfolioAllocator] = None,
        state_bus=None,
    ) -> None:
        self.cfg = config
        self._bus = state_bus

        if isinstance(signal_gen, LiveSignalAdapter):
            self.signal_gen = signal_gen
        else:
            self.signal_gen = LiveSignalAdapter(signal_gen)

        if allocator is not None:
            self.allocator = allocator
        else:
            alloc_cfg = config.allocator_config or AllocatorConfig(
                capital_usd=config.capital_usdt
            )
            self.allocator = PortfolioAllocator(alloc_cfg)

        self._notifier: Optional[TelegramNotifier] = None
        if config.notifier_config:
            self._notifier = TelegramNotifier(config.notifier_config)

        self._fee_rate = (
            _FEE_TAKER.get(config.exchange, 0.00055)
            if config.use_taker_fees
            else _FEE_MAKER.get(config.exchange, 0.00020)
        )

        self._state = PaperState.IDLE
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=config.queue_maxsize)
        self._queue_drops = 0
        self._price_y: float = 0.0
        self._price_x: float = 0.0
        self._bid_y: float = 0.0
        self._ask_y: float = 0.0
        self._bid_x: float = 0.0
        self._ask_x: float = 0.0
        self._warming_bars: int = 0
        self._spread_buffer: Deque[float] = deque(maxlen=config.spread_buffer_size)

        # Position state
        self._entry_fill: Optional[SimulatedFillPair] = None
        self._entry_side_y: Optional[str] = None
        self._entry_side_x: Optional[str] = None
        self._entry_qty_y: float = 0.0
        self._entry_qty_x: float = 0.0
        self._entry_zscore: float = 0.0
        self._entry_hedge_ratio: float = 0.0
        self._entry_notional: float = 0.0

        # PnL tracking
        self._realized_pnl: float = 0.0
        self._open_pnl: float = 0.0
        self._daily_pnl: float = 0.0
        self._total_fees: float = 0.0
        self._trade_count: int = 0
        self._win_count: int = 0
        self._trade_pnl_history: List[float] = []
        self._daily_reset_date: Optional[pd.Timestamp] = None
        self._last_log_ts: pd.Timestamp = pd.Timestamp.min.tz_localize("UTC")
        self._log_interval = pd.Timedelta(seconds=config.log_interval_s)

        self._trade_db = _PaperTradeDB(config.trade_db_path)
        self._trade_pnl_history = self._trade_db.load_pnl_fractions(200)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        logger.info(
            f"[PaperTrader] START | pair={self.cfg.sym_y}/{self.cfg.sym_x} "
            f"exchange={self.cfg.exchange} | capital={self.cfg.capital_usdt:.0f} USDT "
            f"| slippage={self.cfg.slippage_pct:.3%} | fee={self._fee_rate:.4%}"
        )
        self._state = PaperState.WARMING_UP
        self._update_bus({"trader_state": "warming_up", "mode": "paper"})

        try:
            await asyncio.gather(
                self._ws_feed(),
                self._consumer(),
                self._heartbeat(),
            )
        except asyncio.CancelledError:
            logger.info("[PaperTrader] cancelled — shutdown")
        except Exception as exc:
            logger.exception(f"[PaperTrader] fatal: {exc}")
            self._state = PaperState.HALTED
            self._update_bus({"trader_state": "halted"})
            if self._notifier:
                await self._notifier.send_halt(reason="FATAL", details=str(exc), pair=self._pair_id)

    # ------------------------------------------------------------------
    # WebSocket feeds (real price, simulated fills)
    # ------------------------------------------------------------------

    async def _ws_feed(self) -> None:
        if self.cfg.exchange == "binance":
            await self._ws_feed_binance()
        else:
            await self._ws_feed_bybit()

    async def _ws_feed_bybit(self) -> None:
        import websockets
        sym_y = self.cfg.sym_y.replace("/", "").replace(":USDT", "")
        sym_x = self.cfg.sym_x.replace("/", "").replace(":USDT", "")
        url = "wss://stream.bybit.com/v5/public/linear"
        sub = json.dumps({"op": "subscribe", "args": [f"tickers.{sym_y}", f"tickers.{sym_x}"]})
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            try:
                await ws.send(sub)
                async for raw in ws:
                    tick = self._parse_bybit(raw)
                    if tick:
                        await self._enqueue(tick)
            except Exception as exc:
                logger.warning(f"[PaperTrader] Bybit WS error: {exc} — reconnecting")
                await asyncio.sleep(2)

    async def _ws_feed_binance(self) -> None:
        import websockets
        def fmt(s: str) -> str:
            return s.replace("/", "").replace(":USDT", "").lower()
        sy, sx = fmt(self.cfg.sym_y), fmt(self.cfg.sym_x)
        url = f"wss://fstream.binance.com/stream?streams={sy}@ticker/{sx}@ticker"
        async for ws in websockets.connect(url, ping_interval=20, ping_timeout=10):
            try:
                async for raw in ws:
                    tick = self._parse_binance(raw)
                    if tick:
                        await self._enqueue(tick)
            except Exception as exc:
                logger.warning(f"[PaperTrader] Binance WS error: {exc} — reconnecting")
                await asyncio.sleep(2)

    # ------------------------------------------------------------------
    # Tick parsing
    # ------------------------------------------------------------------

    def _parse_bybit(self, raw: str) -> Optional[dict]:
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            sym = data.get("symbol", "")
            last = data.get("lastPrice")
            if not last or not sym:
                return None
            return {
                "symbol": sym,
                "price": float(last),
                "bid": float(data.get("bid1Price") or last),
                "ask": float(data.get("ask1Price") or last),
                "ts": pd.Timestamp.now(tz="UTC"),
            }
        except Exception:
            return None

    def _parse_binance(self, raw: str) -> Optional[dict]:
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            if data.get("e") != "24hrTicker":
                return None
            sym = data.get("s", "")
            last = data.get("c")
            if not last or not sym:
                return None
            return {
                "symbol": sym,
                "price": float(last),
                "bid": float(data.get("b") or last),
                "ask": float(data.get("a") or last),
                "ts": pd.Timestamp.now(tz="UTC"),
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------

    async def _enqueue(self, tick: dict) -> None:
        if self._queue.full():
            self._queue_drops += 1
            if self._queue_drops >= self.cfg.queue_drop_halt_threshold:
                logger.critical(f"[PaperTrader] Queue overflow HALT ({self._queue_drops} drops)")
                self._state = PaperState.HALTED
                if self._notifier:
                    await self._notifier.send_queue_overflow(
                        self._queue_drops, self.cfg.queue_drop_halt_threshold, self._pair_id
                    )
        else:
            self._queue_drops = 0
            await self._queue.put(tick)

    # ------------------------------------------------------------------
    # Consumer
    # ------------------------------------------------------------------

    async def _consumer(self) -> None:
        while self._state != PaperState.HALTED:
            tick = await self._queue.get()
            self._update_prices(tick)
            if self._price_y > 0 and self._price_x > 0:
                await self._on_tick(tick["ts"])
            self._queue.task_done()

    def _update_prices(self, tick: dict) -> None:
        sym = tick["symbol"].upper()
        sy = self.cfg.sym_y.replace("/", "").replace(":USDT", "").upper()
        sx = self.cfg.sym_x.replace("/", "").replace(":USDT", "").upper()
        # Bybit symbols sunt fără suffix (BTCUSDT), Binance la fel
        sym_clean = sym.replace("-PERP", "").replace("PERP", "")
        if sym_clean == sy or sym_clean == sy + "USDT":
            self._price_y = tick["price"]
            self._bid_y = tick["bid"]
            self._ask_y = tick["ask"]
        elif sym_clean == sx or sym_clean == sx + "USDT":
            self._price_x = tick["price"]
            self._bid_x = tick["bid"]
            self._ask_x = tick["ask"]

    # ------------------------------------------------------------------
    # On tick
    # ------------------------------------------------------------------

    async def _on_tick(self, ts: pd.Timestamp) -> None:
        self._reset_daily_pnl_if_needed(ts)

        if self._state == PaperState.WARMING_UP:
            self._warming_bars += 1
            if self._warming_bars >= self.cfg.min_warmup_bars:
                self._state = PaperState.ACTIVE
                logger.info(f"[PaperTrader] Warm-up complete ({self._warming_bars} bars) — ACTIVE")
                self._update_bus({"trader_state": "active"})
            return

        if self._state not in (PaperState.ACTIVE, PaperState.IN_POSITION):
            return

        sig = self.signal_gen.on_tick(self._price_y, self._price_x, ts)
        if sig is None:
            return

        spread_val = sig.spread if hasattr(sig, "spread") else (
            self._price_y - sig.hedge_ratio * self._price_x
        )
        self._spread_buffer.append(spread_val)

        snap = self.allocator.update_state(
            open_pnl_per_pair={self._pair_id: self._compute_open_pnl() if self._state == PaperState.IN_POSITION else 0.0},
            spread_updates={self._pair_id: spread_val},
        )

        if snap.level == DDLevel.HARD_STOP:
            logger.critical(f"[PaperTrader] HARD_STOP | notes={snap.notes}")
            if self._notifier:
                await self._notifier.send_halt("HARD_STOP", f"DD limit atins. {snap.notes}", self._pair_id)
            await self._close_position(sig, ts, reason="HARD_STOP")
            self._state = PaperState.HALTED
            self._update_bus({"trader_state": "halted"})
            return

        if self._pair_id in snap.pairs_force_close and self._state == PaperState.IN_POSITION:
            logger.warning(f"[PaperTrader] PAIR_DD exceeded — force close")
            if self._notifier:
                await self._notifier.send_halt("PAIR_DD", pair=self._pair_id)
            await self._close_position(sig, ts, reason="PAIR_DD")
            return

        self._open_pnl = self._compute_open_pnl() if self._state == PaperState.IN_POSITION else 0.0
        self._publish_state(sig, ts)

        if (ts - self._last_log_ts) >= self._log_interval:
            logger.info(
                f"[Paper] {ts.strftime('%H:%M:%S')} | state={self._state.value} "
                f"| z={sig.zscore:+.3f} | β={sig.hedge_ratio:.4f} "
                f"| Y={self._price_y:.4f} X={self._price_x:.4f} "
                f"| open_pnl={self._open_pnl:+.2f} | realized={self._realized_pnl:+.2f} "
                f"| trades={self._trade_count} | fees={self._total_fees:.3f}"
            )
            self._last_log_ts = ts

        if self._state == PaperState.IN_POSITION and getattr(sig, "exit", False):
            await self._close_position(sig, ts)
            return

        if self._state == PaperState.ACTIVE and getattr(sig, "entry", False):
            await self._open_position(sig, ts)

    # ------------------------------------------------------------------
    # Fill simulation
    # ------------------------------------------------------------------

    def _simulate_fill(
        self,
        symbol: str,
        side: str,
        qty: float,
        mid_price: float,
        bid: float,
        ask: float,
    ) -> SimulatedFill:
        """
        Slippage model:
          buy  → fill at ask + slippage_pct/2 * mid
          sell → fill at bid - slippage_pct/2 * mid
        Fee = qty * fill_price * fee_rate
        """
        slip = mid_price * self.cfg.slippage_pct / 2.0
        if side == "buy":
            fill_price = ask + slip + self.cfg.fixed_spread_usdt / 2.0
        else:
            fill_price = bid - slip - self.cfg.fixed_spread_usdt / 2.0
        fill_price = max(fill_price, 1e-8)
        fee = qty * fill_price * self._fee_rate
        return SimulatedFill(
            symbol=symbol,
            side=side,
            qty=qty,
            fill_price=fill_price,
            fee_usdt=fee,
            ts=pd.Timestamp.now(tz="UTC"),
        )

    async def _execute_pair(
        self,
        sym_y: str, side_y: str, qty_y: float, mid_y: float,
        sym_x: str, side_x: str, qty_x: float, mid_x: float,
    ) -> SimulatedFillPair:
        """Simulează fill cu latency artificială."""
        if self.cfg.latency_ms > 0:
            await asyncio.sleep(self.cfg.latency_ms / 1000.0)
        fill_y = self._simulate_fill(sym_y, side_y, qty_y, mid_y, self._bid_y or mid_y, self._ask_y or mid_y)
        fill_x = self._simulate_fill(sym_x, side_x, qty_x, mid_x, self._bid_x or mid_x, self._ask_x or mid_x)
        return SimulatedFillPair(leg_y=fill_y, leg_x=fill_x)

    # ------------------------------------------------------------------
    # Open / Close
    # ------------------------------------------------------------------

    async def _open_position(self, sig, ts: pd.Timestamp) -> None:
        if abs(self._daily_pnl) >= self.cfg.capital_usdt * self.cfg.max_daily_drawdown:
            logger.warning(f"[PaperTrader] Daily DD limit | daily_pnl={self._daily_pnl:.2f}")
            self._state = PaperState.HALTED
            self._update_bus({"trader_state": "halted"})
            if self._notifier:
                await self._notifier.send_halt("DAILY_DD", f"daily_pnl={self._daily_pnl:.2f}", self._pair_id)
            return

        pnl_series = pd.Series(self._trade_pnl_history) if self._trade_pnl_history else None
        if len(self._spread_buffer) >= self.cfg.min_warmup_bars:
            spread_series = pd.Series(list(self._spread_buffer))
        else:
            spread_series = pd.Series([sig.spread] * max(self.cfg.min_warmup_bars, 1))

        decision = self.allocator.request_entry(
            pair_id=self._pair_id,
            candidate_spread=spread_series,
            trade_pnl_history=pnl_series,
            current_zscore=sig.zscore,
            entry_beta=sig.hedge_ratio,
        )
        if not decision.allowed:
            logger.info(f"[PaperTrader] Entry BLOCKED: {decision.reject_reason}")
            return

        notional_y = decision.notional_usd
        hedge_ratio = sig.hedge_ratio
        qty_y = notional_y / self._price_y
        qty_x = qty_y * abs(hedge_ratio)
        side_y = "buy" if getattr(sig, "direction", 1) > 0 else "sell"
        side_x = "sell" if side_y == "buy" else "buy"

        logger.info(
            f"[Paper ENTRY] {side_y} {self.cfg.sym_y} {qty_y:.4f}@~{self._price_y:.4f} "
            f"| {side_x} {self.cfg.sym_x} {qty_x:.4f}@~{self._price_x:.4f} "
            f"| z={sig.zscore:+.3f} | β={hedge_ratio:.4f} | notional=${notional_y:.0f}"
        )

        fill_pair = await self._execute_pair(
            self.cfg.sym_y, side_y, qty_y, self._price_y,
            self.cfg.sym_x, side_x, qty_x, self._price_x,
        )

        self._entry_fill = fill_pair
        self._entry_side_y = side_y
        self._entry_side_x = side_x
        self._entry_qty_y = qty_y
        self._entry_qty_x = qty_x
        self._entry_notional = notional_y
        self._entry_zscore = sig.zscore
        self._entry_hedge_ratio = hedge_ratio
        self._total_fees += fill_pair.total_fee_usdt
        self._state = PaperState.IN_POSITION
        self._update_bus({"trader_state": "in_position"})

        if self._notifier:
            await self._notifier.send_trade_entry(
                pair=self._pair_id,
                side_y=side_y,
                zscore=sig.zscore,
                notional_usd=notional_y,
                hedge_ratio=hedge_ratio,
                method=decision.kelly_result.method_used if decision.kelly_result else "vol_target",
                exchange=f"{self.cfg.exchange} [PAPER]",
            )

    async def _close_position(self, sig, ts: pd.Timestamp, reason: str = "signal") -> None:
        if self._state not in (PaperState.IN_POSITION,):
            return

        close_side_y = "sell" if self._entry_side_y == "buy" else "buy"
        close_side_x = "sell" if self._entry_side_x == "buy" else "buy"

        exit_fill = await self._execute_pair(
            self.cfg.sym_y, close_side_y, self._entry_qty_y, self._price_y,
            self.cfg.sym_x, close_side_x, self._entry_qty_x, self._price_x,
        )

        pnl = self._compute_pnl(exit_fill)
        self._daily_pnl += pnl
        self._realized_pnl += pnl
        self._total_fees += exit_fill.total_fee_usdt
        self._trade_count += 1
        if pnl >= 0:
            self._win_count += 1
        pnl_frac = pnl / max(self.cfg.capital_usdt, 1.0)
        self._trade_pnl_history.append(pnl_frac)
        if len(self._trade_pnl_history) > 200:
            self._trade_pnl_history = self._trade_pnl_history[-200:]

        self._trade_db.append({
            "ts": time.time(),
            "pair_y": self.cfg.sym_y,
            "pair_x": self.cfg.sym_x,
            "side_y": self._entry_side_y or "",
            "entry_y": self._entry_fill.leg_y.fill_price if self._entry_fill else 0.0,
            "entry_x": self._entry_fill.leg_x.fill_price if self._entry_fill else 0.0,
            "exit_y": exit_fill.leg_y.fill_price,
            "exit_x": exit_fill.leg_x.fill_price,
            "qty_y": self._entry_qty_y,
            "qty_x": self._entry_qty_x,
            "pnl_usdt": pnl,
            "fees_usdt": exit_fill.total_fee_usdt,
            "pnl_frac": pnl_frac,
            "zscore_entry": self._entry_zscore,
            "hedge_ratio": self._entry_hedge_ratio,
            "slippage_pct": self.cfg.slippage_pct,
        })

        logger.info(
            f"[Paper EXIT] PnL={pnl:+.2f} USDT ({pnl_frac:+.2%}) | "
            f"reason={reason} | realized={self._realized_pnl:+.2f} | trades={self._trade_count} | "
            f"win_rate={self.win_rate:.1%} | fees_total={self._total_fees:.3f}"
        )

        if self._notifier:
            await self._notifier.send_trade_exit(
                pair=self._pair_id,
                pnl_usd=pnl,
                pnl_pct=pnl_frac,
                trade_count=self._trade_count,
                reason=reason,
                fees_usd=exit_fill.total_fee_usdt,
            )

        self.allocator.record_exit(self._pair_id)
        self._state = PaperState.ACTIVE
        self._update_bus({"trader_state": "active"})
        self._entry_fill = None
        self._entry_side_y = self._entry_side_x = None
        self._entry_qty_y = self._entry_qty_x = 0.0
        self._entry_notional = self._entry_zscore = self._entry_hedge_ratio = 0.0
        self._open_pnl = 0.0

    # ------------------------------------------------------------------
    # PnL calculations
    # ------------------------------------------------------------------

    def _compute_pnl(self, exit_fill: SimulatedFillPair) -> float:
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

    def _compute_open_pnl(self) -> float:
        if not self._entry_fill:
            return 0.0
        return (
            (self._price_y - self._entry_fill.leg_y.fill_price)
            * self._entry_qty_y
            * (1 if self._entry_side_y == "buy" else -1)
        ) + (
            (self._price_x - self._entry_fill.leg_x.fill_price)
            * self._entry_qty_x
            * (1 if self._entry_side_x == "buy" else -1)
        )

    @property
    def win_rate(self) -> float:
        return self._win_count / max(self._trade_count, 1)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Returns performance snapshot dict."""
        return {
            "mode": "paper",
            "exchange": self.cfg.exchange,
            "pair": self._pair_id,
            "capital_usdt": self.cfg.capital_usdt,
            "realized_pnl": self._realized_pnl,
            "realized_pnl_pct": self._realized_pnl / max(self.cfg.capital_usdt, 1.0),
            "open_pnl": self._open_pnl,
            "daily_pnl": self._daily_pnl,
            "total_fees": self._total_fees,
            "trade_count": self._trade_count,
            "win_rate": self.win_rate,
            "slippage_pct": self.cfg.slippage_pct,
            "fee_rate": self._fee_rate,
            "state": self._state.value,
        }

    async def send_daily_summary(self) -> None:
        """Trimite sumar zilnic via Telegram (apelabil manual sau scheduled)."""
        if not self._notifier:
            return
        await self._notifier.send_daily_summary(
            realized_pnl=self._realized_pnl,
            trade_count=self._trade_count,
            win_rate=self.win_rate,
            max_dd=0.0,  # TODO: track max_dd in paper trader
            capital_usd=self.cfg.capital_usdt,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _pair_id(self) -> str:
        return f"{self.cfg.sym_y}/{self.cfg.sym_x}"

    def _publish_state(self, sig, ts: pd.Timestamp) -> None:
        if not self._bus or not self.cfg.state_bus_enabled:
            return
        self._bus.update({
            "mode": "paper",
            "trader_state": self._state.value,
            "price_y": self._price_y,
            "price_x": self._price_x,
            "timestamp_utc": ts.isoformat(),
            "zscore": sig.zscore,
            "spread": sig.spread,
            "hedge_ratio": sig.hedge_ratio,
            "realized_pnl": self._realized_pnl,
            "open_pnl": self._open_pnl,
            "daily_pnl": self._daily_pnl,
            "total_fees_usdt": self._total_fees,
            "trade_count": self._trade_count,
            "win_rate": self.win_rate,
            "in_position": self._state == PaperState.IN_POSITION,
            "entry_side_y": self._entry_side_y or "",
            "slippage_pct": self.cfg.slippage_pct,
            "fee_rate": self._fee_rate,
        })

    def _update_bus(self, data: dict) -> None:
        if self._bus and self.cfg.state_bus_enabled:
            self._bus.update(data)

    def _reset_daily_pnl_if_needed(self, ts: pd.Timestamp) -> None:
        date = ts.normalize()
        if self._daily_reset_date is None or date > self._daily_reset_date:
            if self._daily_reset_date is not None:
                logger.info(f"[PaperTrader] Daily PnL reset | prev={self._daily_pnl:+.2f} | date={date.date()}")
            self._daily_pnl = 0.0
            self._daily_reset_date = date

    async def _heartbeat(self) -> None:
        while self._state != PaperState.HALTED:
            await asyncio.sleep(self.cfg.heartbeat_interval_s)
            logger.debug(
                f"[Paper HB] state={self._state.value} | realized={self._realized_pnl:+.2f} "
                f"| open={self._open_pnl:+.2f} | trades={self._trade_count} "
                f"| win={self.win_rate:.1%} | fees={self._total_fees:.3f}"
            )


# ---------------------------------------------------------------------------
# SQLite persistence (paper trades)
# ---------------------------------------------------------------------------

class _PaperTradeDB:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        if db_path:
            self._init()

    def _init(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts            REAL    NOT NULL,
                    pair_y        TEXT,
                    pair_x        TEXT,
                    side_y        TEXT,
                    entry_y       REAL,
                    entry_x       REAL,
                    exit_y        REAL,
                    exit_x        REAL,
                    qty_y         REAL,
                    qty_x         REAL,
                    pnl_usdt      REAL,
                    fees_usdt     REAL,
                    pnl_frac      REAL,
                    zscore_entry  REAL,
                    hedge_ratio   REAL,
                    slippage_pct  REAL
                )
            """)
            conn.commit()

    def append(self, record: dict) -> None:
        if not self._path:
            return
        try:
            with sqlite3.connect(self._path) as conn:
                conn.execute("""
                    INSERT INTO paper_trades
                        (ts, pair_y, pair_x, side_y, entry_y, entry_x,
                         exit_y, exit_x, qty_y, qty_x, pnl_usdt, fees_usdt,
                         pnl_frac, zscore_entry, hedge_ratio, slippage_pct)
                    VALUES
                        (:ts, :pair_y, :pair_x, :side_y, :entry_y, :entry_x,
                         :exit_y, :exit_x, :qty_y, :qty_x, :pnl_usdt, :fees_usdt,
                         :pnl_frac, :zscore_entry, :hedge_ratio, :slippage_pct)
                """, record)
                conn.commit()
        except Exception as exc:
            logger.warning(f"PaperTradeDB write failed: {exc}")

    def load_pnl_fractions(self, limit: int = 200) -> List[float]:
        if not self._path:
            return []
        try:
            with sqlite3.connect(self._path) as conn:
                rows = conn.execute(
                    "SELECT pnl_frac FROM paper_trades ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [r[0] for r in reversed(rows) if r[0] is not None]
        except Exception as exc:
            logger.warning(f"PaperTradeDB load failed: {exc}")
            return []

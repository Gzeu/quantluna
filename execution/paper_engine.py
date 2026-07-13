"""
QuantLuna — Paper Trading Engine
Sprint 30

Simulator complet pentru paper/dry trading:
  - Market orders: fill instant la mid-price + slippage model
  - Limit orders: fill la price cross (next tick simulat)
  - Partial fills: probabilistic (10% sansa partial la ordin mare)
  - Latency jitter: 50-200ms simulat
  - Commission: Bybit taker 0.055% din notional
  - P&L tracking per pereche si global
  - Equity curve (snapshot la fiecare fill)
  - Trade log complet
  - Thread-safe (asyncio.Lock)

Usage:
    from execution.paper_engine import PaperTradingEngine

    engine = PaperTradingEngine(initial_capital=10000.0)

    order = await engine.submit_order(
        symbol="BTCUSDT",
        side="buy",
        qty=0.01,
        order_type="market",
        mid_price=65000.0,
        pair="BTC/ETH",
    )
    print(order.avg_fill_price, order.commission)

    snap = engine.snapshot()
    print(snap["equity_usdt"], snap["total_pnl"])
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from execution.paper_order import OrderSide, OrderStatus, OrderType, PaperOrder
from core.position_store import PositionStore

logger = logging.getLogger(__name__)

# Bybit taker commission rate
_TAKER_COMMISSION = 0.00055  # 0.055%

# Slippage model: market order gets mid +/- random(0, max_slip) * price
_SLIPPAGE_BPS_MAX = 3   # max 3 basis points = 0.03%

# Partial fill probability for large orders (> threshold notional)
_PARTIAL_FILL_PROB = 0.10
_PARTIAL_FILL_THRESHOLD_USDT = 50_000.0


class Position:
    """Pozitie deschisa pe un simbol."""
    def __init__(self, symbol: str, side: OrderSide, qty: float, entry_price: float, pair: str = ""):
        self.symbol       = symbol
        self.side         = side
        self.qty          = qty
        self.entry_price  = entry_price
        self.pair         = pair
        self.realised_pnl = 0.0
        self.opened_at    = datetime.now(timezone.utc)

    def unrealised_pnl(self, current_price: float) -> float:
        mult = 1.0 if self.side == OrderSide.BUY else -1.0
        return mult * (current_price - self.entry_price) * self.qty

    def to_dict(self, current_price: Optional[float] = None) -> dict:
        upnl = self.unrealised_pnl(current_price) if current_price else 0.0
        return {
            "symbol":       self.symbol,
            "side":         self.side.value,
            "qty":          self.qty,
            "entry_price":  self.entry_price,
            "pair":         self.pair,
            "unrealised_pnl": round(upnl, 4),
            "realised_pnl": round(self.realised_pnl, 4),
            "opened_at":    self.opened_at.isoformat(),
        }


class PaperTradingEngine:
    """
    Engine paper trading: simuleaza un exchange Bybit linear futures.
    """

    def __init__(
        self,
        initial_capital:  float = 10_000.0,
        taker_commission: float = _TAKER_COMMISSION,
        max_slippage_bps: float = _SLIPPAGE_BPS_MAX,
        simulate_latency: bool  = True,
        store: Optional[PositionStore] = None,
    ) -> None:
        self.initial_capital  = initial_capital
        self.taker_commission = taker_commission
        self.max_slippage_bps = max_slippage_bps
        self.simulate_latency = simulate_latency
        self._store = store

        self._equity:     float = initial_capital
        self._realised_pnl: float = 0.0
        self._positions:  Dict[str, Position] = {}   # symbol -> Position
        self._trades:     List[dict]  = []
        self._orders:     List[PaperOrder] = []
        self._equity_curve: List[dict] = [
            {"ts": datetime.now(timezone.utc).isoformat(), "equity": initial_capital, "pnl": 0.0}
        ]
        self._lock = asyncio.Lock()
        self._order_counter = 0

        # Load persisted positions on startup
        if self._store:
            self._load_positions()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit_order(
        self,
        symbol:     str,
        side:       str,         # "buy" | "sell"
        qty:        float,
        order_type: str = "market",  # "market" | "limit"
        mid_price:  float = 0.0,
        limit_price: Optional[float] = None,
        pair:       str = "",
        reduce_only: bool = False,
    ) -> PaperOrder:
        """Trimite un ordin simulat. Returneaza PaperOrder cu fill info."""
        async with self._lock:
            self._order_counter += 1
            order = PaperOrder(
                order_id   = f"PAPER-{self._order_counter:06d}-{uuid.uuid4().hex[:6]}",
                symbol     = symbol.upper(),
                side       = OrderSide(side.lower()),
                order_type = OrderType(order_type.lower()),
                qty        = qty,
                price      = limit_price,
                pair       = pair,
                reduce_only= reduce_only,
            )
            self._orders.append(order)

        # Simuleaza latenta
        if self.simulate_latency:
            await asyncio.sleep(random.uniform(0.05, 0.20))

        # Fill logic
        async with self._lock:
            if order.order_type == OrderType.MARKET:
                self._fill_market(order, mid_price)
            else:
                # Limit: fill daca price e favorabil (simplificat)
                fill_price = limit_price or mid_price
                self._fill_limit(order, fill_price, mid_price)

            if order.status == OrderStatus.FILLED:
                self._update_position(order)
                self._save_positions()
                self._record_equity()

        logger.info(f"[PAPER] {order.side.value.upper()} {order.qty} {order.symbol} "
                    f"@ {order.avg_fill_price:.4f} | comm={order.commission:.4f} | "
                    f"status={order.status.value}")
        return order

    def close_position(
        self,
        symbol:        str,
        current_price: float,
        reason:        str = "signal",
    ) -> Optional[dict]:
        """Inchide pozitia pe un simbol (fara latenta, sync)."""
        pos = self._positions.pop(symbol.upper(), None)
        if pos is None:
            return None

        mult = 1.0 if pos.side == OrderSide.BUY else -1.0
        pnl  = mult * (current_price - pos.entry_price) * pos.qty
        comm = current_price * pos.qty * self.taker_commission
        net_pnl = pnl - comm

        self._realised_pnl += net_pnl
        self._equity       += net_pnl

        trade = {
            "symbol":      symbol.upper(),
            "side":        pos.side.value,
            "qty":         pos.qty,
            "entry_price": pos.entry_price,
            "exit_price":  current_price,
            "pnl":         round(net_pnl, 4),
            "commission":  round(comm, 4),
            "reason":      reason,
            "pair":        pos.pair,
            "opened_at":   pos.opened_at.isoformat(),
            "closed_at":   datetime.now(timezone.utc).isoformat(),
        }
        self._trades.append(trade)
        self._save_positions()
        self._record_equity()

        logger.info(f"[PAPER CLOSE] {symbol} pnl={net_pnl:+.4f} USDT | reason={reason}")
        return trade

    def snapshot(self) -> dict:
        """Starea curenta a engine-ului."""
        n_trades  = len(self._trades)
        wins      = [t for t in self._trades if t["pnl"] > 0]
        win_rate  = len(wins) / n_trades if n_trades else 0.0
        return {
            "equity_usdt":   round(self._equity, 4),
            "initial_capital": self.initial_capital,
            "total_pnl":     round(self._realised_pnl, 4),
            "pct_return":    round((self._equity - self.initial_capital) / self.initial_capital, 6),
            "n_trades":      n_trades,
            "win_rate":      round(win_rate, 4),
            "open_positions": len(self._positions),
            "n_orders":      len(self._orders),
        }

    def positions(self, current_prices: Optional[Dict[str, float]] = None) -> List[dict]:
        cp = current_prices or {}
        return [p.to_dict(cp.get(sym)) for sym, p in self._positions.items()]

    def trades(self, limit: int = 100) -> List[dict]:
        return self._trades[-limit:]

    def equity_curve(self) -> List[dict]:
        return self._equity_curve

    def _load_positions(self) -> None:
        """Load previously saved positions from store."""
        if not self._store:
            return
        data = self._store.load_positions()
        for symbol, pos_dict in data.items():
            try:
                side_str = pos_dict.get("side", "buy")
                # Handle both string and enum values
                if isinstance(side_str, str):
                    side = OrderSide(side_str.lower())
                else:
                    side = side_str
                qty = float(pos_dict.get("qty", 0.0))
                entry_price = float(pos_dict.get("entry_price", 0.0))
                pair = str(pos_dict.get("pair", ""))
                if qty > 0 and entry_price > 0:
                    pos = Position(symbol, side, qty, entry_price, pair)
                    pos.realised_pnl = float(pos_dict.get("realised_pnl", 0.0))
                    self._positions[symbol] = pos
                    logger.info(f"[PAPER] Restored position: {symbol} {side.value} {qty} @ {entry_price}")
            except Exception as exc:
                logger.warning(f"[PAPER] Failed to restore position {symbol}: {exc}")

    def _save_positions(self) -> None:
        """Save current positions to store."""
        if not self._store:
            return
        serializable = {}
        for symbol, pos in self._positions.items():
            serializable[symbol] = {
                "symbol": pos.symbol,
                "side": pos.side.value,
                "qty": pos.qty,
                "entry_price": pos.entry_price,
                "pair": pos.pair,
                "realised_pnl": pos.realised_pnl,
                "opened_at": pos.opened_at.isoformat(),
            }
        self._store.save_positions(serializable)

    def reset(self) -> None:
        """Reset complet la starea initiala."""
        self._equity        = self.initial_capital
        self._realised_pnl  = 0.0
        self._positions     = {}
        self._trades        = []
        self._orders        = []
        self._order_counter = 0
        self._equity_curve  = [
            {"ts": datetime.now(timezone.utc).isoformat(), "equity": self.initial_capital, "pnl": 0.0}
        ]
        if self._store:
            self._store.clear()
        logger.info("[PAPER] Engine reset.")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fill_market(self, order: PaperOrder, mid_price: float) -> None:
        if mid_price <= 0:
            order.status = OrderStatus.REJECTED
            return

        # Slippage: buy = price + slip, sell = price - slip
        slip_frac  = random.uniform(0, self.max_slippage_bps) / 10_000.0
        slip_abs   = mid_price * slip_frac
        fill_price = mid_price + slip_abs if order.side == OrderSide.BUY else mid_price - slip_abs

        # Partial fill check
        notional = fill_price * order.qty
        if notional > _PARTIAL_FILL_THRESHOLD_USDT and random.random() < _PARTIAL_FILL_PROB:
            fill_qty = order.qty * random.uniform(0.5, 0.9)
            order.status = OrderStatus.PARTIAL
        else:
            fill_qty = order.qty
            order.status = OrderStatus.FILLED

        commission = fill_price * fill_qty * self.taker_commission
        order.filled_qty     = round(fill_qty, 8)
        order.avg_fill_price = round(fill_price, 6)
        order.commission     = round(commission, 6)
        order.slippage       = round(slip_abs * fill_qty, 6)
        order.filled_at      = datetime.now(timezone.utc)

    def _fill_limit(self, order: PaperOrder, limit_price: float, mid_price: float) -> None:
        # Simplificat: fill daca limit_price e rezonabil vs mid
        tolerance = mid_price * 0.005  # 0.5% tolerance
        if abs(limit_price - mid_price) <= tolerance:
            order.avg_fill_price = round(limit_price, 6)
            order.filled_qty     = order.qty
            order.commission     = round(limit_price * order.qty * (self.taker_commission * 0.7), 6)
            order.status         = OrderStatus.FILLED
            order.filled_at      = datetime.now(timezone.utc)
        else:
            order.status = OrderStatus.PENDING  # asteptare price cross

    def _update_position(self, order: PaperOrder) -> None:
        sym = order.symbol
        if sym in self._positions:
            pos = self._positions[sym]
            if pos.side == order.side:
                # Adauga la pozitie (average in)
                total_qty    = pos.qty + order.filled_qty
                avg_price    = (pos.entry_price * pos.qty + order.avg_fill_price * order.filled_qty) / total_qty
                pos.qty          = total_qty
                pos.entry_price  = avg_price
            else:
                # Inchide partial/total
                if order.filled_qty >= pos.qty:
                    mult    = 1.0 if pos.side == OrderSide.BUY else -1.0
                    pnl     = mult * (order.avg_fill_price - pos.entry_price) * pos.qty
                    net_pnl = pnl - order.commission
                    self._realised_pnl += net_pnl
                    self._equity       += net_pnl
                    self._trades.append({
                        "symbol":      sym, "side": pos.side.value,
                        "qty":         pos.qty, "entry_price": pos.entry_price,
                        "exit_price":  order.avg_fill_price,
                        "pnl":         round(net_pnl, 4), "commission": round(order.commission, 4),
                        "reason":      "order", "pair": pos.pair,
                        "opened_at":   pos.opened_at.isoformat(),
                        "closed_at":   datetime.now(timezone.utc).isoformat(),
                    })
                    del self._positions[sym]
                    self._save_positions()
                else:
                    pos.qty -= order.filled_qty
                    self._save_positions()
        else:
            self._positions[sym] = Position(
                symbol=sym, side=order.side,
                qty=order.filled_qty, entry_price=order.avg_fill_price,
                pair=order.pair,
            )
            self._save_positions()
        # Scade comision din equity
        self._equity -= order.commission

    def _record_equity(self) -> None:
        self._equity_curve.append({
            "ts":     datetime.now(timezone.utc).isoformat(),
            "equity": round(self._equity, 4),
            "pnl":    round(self._realised_pnl, 4),
        })

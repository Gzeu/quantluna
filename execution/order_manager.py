"""
execution/order_manager.py  —  QuantLuna Sprint 4 v2

Responsabilities:
  - Submit pair legs (market or limit postOnly)
  - Dynamic slippage model: base + size_impact, capped
  - Exponential backoff retry on transient exchange errors
  - Idempotent close()
  - FillPair dataclass for dashboard analytics
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import ccxt.async_support as ccxt_async

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Fill:
    symbol: str
    side: str                     # 'buy' | 'sell'
    qty: float
    ref_price: float
    fill_price: float
    slippage_bps: float
    fee_usdt: float
    order_id: str = ""
    order_type: str = "market"
    timestamp_ms: int = 0

    @property
    def cost_usdt(self) -> float:
        return self.fill_price * self.qty


@dataclass
class FillPair:
    leg_y: Fill
    leg_x: Fill
    entry_ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def total_cost_usdt(self) -> float:
        return self.leg_y.cost_usdt + self.leg_x.cost_usdt

    @property
    def net_notional(self) -> float:
        """Signed: positive = long Y / short X."""
        sign_y = 1.0 if self.leg_y.side == "buy" else -1.0
        sign_x = 1.0 if self.leg_x.side == "buy" else -1.0
        return sign_y * self.leg_y.cost_usdt + sign_x * self.leg_x.cost_usdt

    @property
    def execution_lag_ms(self) -> int:
        if self.leg_x.timestamp_ms and self.leg_y.timestamp_ms:
            return abs(self.leg_x.timestamp_ms - self.leg_y.timestamp_ms)
        return 0

    @property
    def total_fee_usdt(self) -> float:
        return self.leg_y.fee_usdt + self.leg_x.fee_usdt


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ExecutionConfig:
    exchange_id: str = "bybit"          # ccxt exchange id
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    paper_mode: bool = False

    # Slippage model
    slippage_bps: float = 3.0           # base slippage
    size_impact_scale: float = 50_000.0 # USDT: full impact at this notional
    max_slippage_bps: float = 15.0      # hard cap

    # Order type
    order_type: str = "market"          # 'market' | 'limit'
    limit_ttl_s: float = 30.0           # limit order TTL before market fallback
    limit_offset_bps: float = 1.0       # limit price offset from mid

    # Fees (maker / taker)
    fee_rate_taker: float = 0.00055     # Bybit perp taker
    fee_rate_maker: float = 0.00020     # Bybit perp maker

    # Retry
    max_retries: int = 3


# ---------------------------------------------------------------------------
# OrderManager
# ---------------------------------------------------------------------------

class OrderManager:
    """
    Async pair execution engine.

    Usage:
        async with OrderManager(config) as om:
            fill_pair = await om.execute_pair(
                sym_y, side_y, qty_y, price_y,
                sym_x, side_x, qty_x, price_x,
            )
    """

    def __init__(self, config: ExecutionConfig):
        self.cfg = config
        self._exchange: Optional[ccxt_async.Exchange] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "OrderManager":
        await self._init_exchange()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def _init_exchange(self):
        cls = getattr(ccxt_async, self.cfg.exchange_id)
        self._exchange = cls({
            "apiKey": self.cfg.api_key,
            "secret": self.cfg.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        if self.cfg.testnet:
            self._exchange.set_sandbox_mode(True)
        logger.info(
            f"OrderManager init: {self.cfg.exchange_id} "
            f"| testnet={self.cfg.testnet} | paper={self.cfg.paper_mode}"
        )

    async def close(self):
        """Idempotent — safe to call multiple times."""
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
            finally:
                self._exchange = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_pair(
        self,
        sym_y: str, side_y: str, qty_y: float, price_y: float,
        sym_x: str, side_x: str, qty_x: float, price_x: float,
    ) -> FillPair:
        """
        Submit both legs concurrently (market) or sequentially (limit).
        Returns FillPair with full fill metadata.
        """
        fill_y, fill_x = await asyncio.gather(
            self._execute_leg_with_retry(sym_y, side_y, qty_y, price_y),
            self._execute_leg_with_retry(sym_x, side_x, qty_x, price_x),
        )
        pair = FillPair(leg_y=fill_y, leg_x=fill_x)
        logger.info(
            f"FillPair | Y={sym_y} {side_y} {qty_y:.4f}@{fill_y.fill_price:.4f} "
            f"| X={sym_x} {side_x} {qty_x:.4f}@{fill_x.fill_price:.4f} "
            f"| lag={pair.execution_lag_ms}ms | fees={pair.total_fee_usdt:.4f} USDT"
        )
        return pair

    # ------------------------------------------------------------------
    # Internal: retry wrapper
    # ------------------------------------------------------------------

    async def _execute_leg_with_retry(
        self,
        symbol: str, side: str, qty: float, ref_price: float,
    ) -> Fill:
        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(self.cfg.max_retries):
            try:
                return await self._execute_leg(symbol, side, qty, ref_price)
            except (
                ccxt_async.NetworkError,
                ccxt_async.ExchangeNotAvailable,
                ccxt_async.RequestTimeout,
            ) as exc:
                last_exc = exc
                if attempt == self.cfg.max_retries - 1:
                    break
                wait = 0.5 * (2 ** attempt)   # 0.5s, 1s, 2s
                logger.warning(
                    f"Retry {attempt + 1}/{self.cfg.max_retries} for {symbol} "
                    f"after {wait:.1f}s: {exc}"
                )
                await asyncio.sleep(wait)
        raise last_exc

    # ------------------------------------------------------------------
    # Internal: single leg execution
    # ------------------------------------------------------------------

    async def _execute_leg(
        self, symbol: str, side: str, qty: float, ref_price: float
    ) -> Fill:
        slippage_bps = self._estimate_slippage_bps(ref_price, side, qty * ref_price)

        if self.cfg.paper_mode:
            return self._paper_fill(symbol, side, qty, ref_price, slippage_bps)

        ts_before = int(time.time() * 1000)

        if self.cfg.order_type == "limit":
            fill = await self._limit_leg(symbol, side, qty, ref_price, slippage_bps)
        else:
            fill = await self._market_leg(symbol, side, qty, ref_price, slippage_bps)

        fill.timestamp_ms = ts_before
        return fill

    async def _market_leg(
        self, symbol: str, side: str, qty: float, ref_price: float, slippage_bps: float
    ) -> Fill:
        order = await self._exchange.create_order(symbol, "market", side, qty)
        fill_price = self._safe_fill_price(order, ref_price, symbol)
        fee = fill_price * qty * self.cfg.fee_rate_taker
        return Fill(
            symbol=symbol, side=side, qty=qty,
            ref_price=ref_price, fill_price=fill_price,
            slippage_bps=slippage_bps, fee_usdt=fee,
            order_id=str(order.get("id", "")),
            order_type="market",
        )

    async def _limit_leg(
        self, symbol: str, side: str, qty: float, ref_price: float, slippage_bps: float
    ) -> Fill:
        limit_price = self._limit_price(ref_price, side)
        order = await self._exchange.create_order(
            symbol, "limit", side, qty, limit_price,
            params={"postOnly": True, "timeInForce": "GTX"},
        )
        order_id = str(order.get("id", ""))

        deadline = time.monotonic() + self.cfg.limit_ttl_s
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            fetched = await self._exchange.fetch_order(order_id, symbol)
            status = fetched.get("status", "")
            if status == "closed":
                fill_price = self._safe_fill_price(fetched, ref_price, symbol)
                fee = fill_price * qty * self.cfg.fee_rate_maker
                return Fill(
                    symbol=symbol, side=side, qty=qty,
                    ref_price=ref_price, fill_price=fill_price,
                    slippage_bps=slippage_bps, fee_usdt=fee,
                    order_id=order_id, order_type="limit",
                )
            if status in ("canceled", "rejected", "expired"):
                break

        # TTL expired or rejected — cancel and fallback to market
        logger.warning(
            f"Limit order {order_id} for {symbol} expired/rejected — "
            f"falling back to market order"
        )
        try:
            await self._exchange.cancel_order(order_id, symbol)
        except Exception:
            pass
        return await self._market_leg(symbol, side, qty, ref_price, slippage_bps)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _estimate_slippage_bps(
        self, ref_price: float, side: str, qty_usdt: float
    ) -> float:
        """Dynamic slippage: base + linear size impact, capped at max."""
        base = self.cfg.slippage_bps
        size_component = (qty_usdt / self.cfg.size_impact_scale) * base
        return min(base + size_component, self.cfg.max_slippage_bps)

    def _limit_price(self, ref_price: float, side: str) -> float:
        offset = ref_price * self.cfg.limit_offset_bps / 10_000
        return ref_price - offset if side == "buy" else ref_price + offset

    def _safe_fill_price(
        self, order: dict, ref_price: float, symbol: str
    ) -> float:
        price = order.get("average") or order.get("price")
        if price is None:
            logger.warning(
                f"Fill price unavailable for {symbol} — falling back to ref_price={ref_price:.6f}. "
                f"Slippage computation may be inaccurate."
            )
            return ref_price
        return float(price)

    def _paper_fill(
        self, symbol: str, side: str, qty: float,
        ref_price: float, slippage_bps: float
    ) -> Fill:
        """Simulate fill with dynamic slippage applied to ref_price."""
        multiplier = 1.0 + slippage_bps / 10_000 if side == "buy" else 1.0 - slippage_bps / 10_000
        fill_price = ref_price * multiplier
        fee = fill_price * qty * self.cfg.fee_rate_taker
        return Fill(
            symbol=symbol, side=side, qty=qty,
            ref_price=ref_price, fill_price=fill_price,
            slippage_bps=slippage_bps, fee_usdt=fee,
            order_id="paper", order_type="paper",
            timestamp_ms=int(time.time() * 1000),
        )

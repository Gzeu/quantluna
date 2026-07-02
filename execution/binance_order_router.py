"""
QuantLuna — BinanceOrderRouter
Sprint 25

Full Binance REST order execution with:
  - python-binance client (sync wrapper, async-compatible via executor)
  - Exponential retry (3 attempts, backoff 1s→22s)
  - Symbol precision / step-size auto-loaded from exchangeInfo
  - MARKET and LIMIT order support
  - Order confirmation polling (up to 10s)
  - Testnet support via BINANCE_TESTNET=true env var
  - Structured OrderReceipt returned on every call

Env vars:
  BINANCE_API_KEY      required
  BINANCE_API_SECRET   required
  BINANCE_TESTNET      true | false (default: false)

Usage:
    from execution.binance_order_router import BinanceOrderRouter, OrderSide
    router = BinanceOrderRouter()
    receipt = router.market_order("BTCUSDT", OrderSide.BUY, qty=0.001)
    print(receipt.status, receipt.filled_qty, receipt.avg_price)
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_API_KEY    = os.getenv("BINANCE_API_KEY",    "")
_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
_TESTNET    = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
_MAX_RETRY  = 3
_RETRY_BASE = 1.0   # seconds
_CONFIRM_TIMEOUT = 10.0  # seconds to poll for fill confirmation


class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"


@dataclass
class OrderReceipt:
    symbol:      str
    order_id:    Optional[int]
    side:        str
    order_type:  str
    requested_qty: float
    filled_qty:  float
    avg_price:   float
    status:      str          # NEW / FILLED / PARTIALLY_FILLED / CANCELED / ERROR
    raw:         Dict[str, Any] = field(default_factory=dict)
    error:       Optional[str] = None
    latency_ms:  float = 0.0


class BinanceOrderRouter:
    """
    Production Binance order router with retry + precision management.

    Parameters
    ----------
    api_key     : Binance API key (default from env)
    api_secret  : Binance API secret (default from env)
    testnet     : use Binance testnet (default from env BINANCE_TESTNET)
    max_retry   : max retry attempts per order (default 3)
    dry_run     : log orders but do NOT send to exchange (default False)
    """

    def __init__(
        self,
        api_key:    str  = _API_KEY,
        api_secret: str  = _API_SECRET,
        testnet:    bool = _TESTNET,
        max_retry:  int  = _MAX_RETRY,
        dry_run:    bool = False,
    ) -> None:
        self.api_key    = api_key
        self.api_secret = api_secret
        self.testnet    = testnet
        self.max_retry  = max_retry
        self.dry_run    = dry_run
        self._client    = None         # lazy init
        self._precision_cache: Dict[str, Dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def market_order(
        self,
        symbol: str,
        side:   OrderSide,
        qty:    float,
    ) -> OrderReceipt:
        """
        Place a MARKET order. Qty is automatically rounded to step size.
        Returns OrderReceipt with fill details.
        """
        qty = self._round_qty(symbol, qty)
        return self._send_order(
            symbol=symbol, side=side,
            order_type=OrderType.MARKET, qty=qty,
        )

    def limit_order(
        self,
        symbol: str,
        side:   OrderSide,
        qty:    float,
        price:  float,
        time_in_force: str = "GTC",
    ) -> OrderReceipt:
        """
        Place a LIMIT order. Price rounded to tick size.
        """
        qty   = self._round_qty(symbol, qty)
        price = self._round_price(symbol, price)
        return self._send_order(
            symbol=symbol, side=side,
            order_type=OrderType.LIMIT, qty=qty,
            price=price, time_in_force=time_in_force,
        )

    def cancel_order(self, symbol: str, order_id: int) -> Dict:
        """Cancel an open order."""
        client = self._get_client()
        return self._retry(lambda: client.cancel_order(symbol=symbol, orderId=order_id))

    def get_balance(self, asset: str = "USDT") -> float:
        """Return free balance for asset."""
        client = self._get_client()
        account = self._retry(lambda: client.get_account())
        for b in account.get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def pair_market_orders(
        self,
        sym_y: str, side_y: OrderSide, qty_y: float,
        sym_x: str, side_x: OrderSide, qty_x: float,
    ) -> tuple[OrderReceipt, OrderReceipt]:
        """
        Place two market orders as a pair (Y leg + X leg).
        Sends both; returns (receipt_y, receipt_x).
        Y leg sent first; X leg sent even if Y fails (best-effort).
        """
        receipt_y = self.market_order(sym_y, side_y, qty_y)
        receipt_x = self.market_order(sym_x, side_x, qty_x)
        logger.info(
            f"Pair orders: {sym_y} {side_y.value} {qty_y} → {receipt_y.status} | "
            f"{sym_x} {side_x.value} {qty_x} → {receipt_x.status}"
        )
        return receipt_y, receipt_x

    # ------------------------------------------------------------------
    # Internal — order dispatch + retry
    # ------------------------------------------------------------------

    def _send_order(
        self,
        symbol:     str,
        side:       OrderSide,
        order_type: OrderType,
        qty:        float,
        price:      Optional[float] = None,
        time_in_force: str = "GTC",
    ) -> OrderReceipt:
        if self.dry_run:
            logger.info(f"[DRY RUN] {side.value} {qty} {symbol} @ {price or 'MARKET'}")
            return OrderReceipt(
                symbol=symbol, order_id=None, side=side.value,
                order_type=order_type.value, requested_qty=qty,
                filled_qty=qty, avg_price=price or 0.0, status="FILLED_DRY",
            )

        client = self._get_client()
        t0 = time.monotonic()

        def _place() -> Dict:
            kwargs: Dict[str, Any] = dict(
                symbol=symbol,
                side=side.value,
                type=order_type.value,
                quantity=qty,
            )
            if order_type == OrderType.LIMIT and price is not None:
                kwargs["price"]         = f"{price:.8f}".rstrip("0").rstrip(".")
                kwargs["timeInForce"]   = time_in_force
            return client.create_order(**kwargs)

        try:
            raw = self._retry(_place)
        except Exception as e:
            logger.error(f"Order failed after {self.max_retry} retries: {e}")
            return OrderReceipt(
                symbol=symbol, order_id=None, side=side.value,
                order_type=order_type.value, requested_qty=qty,
                filled_qty=0.0, avg_price=0.0, status="ERROR", error=str(e),
                latency_ms=round((time.monotonic() - t0) * 1000, 1),
            )

        order_id  = raw.get("orderId")
        filled    = float(raw.get("executedQty", 0))
        avg_price = self._calc_avg_price(raw)
        status    = raw.get("status", "UNKNOWN")

        # Poll for fill if not immediately filled (LIMIT orders)
        if status not in ("FILLED", "CANCELED", "REJECTED") and order_id:
            status, filled, avg_price = self._poll_fill(
                symbol, order_id, filled, avg_price
            )

        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            f"ORDER {side.value} {qty} {symbol}: status={status} "
            f"filled={filled} avg={avg_price:.6f} latency={latency_ms}ms"
        )
        return OrderReceipt(
            symbol=symbol, order_id=order_id, side=side.value,
            order_type=order_type.value, requested_qty=qty,
            filled_qty=filled, avg_price=avg_price, status=status,
            raw=raw, latency_ms=latency_ms,
        )

    def _retry(self, fn, attempt: int = 0) -> Any:
        """Retry fn up to max_retry times with exponential backoff."""
        try:
            return fn()
        except Exception as e:
            if attempt >= self.max_retry - 1:
                raise
            wait = _RETRY_BASE * (2 ** attempt)
            logger.warning(f"Retry {attempt + 1}/{self.max_retry} after {wait}s: {e}")
            time.sleep(wait)
            return self._retry(fn, attempt + 1)

    def _poll_fill(
        self,
        symbol:    str,
        order_id:  int,
        filled:    float,
        avg_price: float,
        timeout:   float = _CONFIRM_TIMEOUT,
    ) -> tuple[str, float, float]:
        """Poll order status until filled or timeout."""
        client   = self._get_client()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                order = client.get_order(symbol=symbol, orderId=order_id)
                status = order.get("status", "UNKNOWN")
                filled = float(order.get("executedQty", filled))
                avg_price = self._calc_avg_price(order) or avg_price
                if status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    return status, filled, avg_price
            except Exception as e:
                logger.warning(f"Poll error: {e}")
            time.sleep(0.5)
        return "TIMEOUT", filled, avg_price

    # ------------------------------------------------------------------
    # Internal — precision
    # ------------------------------------------------------------------

    def _load_symbol_info(self, symbol: str) -> Dict:
        """Load and cache exchangeInfo for symbol."""
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]
        client = self._get_client()
        try:
            info = client.get_symbol_info(symbol)
            if info:
                self._precision_cache[symbol] = info
                return info
        except Exception as e:
            logger.warning(f"Could not load symbol info for {symbol}: {e}")
        return {}

    def _get_filter(self, symbol: str, filter_type: str) -> Dict:
        info = self._load_symbol_info(symbol)
        for f in info.get("filters", []):
            if f.get("filterType") == filter_type:
                return f
        return {}

    def _round_qty(self, symbol: str, qty: float) -> float:
        """Round qty to LOT_SIZE step size."""
        f = self._get_filter(symbol, "LOT_SIZE")
        step = float(f.get("stepSize", "0.00001"))
        if step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(step))))
        return round(math.floor(qty / step) * step, precision)

    def _round_price(self, symbol: str, price: float) -> float:
        """Round price to PRICE_FILTER tick size."""
        f = self._get_filter(symbol, "PRICE_FILTER")
        tick = float(f.get("tickSize", "0.01"))
        if tick <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(tick))))
        return round(math.floor(price / tick) * tick, precision)

    @staticmethod
    def _calc_avg_price(order: Dict) -> float:
        """Compute avg fill price from fills list or cummulativeQuoteQty."""
        fills = order.get("fills", [])
        if fills:
            total_qty   = sum(float(f["qty"])   for f in fills)
            total_quote = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            return total_quote / max(total_qty, 1e-12)
        exec_qty   = float(order.get("executedQty",          0))
        cum_quote  = float(order.get("cummulativeQuoteQty",  0))
        return cum_quote / max(exec_qty, 1e-12)

    # ------------------------------------------------------------------
    # Internal — client
    # ------------------------------------------------------------------

    def _get_client(self):
        """Lazy-init python-binance client."""
        if self._client is not None:
            return self._client
        try:
            from binance.client import Client
        except ImportError as e:
            raise RuntimeError(
                "python-binance not installed. Run: pip install python-binance"
            ) from e
        self._client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet,
        )
        logger.info(f"BinanceOrderRouter: client init (testnet={self.testnet})")
        return self._client

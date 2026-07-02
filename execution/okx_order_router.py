"""
QuantLuna — OKX Order Router (Sprint 16)

Mirrors the interface of `bybit_order_router.py` and `binance_order_router.py`
so OKX can be used as a third execution venue with zero changes to the
strategy / risk layer.

Supports:
  - Spot and SWAP (perpetual) markets
  - Market and limit orders
  - Position query, balance query
  - Testnet via demo trading flag

Usage:
    from execution.okx_order_router import OKXOrderRouter, OKXConfig

    router = OKXOrderRouter(OKXConfig(
        api_key="...", api_secret="...", passphrase="...",
        testnet=True,
    ))
    result = await router.place_market_order("BTC-USDT-SWAP", "buy", 0.01)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import ccxt.async_support as ccxt
    _CCXT_AVAILABLE = True
except ImportError:
    _CCXT_AVAILABLE = False
    ccxt = None


@dataclass
class OKXConfig:
    """Configuration for OKX exchange connection."""
    api_key:    str = ""
    api_secret: str = ""
    passphrase: str = ""  # OKX requires a passphrase
    testnet:    bool = False

    # Trading type: 'spot', 'swap' (perpetual), 'futures'
    instrument_type: str = "swap"

    # Max retries for rate-limit or transient errors
    max_retries: int = 3
    retry_delay: float = 1.0

    # Default leverage for SWAP positions
    default_leverage: int = 1

    # Margin mode: 'cross' or 'isolated'
    margin_mode: str = "cross"

    # Request timeout (seconds)
    timeout_ms: int = 10_000

    # Extra CCXT options
    ccxt_options: Dict[str, Any] = field(default_factory=dict)


class OKXOrderRouter:
    """
    Async OKX order router built on top of CCXT.

    All public methods are coroutines and safe to use in asyncio loops.
    Methods return raw CCXT response dicts for maximum transparency.

    Parameters
    ----------
    cfg : OKXConfig
    """

    def __init__(self, cfg: Optional[OKXConfig] = None) -> None:
        self.cfg = cfg or OKXConfig()
        self._exchange: Optional[Any] = None
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Initialise CCXT OKX exchange object."""
        if not _CCXT_AVAILABLE:
            raise RuntimeError(
                "ccxt package not installed. Run: pip install ccxt"
            )

        cfg = self.cfg
        options: Dict[str, Any] = {
            "defaultType":    cfg.instrument_type,
            "recvWindow":     5000,
            "adjustForTimeDifference": True,
            **cfg.ccxt_options,
        }

        if cfg.testnet:
            options["hostname"] = "www.okx.com"
            options["x-simulated-trading"] = "1"

        self._exchange = ccxt.okx({
            "apiKey":     cfg.api_key,
            "secret":     cfg.api_secret,
            "password":   cfg.passphrase,
            "options":    options,
            "timeout":    cfg.timeout_ms,
            "enableRateLimit": True,
        })

        if cfg.testnet:
            self._exchange.set_sandbox_mode(True)

        self._connected = True
        logger.info(
            f"OKXOrderRouter: connected "
            f"({'testnet' if cfg.testnet else 'mainnet'}, {cfg.instrument_type})"
        )

    async def close(self) -> None:
        """Close CCXT exchange session."""
        if self._exchange is not None:
            await self._exchange.close()
            self._connected = False
            logger.info("OKXOrderRouter: disconnected")

    async def __aenter__(self) -> "OKXOrderRouter":
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Market info
    # ------------------------------------------------------------------

    async def get_ticker(self, symbol: str) -> Dict:
        """Fetch current ticker for a symbol."""
        return await self._retry(lambda: self._exchange.fetch_ticker(symbol))

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict:
        """Fetch level-2 orderbook."""
        return await self._retry(lambda: self._exchange.fetch_order_book(symbol, limit))

    async def get_balance(self) -> Dict:
        """Fetch account balance."""
        return await self._retry(lambda: self._exchange.fetch_balance())

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Fetch open positions. Optionally filter by symbol."""
        params = {}
        if self.cfg.instrument_type == "swap":
            params["instType"] = "SWAP"

        positions = await self._retry(
            lambda: self._exchange.fetch_positions(
                [symbol] if symbol else None, params
            )
        )
        return [p for p in positions if p.get("contracts", 0) and p["contracts"] != 0]

    async def get_funding_rate(self, symbol: str) -> Dict:
        """Fetch current funding rate for a perpetual symbol."""
        return await self._retry(
            lambda: self._exchange.fetch_funding_rate(symbol)
        )

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> Dict:
        """
        Place a market order.

        Parameters
        ----------
        symbol          : e.g. 'BTC-USDT-SWAP' or 'BTC/USDT'
        side            : 'buy' or 'sell'
        qty             : quantity in base asset contracts
        reduce_only     : close-only order flag
        client_order_id : optional client-side order ID
        """
        self._assert_connected()
        params: Dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        if client_order_id:
            params["clOrdId"] = client_order_id

        logger.info(
            f"OKX market order: {side} {qty} {symbol} reduce_only={reduce_only}"
        )
        return await self._retry(
            lambda: self._exchange.create_market_order(symbol, side, qty, params=params)
        )

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> Dict:
        """Place a limit order."""
        self._assert_connected()
        params: Dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        if post_only:
            params["postOnly"] = True
        if client_order_id:
            params["clOrdId"] = client_order_id

        logger.info(
            f"OKX limit order: {side} {qty} @ {price} {symbol}"
        )
        return await self._retry(
            lambda: self._exchange.create_limit_order(symbol, side, qty, price, params=params)
        )

    async def cancel_order(
        self,
        order_id: str,
        symbol: str,
    ) -> Dict:
        """Cancel an open order by ID."""
        self._assert_connected()
        return await self._retry(
            lambda: self._exchange.cancel_order(order_id, symbol)
        )

    async def get_order(self, order_id: str, symbol: str) -> Dict:
        """Fetch order status."""
        self._assert_connected()
        return await self._retry(
            lambda: self._exchange.fetch_order(order_id, symbol)
        )

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """List all open orders."""
        self._assert_connected()
        return await self._retry(
            lambda: self._exchange.fetch_open_orders(symbol)
        )

    async def cancel_all_orders(self, symbol: str) -> List[Dict]:
        """Cancel all open orders for a symbol."""
        self._assert_connected()
        return await self._retry(
            lambda: self._exchange.cancel_all_orders(symbol)
        )

    # ------------------------------------------------------------------
    # Leverage & margin
    # ------------------------------------------------------------------

    async def set_leverage(
        self,
        symbol: str,
        leverage: Optional[int] = None,
        margin_mode: Optional[str] = None,
    ) -> Dict:
        """Set leverage and margin mode for a symbol."""
        self._assert_connected()
        lev  = leverage    or self.cfg.default_leverage
        mode = margin_mode or self.cfg.margin_mode

        await self._retry(
            lambda: self._exchange.set_margin_mode(mode, symbol)
        )
        return await self._retry(
            lambda: self._exchange.set_leverage(lev, symbol)
        )

    # ------------------------------------------------------------------
    # Pair trading helpers
    # ------------------------------------------------------------------

    async def open_pair(
        self,
        sym_y: str,
        sym_x: str,
        qty_y: float,
        qty_x: float,
        side_y: str,
        side_x: str,
    ) -> Dict:
        """
        Place two market orders simultaneously (pairs trade entry).
        Returns dict with both order results.
        """
        self._assert_connected()
        order_y, order_x = await asyncio.gather(
            self.place_market_order(sym_y, side_y, qty_y),
            self.place_market_order(sym_x, side_x, qty_x),
        )
        return {"leg_y": order_y, "leg_x": order_x}

    async def close_pair(
        self,
        sym_y: str,
        sym_x: str,
        qty_y: float,
        qty_x: float,
        side_y: str,
        side_x: str,
    ) -> Dict:
        """Close both legs of a pairs trade."""
        return await self.open_pair(
            sym_y, sym_x, qty_y, qty_x, side_y, side_x
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_connected(self) -> None:
        if not self._connected or self._exchange is None:
            raise RuntimeError(
                "OKXOrderRouter not connected. Call await router.connect() first."
            )

    async def _retry(self, coro_factory, retries: Optional[int] = None) -> Any:
        """Execute a coroutine factory with exponential backoff on rate-limit errors."""
        max_r = retries or self.cfg.max_retries
        for attempt in range(1, max_r + 1):
            try:
                return await coro_factory()
            except Exception as e:  # noqa: BLE001
                err = str(e).lower()
                if any(kw in err for kw in ("ratelimit", "ddos", "too many")):
                    wait = self.cfg.retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"OKX rate-limit hit, retrying in {wait:.1f}s "
                        f"(attempt {attempt}/{max_r})"
                    )
                    await asyncio.sleep(wait)
                elif attempt < max_r:
                    logger.warning(
                        f"OKX order error: {e} (attempt {attempt}/{max_r})"
                    )
                    await asyncio.sleep(self.cfg.retry_delay)
                else:
                    logger.error(f"OKX order failed after {max_r} attempts: {e}")
                    raise
        raise RuntimeError("Unreachable")

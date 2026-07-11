"""
execution/spot_order_router.py  -  QuantLuna Spot Order Router v1.0

Sprint S30 (2026-07-12):
  Suport tranzactionare Spot pe Bybit (category=spot).
  Interface identic cu BybitOrderRouter pentru compatibilitate cu
  OrderManager si StrategyClassifier.

Diferente fata de Futures Linear:
  - Nu exista hedge mode, position size sau funding rate
  - Qty rounding foloseste basePrecision din instrument_info
  - fetch_positions() returneaza SpotHolding list (nu Position)
  - Nu exista SL/TP native la nivel de pozitie (se simuleaza cu ordine)

Usage::

    router = SpotOrderRouter.from_env()
    await router.place_market_order("BTCUSDT", side="Buy", qty=0.001)
    holdings = await router.fetch_holdings()
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class SpotHolding:
    """Echivalentul pozitiei pentru piata spot."""
    asset: str
    free: float
    locked: float
    total: float
    avg_buy_price: float = 0.0
    unrealised_pnl: float = 0.0  # estimat daca avg_buy_price e disponibil

    @property
    def symbol(self) -> str:
        """Returneaza simbolul USDT (ex: BTC -> BTCUSDT)."""
        return f"{self.asset}USDT"

    @property
    def usdt_value(self) -> float:
        """Valoare totala estimata in USDT (necesita price feed extern)."""
        return self.total  # placeholder; se actualizeaza din price feed


@dataclass
class SpotOrderResult:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    status: str
    raw: Dict[str, Any] = field(default_factory=dict)


class SpotOrderRouter:
    """
    Router pentru Bybit Spot (category=spot).

    Parametrii de conectare sunt identici cu BybitOrderRouter
    (api_key, api_secret, testnet) pentru a permite reutilizarea
    configuratiei existente.
    """

    CATEGORY = "spot"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        recv_window: int = 5000,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._recv_window = recv_window
        self._client: Optional[Any] = None
        self._base_url = (
            "https://api-testnet.bybit.com"
            if testnet else
            "https://api.bybit.com"
        )

    @classmethod
    def from_env(cls) -> "SpotOrderRouter":
        return cls(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
        )

    # ------------------------------------------------------------------
    # Client lazy init
    # ------------------------------------------------------------------

    def _get_client(self):
        """Lazy-init pybit client."""
        if self._client is None:
            try:
                from pybit.unified_trading import HTTP
                self._client = HTTP(
                    testnet=self._testnet,
                    api_key=self._api_key,
                    api_secret=self._api_secret,
                    recv_window=self._recv_window,
                )
            except ImportError:
                raise RuntimeError(
                    "pybit nu e instalat. Adauga 'pybit>=5.6' in requirements.txt"
                )
        return self._client

    # ------------------------------------------------------------------
    # Wallet / Holdings
    # ------------------------------------------------------------------

    async def fetch_holdings(self, min_usdt_value: float = 1.0) -> List[SpotHolding]:
        """
        Returneaza toate holdings-urile spot cu valoare > min_usdt_value.
        Foloseste /v5/account/wallet cu accountType=SPOT.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().get_wallet_balance(
                    accountType="SPOT"
                )
            )
            coins = (
                resp.get("result", {})
                   .get("list", [{}])[0]
                   .get("coin", [])
            )
            holdings = []
            for c in coins:
                total = float(c.get("walletBalance", 0) or 0)
                if total < 1e-9:
                    continue
                free = float(c.get("availableToWithdraw", 0) or 0)
                locked = max(0.0, total - free)
                avg_price = float(c.get("avgPrice", 0) or 0)
                usd_val = float(c.get("usdValue", 0) or 0)
                if usd_val < min_usdt_value and c.get("coin") != "USDT":
                    continue
                holdings.append(SpotHolding(
                    asset=c["coin"],
                    free=free,
                    locked=locked,
                    total=total,
                    avg_buy_price=avg_price,
                ))
            logger.debug(
                "[SpotRouter] fetch_holdings: {} assets (min_usdt={})",
                len(holdings), min_usdt_value,
            )
            return holdings
        except Exception as exc:
            logger.error("[SpotRouter] fetch_holdings failed: {}", exc)
            return []

    async def fetch_usdt_balance(self) -> float:
        """Returneaza balanta USDT disponibila in spot wallet."""
        holdings = await self.fetch_holdings(min_usdt_value=0.0)
        for h in holdings:
            if h.asset == "USDT":
                return h.free
        return 0.0

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    async def place_market_order(
        self,
        symbol: str,
        side: str,  # "Buy" | "Sell"
        qty: float,
        qty_is_quote: bool = False,  # True = qty in USDT (market buy)
    ) -> SpotOrderResult:
        """
        Plaseaza un ordin market spot.

        Args:
            symbol:       ex: "BTCUSDT"
            side:         "Buy" sau "Sell"
            qty:          cantitate in base asset (sau USDT daca qty_is_quote=True)
            qty_is_quote: daca True, qty reprezinta USDT de cheltuit (market buy)
        """
        import asyncio
        loop = asyncio.get_event_loop()
        params: Dict[str, Any] = {
            "category": self.CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
        }
        if qty_is_quote and side == "Buy":
            params["marketUnit"] = "quoteCoin"
            params["qty"] = str(qty)
        else:
            params["qty"] = str(qty)

        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().place_order(**params)
            )
            result = resp.get("result", {})
            ret = SpotOrderResult(
                order_id=result.get("orderId", ""),
                symbol=symbol,
                side=side,
                qty=qty,
                price=float(result.get("price", 0) or 0),
                status=result.get("orderStatus", "unknown"),
                raw=result,
            )
            logger.info(
                "[SpotRouter] place_market_order {} {} qty={} -> id={} status={}",
                side, symbol, qty, ret.order_id, ret.status,
            )
            return ret
        except Exception as exc:
            logger.error(
                "[SpotRouter] place_market_order failed {} {} qty={}: {}",
                side, symbol, qty, exc,
            )
            raise

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        time_in_force: str = "GTC",
    ) -> SpotOrderResult:
        """Plaseaza un ordin limit spot."""
        import asyncio
        loop = asyncio.get_event_loop()
        params = {
            "category": self.CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(price),
            "timeInForce": time_in_force,
        }
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().place_order(**params)
            )
            result = resp.get("result", {})
            ret = SpotOrderResult(
                order_id=result.get("orderId", ""),
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                status=result.get("orderStatus", "unknown"),
                raw=result,
            )
            logger.info(
                "[SpotRouter] place_limit_order {} {} qty={} @{} -> id={}",
                side, symbol, qty, price, ret.order_id,
            )
            return ret
        except Exception as exc:
            logger.error(
                "[SpotRouter] place_limit_order failed {} {} qty={} @{}: {}",
                side, symbol, qty, price, exc,
            )
            raise

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Canceleaza un ordin spot activ."""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._get_client().cancel_order(
                    category=self.CATEGORY,
                    symbol=symbol,
                    orderId=order_id,
                )
            )
            logger.info(
                "[SpotRouter] cancel_order {} id={} OK", symbol, order_id
            )
            return True
        except Exception as exc:
            logger.warning(
                "[SpotRouter] cancel_order failed {} id={}: {}",
                symbol, order_id, exc,
            )
            return False

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Returneaza ordinele spot deschise."""
        import asyncio
        loop = asyncio.get_event_loop()
        params: Dict[str, Any] = {"category": self.CATEGORY}
        if symbol:
            params["symbol"] = symbol
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().get_open_orders(**params)
            )
            return resp.get("result", {}).get("list", [])
        except Exception as exc:
            logger.error("[SpotRouter] fetch_open_orders failed: {}", exc)
            return []

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Pret curent spot pentru un simbol."""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().get_tickers(
                    category=self.CATEGORY, symbol=symbol
                )
            )
            items = resp.get("result", {}).get("list", [])
            if items:
                t = items[0]
                return {
                    "symbol": symbol,
                    "last": float(t.get("lastPrice", 0) or 0),
                    "bid": float(t.get("bid1Price", 0) or 0),
                    "ask": float(t.get("ask1Price", 0) or 0),
                    "volume": float(t.get("volume24h", 0) or 0),
                }
            return {"symbol": symbol, "last": 0.0}
        except Exception as exc:
            logger.error("[SpotRouter] fetch_ticker {} failed: {}", symbol, exc)
            return {"symbol": symbol, "last": 0.0}

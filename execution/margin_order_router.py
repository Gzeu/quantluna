"""
execution/margin_order_router.py  -  QuantLuna Margin Order Router v1.0

Sprint S35 (2026-07-12):
  Router pentru Margin Trading pe Bybit (Unified Margin Account).
  Foloseste category=linear sau spot in functie de tipul de margin.

  Tipuri suportate:
    - Cross Margin Spot  (category=spot, marginMode=CROSS_MARGIN)
    - Isolated Margin    (category=spot, marginMode=ISOLATED_MARGIN)
    - Portfolio Margin   (UNIFIED account cu leverage)

  Diferente fata de SpotOrderRouter:
    - Returneaza MarginPosition cu margin_ratio, liq_price, leverage
    - Suporta set_leverage() per simbol
    - Expune fetch_margin_ratio() pentru MarginRiskGuard

Usage::

    router = MarginOrderRouter.from_env(margin_mode="cross")
    await router.set_leverage("BTCUSDT", leverage=3)
    pos = await router.fetch_margin_position("BTCUSDT")
    if pos.margin_ratio < 1.2:
        await router.close_position("BTCUSDT")  # auto-deleverage
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class MarginPosition:
    symbol: str
    side: str           # "Buy" | "Sell"
    size: float
    entry_price: float
    mark_price: float
    liq_price: float
    margin: float       # USDT alocat ca margin
    leverage: float
    unrealised_pnl: float
    margin_ratio: float = 0.0   # margin / maintenance_margin (>1.0 = safe)
    margin_mode: str = "cross"  # "cross" | "isolated"
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_safe(self) -> bool:
        """Pozitia e sigura daca margin_ratio > 1.5 (50% buffer)."""
        return self.margin_ratio >= 1.5

    @property
    def is_danger(self) -> bool:
        """Zona de pericol: margin_ratio intre 1.1 - 1.5."""
        return 1.1 <= self.margin_ratio < 1.5

    @property
    def is_critical(self) -> bool:
        """Zona critica: sub 1.1 -> deleverage automat."""
        return self.margin_ratio < 1.1


class MarginOrderRouter:
    """
    Router pentru Margin Trading Bybit (Unified Account).
    Compatibil cu interfata BybitOrderRouter pentru OrderManager.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        margin_mode: str = "cross",   # "cross" | "isolated" | "portfolio"
        default_leverage: float = 3.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._margin_mode = margin_mode
        self._default_leverage = default_leverage
        self._client = None
        self._leverage_cache: Dict[str, float] = {}

    @classmethod
    def from_env(cls, margin_mode: str = "cross") -> "MarginOrderRouter":
        return cls(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
            margin_mode=margin_mode,
            default_leverage=float(os.getenv("MARGIN_DEFAULT_LEVERAGE", "3")),
        )

    def _get_client(self):
        if self._client is None:
            try:
                from pybit.unified_trading import HTTP
                self._client = HTTP(
                    testnet=self._testnet,
                    api_key=self._api_key,
                    api_secret=self._api_secret,
                )
            except ImportError:
                raise RuntimeError("pybit nu e instalat")
        return self._client

    # ------------------------------------------------------------------
    # Leverage
    # ------------------------------------------------------------------

    async def set_leverage(
        self, symbol: str, leverage: float, category: str = "linear"
    ) -> bool:
        """Seteaza leverage pentru un simbol."""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._get_client().set_leverage(
                    category=category,
                    symbol=symbol,
                    buyLeverage=str(leverage),
                    sellLeverage=str(leverage),
                )
            )
            self._leverage_cache[symbol] = leverage
            logger.info(
                "[MarginRouter] set_leverage {} {}x OK", symbol, leverage
            )
            return True
        except Exception as exc:
            logger.error(
                "[MarginRouter] set_leverage failed {} {}x: {}", symbol, leverage, exc
            )
            return False

    # ------------------------------------------------------------------
    # Position info
    # ------------------------------------------------------------------

    async def fetch_margin_positions(
        self, category: str = "linear"
    ) -> List[MarginPosition]:
        """Returneaza toate pozitiile cu info margin extins."""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().get_positions(
                    category=category, settleCoin="USDT"
                )
            )
            items = resp.get("result", {}).get("list", [])
            positions = []
            for p in items:
                size = float(p.get("size", 0) or 0)
                if size == 0:
                    continue
                entry = float(p.get("avgPrice", 0) or 0)
                mark = float(p.get("markPrice", 0) or 0)
                liq = float(p.get("liqPrice", 0) or 0)
                margin = float(p.get("positionIM", 0) or 0)  # initial margin
                mm = float(p.get("positionMM", 0) or 0)      # maintenance margin
                leverage = float(p.get("leverage", self._default_leverage) or self._default_leverage)
                upnl = float(p.get("unrealisedPnl", 0) or 0)
                margin_ratio = (margin / mm) if mm > 0 else 999.0
                positions.append(MarginPosition(
                    symbol=p["symbol"],
                    side=p.get("side", "Buy"),
                    size=size,
                    entry_price=entry,
                    mark_price=mark,
                    liq_price=liq,
                    margin=margin,
                    leverage=leverage,
                    unrealised_pnl=upnl,
                    margin_ratio=margin_ratio,
                    margin_mode=self._margin_mode,
                    raw=dict(p),
                ))
            return positions
        except Exception as exc:
            logger.error("[MarginRouter] fetch_margin_positions failed: {}", exc)
            return []

    async def fetch_margin_position(
        self, symbol: str, category: str = "linear"
    ) -> Optional[MarginPosition]:
        """Returneaza pozitia margin pentru un simbol specific."""
        positions = await self.fetch_margin_positions(category=category)
        for p in positions:
            if p.symbol == symbol:
                return p
        return None

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        category: str = "linear",
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """Plaseaza ordin market pe contul de margin."""
        import asyncio
        loop = asyncio.get_event_loop()
        params: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
        }
        if reduce_only:
            params["reduceOnly"] = True
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().place_order(**params)
            )
            result = resp.get("result", {})
            logger.info(
                "[MarginRouter] place_market {} {} qty={} reduce_only={} -> id={}",
                side, symbol, qty, reduce_only,
                result.get("orderId", ""),
            )
            return result
        except Exception as exc:
            logger.error(
                "[MarginRouter] place_market failed {} {} qty={}: {}",
                side, symbol, qty, exc,
            )
            raise

    async def close_position(
        self,
        symbol: str,
        category: str = "linear",
    ) -> bool:
        """Inchide complet o pozitie margin (market order reduce_only)."""
        pos = await self.fetch_margin_position(symbol, category=category)
        if pos is None:
            logger.warning(
                "[MarginRouter] close_position: {} nu are pozitie activa", symbol
            )
            return False
        close_side = "Sell" if pos.side == "Buy" else "Buy"
        try:
            await self.place_market_order(
                symbol=symbol,
                side=close_side,
                qty=pos.size,
                category=category,
                reduce_only=True,
            )
            logger.warning(
                "[MarginRouter] close_position {} {} qty={} OK",
                symbol, close_side, pos.size,
            )
            return True
        except Exception as exc:
            logger.error("[MarginRouter] close_position failed {}: {}", symbol, exc)
            return False

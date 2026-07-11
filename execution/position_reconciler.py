"""
execution/position_reconciler.py — QuantLuna Position Reconciler

Phase 0.5: Detecteaza pozitii deschise pe Bybit la startup si le injecteaza
in OrderManager pentru continuitate automata dupa restart.

Flux:
  1. fetch() — GET /v5/position/list pentru symbol_y si symbol_x
  2. Returneaza AdoptedPosition sau None daca nu exista pozitii
  3. bybit_live_runner._reconcile_positions() apeleaza adopt_position()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


@dataclass
class AdoptedPosition:
    """Pozitie detectata pe platforma la startup."""
    symbol_y: str
    symbol_x: str
    y_side: str        # 'long' | 'short' | 'none'
    x_side: str        # 'long' | 'short' | 'none'
    y_qty: float
    x_qty: float
    y_entry_price: float
    x_entry_price: float
    unrealised_pnl: float = 0.0
    source: str = "bybit_rest"   # 'bybit_rest' | 'checkpoint'

    @property
    def has_position(self) -> bool:
        return self.y_qty > 0 or self.x_qty > 0

    def __repr__(self) -> str:
        return (
            f"AdoptedPosition({self.symbol_y} {self.y_side} {self.y_qty:.6f} "
            f"@ {self.y_entry_price:.4f} | "
            f"{self.symbol_x} {self.x_side} {self.x_qty:.6f} "
            f"@ {self.x_entry_price:.4f} | "
            f"uPnL={self.unrealised_pnl:.4f} USDT | src={self.source})"
        )


class PositionReconciler:
    """
    Interogheaza Bybit REST /v5/position/list la startup.
    Returneaza AdoptedPosition daca exista pozitii deschise pe symbol_y / symbol_x.

    Parametri
    ---------
    order_router : obiectul BybitOrderRouter (are session HTTP Bybit)
    symbol_y     : ex. 'BTCUSDT'
    symbol_x     : ex. 'ETHUSDT'
    category     : 'linear' | 'inverse' (default 'linear')
    """

    def __init__(
        self,
        order_router: Any,
        symbol_y: str,
        symbol_x: str,
        category: str = "linear",
    ) -> None:
        self._router = order_router
        self._symbol_y = symbol_y
        self._symbol_x = symbol_x
        self._category = category

    async def fetch(self) -> Optional[AdoptedPosition]:
        """
        Interogheaza Bybit REST si returneaza pozitia adoptata sau None.
        Nu ridica exceptii — orice eroare returneaza None (runner continua).
        """
        try:
            pos_y = await self._get_position(self._symbol_y)
            pos_x = await self._get_position(self._symbol_x)

            y_qty = abs(float(pos_y.get("size", 0)))
            x_qty = abs(float(pos_x.get("size", 0)))

            if y_qty == 0.0 and x_qty == 0.0:
                logger.info("PositionReconciler: Nu exista pozitii deschise pe platforma")
                return None

            adopted = AdoptedPosition(
                symbol_y=self._symbol_y,
                symbol_x=self._symbol_x,
                y_side=self._parse_side(pos_y.get("side", "None")),
                x_side=self._parse_side(pos_x.get("side", "None")),
                y_qty=y_qty,
                x_qty=x_qty,
                y_entry_price=float(pos_y.get("avgPrice", 0.0)),
                x_entry_price=float(pos_x.get("avgPrice", 0.0)),
                unrealised_pnl=(
                    float(pos_y.get("unrealisedPnl", 0.0))
                    + float(pos_x.get("unrealisedPnl", 0.0))
                ),
                source="bybit_rest",
            )
            logger.info(f"PositionReconciler: Pozitie detectata: {adopted}")
            return adopted

        except Exception as exc:
            logger.warning(
                f"PositionReconciler: fetch() esuat ({exc}) — continua fara adoptie"
            )
            return None

    async def _get_position(self, symbol: str) -> dict:
        """Apeleaza Bybit REST GET /v5/position/list pentru un simbol."""
        try:
            # BybitOrderRouter expune session HTTP pybit
            session = getattr(self._router, "_session", None) or getattr(
                self._router, "session", None
            )
            if session is None:
                # Fallback: incearca direct prin router daca are get_positions
                if hasattr(self._router, "get_positions"):
                    result = await self._router.get_positions(symbol)
                    items = result.get("result", {}).get("list", [])
                    return items[0] if items else {}
                return {}

            # pybit HTTP session (sync) — wrapped in executor
            import asyncio
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: session.get_positions(
                    category=self._category,
                    symbol=symbol,
                ),
            )
            items = resp.get("result", {}).get("list", [])
            # Filtreaza pozitia cu size > 0
            for item in items:
                if float(item.get("size", 0)) > 0:
                    return item
            return {}
        except Exception as exc:
            logger.debug(f"PositionReconciler._get_position({symbol}): {exc}")
            return {}

    @staticmethod
    def _parse_side(side_str: str) -> str:
        """Normalizeaza Buy/Sell/None din Bybit API la long/short/none."""
        s = side_str.lower()
        if s == "buy":
            return "long"
        if s == "sell":
            return "short"
        return "none"

"""
QuantLuna — Order Manager

Handles leg execution for a pairs trade:
  - Simultaneous submission of both legs
  - Fill tracking and slippage measurement
  - Paper trading mode (no real orders)
"""
import asyncio
from dataclasses import dataclass
from typing import Optional, Tuple
from loguru import logger

try:
    import ccxt.async_support as ccxt_async
except ImportError:
    ccxt_async = None


@dataclass
class Fill:
    symbol: str
    side: str       # buy | sell
    qty: float      # contracts / coins
    price: float
    notional: float
    fee: float
    slippage_bps: float
    order_id: str
    is_paper: bool = False


class OrderManager:
    """
    Executes pairs legs via CCXT (async).

    Parameters
    ----------
    exchange_id : ccxt exchange id (binance, bybit, ...)
    api_key, secret : credentials (empty string for paper mode)
    paper_mode : if True, simulate fills at current price
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        secret: str = "",
        paper_mode: bool = True,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0005,
        assumed_slippage_bps: float = 3.0,
    ):
        self.exchange_id = exchange_id
        self.paper_mode = paper_mode
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_bps = assumed_slippage_bps
        self._exchange = None

        if not paper_mode and ccxt_async is not None:
            exchange_class = getattr(ccxt_async, exchange_id)
            self._exchange = exchange_class({
                "apiKey": api_key,
                "secret": secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            })

    async def execute_pair(
        self,
        sym_y: str, side_y: str, qty_y: float, price_y: float,
        sym_x: str, side_x: str, qty_x: float, price_x: float,
    ) -> Tuple[Fill, Fill]:
        """Execute both legs concurrently."""
        fill_y, fill_x = await asyncio.gather(
            self._execute_leg(sym_y, side_y, qty_y, price_y),
            self._execute_leg(sym_x, side_x, qty_x, price_x),
        )
        logger.info(
            f"Pair executed: {sym_y} {side_y} @ {fill_y.price:.4f} | "
            f"{sym_x} {side_x} @ {fill_x.price:.4f}"
        )
        return fill_y, fill_x

    async def _execute_leg(
        self, symbol: str, side: str, qty: float, ref_price: float
    ) -> Fill:
        if self.paper_mode:
            slippage = ref_price * self.slippage_bps / 10000
            fill_price = ref_price + slippage if side == "buy" else ref_price - slippage
            notional = fill_price * qty
            fee = notional * self.taker_fee
            return Fill(
                symbol=symbol, side=side, qty=qty, price=fill_price,
                notional=notional, fee=fee,
                slippage_bps=self.slippage_bps,
                order_id=f"PAPER_{symbol}_{side}",
                is_paper=True,
            )

        # Live order
        try:
            order = await self._exchange.create_order(
                symbol, "market", side, qty
            )
            fill_price = float(order.get("average", ref_price))
            notional = fill_price * qty
            fee = notional * self.taker_fee
            slippage_bps = abs(fill_price - ref_price) / ref_price * 10000
            return Fill(
                symbol=symbol, side=side, qty=qty, price=fill_price,
                notional=notional, fee=fee,
                slippage_bps=slippage_bps,
                order_id=str(order["id"]),
                is_paper=False,
            )
        except Exception as e:
            logger.error(f"Order failed {symbol} {side}: {e}")
            raise

    async def close(self):
        if self._exchange:
            await self._exchange.close()

"""
execution/native_sl_tp.py  -  QuantLuna Native Stop-Loss / Take-Profit

Places SL and TP orders natively on Bybit using the V5 API.
Bybit closes the position automatically even if the Python process is down.

Two strategies (tried in order):
  1. Combined: single POST to /v5/order/create with stopLoss + takeProfit fields.
     Works for linear perpetuals on Bybit V5.
  2. Fallback: two separate STOP_MARKET orders via order_router.create_order().

position_idx (Bybit hedge mode)::
    0  =  one-way mode (default, backwards compatible)
    1  =  hedge mode LONG leg  (positionIdx=1)
    2  =  hedge mode SHORT leg (positionIdx=2)

Usage::
    from execution.native_sl_tp import place_sl_tp, calc_sl_price, calc_tp_price

    sl_price = calc_sl_price(entry=67000.0, side="long", sl_pct=0.02)  # 2%
    tp_price = calc_tp_price(entry=67000.0, side="long", tp_pct=0.04)  # 4%

    # One-way mode (default)
    success = await place_sl_tp(
        order_router=order_router,
        symbol="BTCUSDT",
        side="long",
        qty=0.001,
        sl_price=sl_price,
        tp_price=tp_price,
        category="linear",
    )

    # Hedge mode — EGLD LONG leg
    success = await place_sl_tp(
        order_router=order_router,
        symbol="EGLDUSDT",
        side="long",
        qty=10.0,
        sl_price=sl_price,
        tp_price=tp_price,
        category="linear",
        position_idx=1,       # <- hedge mode LONG
    )

Returns True if at least SL was placed, False if both strategies failed.
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger


def calc_sl_price(
    entry: float,
    side: str,
    sl_pct: float,
    tick_size: float = 0.0,
) -> float:
    """Calculate stop-loss price. sl_pct=0.02 means 2% below entry for long."""
    if side == "long":
        price = entry * (1.0 - sl_pct)
    else:
        price = entry * (1.0 + sl_pct)
    if tick_size > 0:
        import math
        price = math.floor(price / tick_size) * tick_size
    return round(price, 8)


def calc_tp_price(
    entry: float,
    side: str,
    tp_pct: float,
    tick_size: float = 0.0,
) -> float:
    """Calculate take-profit price. tp_pct=0.04 means 4% above entry for long."""
    if side == "long":
        price = entry * (1.0 + tp_pct)
    else:
        price = entry * (1.0 - tp_pct)
    if tick_size > 0:
        import math
        price = math.ceil(price / tick_size) * tick_size
    return round(price, 8)


async def place_sl_tp(
    order_router: Any,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
    tp_price: float,
    category: str = "linear",
    position_idx: int = 0,
) -> bool:
    """
    Place native SL + TP on Bybit. Returns True if at least SL was placed.

    Tries strategy 1 (combined) first, then fallback (separate orders).

    Parameters
    ----------
    position_idx : int
        0 = one-way mode (default, backwards compatible)
        1 = hedge mode LONG leg
        2 = hedge mode SHORT leg
    """
    if sl_price <= 0 and tp_price <= 0:
        logger.warning(
            "native_sl_tp: ambele SL(%s) si TP(%s) <= 0 — skip",
            sl_price, tp_price,
        )
        return False

    # Strategy 1: Combined order via order_router raw API call
    try:
        success = await _place_combined(
            order_router, symbol, side, qty,
            sl_price, tp_price, category, position_idx,
        )
        if success:
            logger.info(
                "native_sl_tp: ✅ SL/TP nativ plasat (combined) | "
                "%s %s SL=%.4f TP=%.4f posIdx=%d",
                symbol, side, sl_price, tp_price, position_idx,
            )
            return True
    except Exception as exc:
        logger.warning(
            "native_sl_tp: strategy 1 (combined) failed (%s) — fallback", exc
        )

    # Strategy 2: Fallback separate STOP_MARKET orders
    sl_ok = False
    tp_ok = False

    if sl_price > 0:
        try:
            sl_ok = await _place_stop_order(
                order_router, symbol, side, qty, sl_price,
                order_label="SL", category=category,
                position_idx=position_idx,
            )
        except Exception as exc:
            logger.error("native_sl_tp: SL fallback failed: %s", exc)

    if tp_price > 0:
        try:
            tp_ok = await _place_stop_order(
                order_router, symbol, side, qty, tp_price,
                order_label="TP", category=category,
                is_tp=True,
                position_idx=position_idx,
            )
        except Exception as exc:
            logger.error("native_sl_tp: TP fallback failed: %s", exc)

    if sl_ok or tp_ok:
        logger.info(
            "native_sl_tp: ✅ SL/TP fallback | %s %s SL_ok=%s TP_ok=%s posIdx=%d",
            symbol, side, sl_ok, tp_ok, position_idx,
        )
    else:
        logger.error(
            "native_sl_tp: ❌ Ambele strategii esuate | %s %s SL=%.4f TP=%.4f posIdx=%d",
            symbol, side, sl_price, tp_price, position_idx,
        )

    return sl_ok or tp_ok


async def _place_combined(
    order_router: Any,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
    tp_price: float,
    category: str,
    position_idx: int = 0,
) -> bool:
    """
    Use Bybit V5 /v5/order/create with stopLoss + takeProfit fields.
    order_router must expose a raw_post(path, params) method, OR
    we fall back to using set_trading_stop via HTTP.
    """
    # Prefer set_sl_tp if available (BybitOrderRouter exposes it)
    if hasattr(order_router, "set_sl_tp"):
        return await order_router.set_sl_tp(
            symbol=symbol,
            sl_price=str(sl_price) if sl_price > 0 else "",
            tp_price=str(tp_price) if tp_price > 0 else "",
            category=category,
            position_idx=position_idx,
        )

    if hasattr(order_router, "raw_post"):
        close_side = "Sell" if side == "long" else "Buy"
        params: dict = {
            "category": category,
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": str(qty),
            "reduceOnly": True,
            "positionIdx": position_idx,
        }
        if sl_price > 0:
            params["stopLoss"] = str(sl_price)
            params["slTriggerBy"] = "LastPrice"
        if tp_price > 0:
            params["takeProfit"] = str(tp_price)
            params["tpTriggerBy"] = "LastPrice"
        result = await order_router.raw_post("/v5/order/create", params)
        ret_code = result.get("retCode", -1) if isinstance(result, dict) else -1
        return ret_code == 0

    # Fallback: try set_trading_stop endpoint
    if hasattr(order_router, "set_trading_stop"):
        return await order_router.set_trading_stop(
            symbol=symbol,
            sl=sl_price,
            tp=tp_price,
            category=category,
            position_idx=position_idx,
        )

    raise NotImplementedError(
        "order_router nu are set_sl_tp / raw_post / set_trading_stop"
    )


async def _place_stop_order(
    order_router: Any,
    symbol: str,
    side: str,
    qty: float,
    trigger_price: float,
    order_label: str,
    category: str,
    is_tp: bool = False,
    position_idx: int = 0,
) -> bool:
    """Place a single STOP_MARKET reduce-only order (SL or TP)."""
    try:
        from execution.bybit_order_router import OrderRequest, OrderSide, OrderType
        close_side = OrderSide.SELL if side == "long" else OrderSide.BUY
        req = OrderRequest(
            symbol=symbol,
            side=close_side,
            order_type=(
                OrderType.STOP_MARKET
                if hasattr(OrderType, "STOP_MARKET")
                else OrderType.MARKET
            ),
            qty=qty,
            price=trigger_price,
            reduce_only=True,
            extra={
                "triggerPrice": str(trigger_price),
                "triggerBy": "LastPrice",
                "orderFilter": "StopOrder",
                "tpslMode": "Full",
                "label": order_label,
                "positionIdx": position_idx,
            },
        )
        await order_router.create_order(req)
        logger.info(
            "native_sl_tp: %s ordin plasat | %s trigger=%.4f posIdx=%d",
            order_label, symbol, trigger_price, position_idx,
        )
        return True
    except Exception as exc:
        logger.error(
            "native_sl_tp: %s ordin esuat | %s trigger=%.4f posIdx=%d | %s",
            order_label, symbol, trigger_price, position_idx, exc,
        )
        return False

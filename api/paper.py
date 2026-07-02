"""
QuantLuna — Paper Trading API
Sprint 30

Endpoints:
  POST   /paper/order       — submit market/limit order simulat
  GET    /paper/positions   — pozitii deschise cu unrealised PnL
  GET    /paper/trades      — trade log (ultimele N)
  GET    /paper/equity      — equity curve complet
  GET    /paper/snapshot    — stare curenta (equity, PnL, win rate)
  DELETE /paper/reset       — reset complet la capital initial
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from execution.paper_engine import PaperTradingEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/paper", tags=["paper"])

# Instanta globala (setat la startup din api/main.py)
_engine: Optional[PaperTradingEngine] = None


def get_engine() -> PaperTradingEngine:
    global _engine
    if _engine is None:
        import os
        capital = float(os.getenv("INITIAL_CAPITAL_USD", "10000"))
        _engine = PaperTradingEngine(initial_capital=capital)
    return _engine


class OrderRequest(BaseModel):
    symbol:      str   = Field(..., example="BTCUSDT")
    side:        str   = Field(..., example="buy",    description="buy | sell")
    qty:         float = Field(..., gt=0, example=0.01)
    order_type:  str   = Field("market", example="market", description="market | limit")
    mid_price:   float = Field(..., gt=0, example=65000.0)
    limit_price: Optional[float] = Field(None, example=64800.0)
    pair:        str   = Field("", example="BTC/ETH")
    reduce_only: bool  = Field(False)


@router.post("/order")
async def submit_order(req: OrderRequest):
    """
    POST /paper/order
    Trimite un ordin simulat. Returneaza fill info complet.
    Simuleaza slippage (max 3bps), comision Bybit 0.055%, latenta 50-200ms.
    """
    engine = get_engine()
    order  = await engine.submit_order(
        symbol      = req.symbol,
        side        = req.side,
        qty         = req.qty,
        order_type  = req.order_type,
        mid_price   = req.mid_price,
        limit_price = req.limit_price,
        pair        = req.pair,
        reduce_only = req.reduce_only,
    )
    return order.to_dict()


@router.get("/positions")
def get_positions(
    symbol: Optional[str] = Query(None, description="Filtreaza dupa simbol")
):
    """
    GET /paper/positions
    Returneaza toate pozitiile deschise cu unrealised PnL estimat.
    """
    engine = get_engine()
    positions = engine.positions()
    if symbol:
        positions = [p for p in positions if p["symbol"] == symbol.upper()]
    return {"positions": positions, "count": len(positions)}


@router.get("/trades")
def get_trades(
    limit: int = Query(50, ge=1, le=500, description="Numarul de trades returnate")
):
    """
    GET /paper/trades?limit=50
    Trade log complet (ultimele N trades).
    """
    engine = get_engine()
    trades = engine.trades(limit=limit)
    return {"trades": trades, "count": len(trades)}


@router.get("/equity")
def get_equity_curve():
    """
    GET /paper/equity
    Equity curve complet de la start engine.
    """
    engine = get_engine()
    return {"equity_curve": engine.equity_curve()}


@router.get("/snapshot")
def get_snapshot():
    """
    GET /paper/snapshot
    Starea curenta a engine-ului: equity, PnL, win rate, trades.
    """
    return get_engine().snapshot()


@router.delete("/reset")
def reset_engine():
    """
    DELETE /paper/reset
    Reset complet: equity -> initial_capital, sterge toate pozitiile si trade log.
    """
    get_engine().reset()
    return {"status": "reset", "initial_capital": get_engine().initial_capital}

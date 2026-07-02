"""
QuantLuna — ExchangeFactory
Sprint 27

Factory pentru order routers — returnează BinanceOrderRouter
sau BybitOrderRouter bazat pe env var EXCHANGE.

Usage:
    from execution.exchange_factory import get_order_router, get_ws_feed

    router = get_order_router()   # auto-detect din EXCHANGE env
    feed   = get_ws_feed(symbol="BTCUSDT", interval="1", on_bar=cb)

Env vars:
  EXCHANGE          binance | bybit (default: bybit)
  EXCHANGE_MODE     paper | dry | live (default: paper)
  BYBIT_API_KEY     Bybit API key
  BYBIT_API_SECRET  Bybit API secret
  BYBIT_TESTNET     true | false
  BYBIT_CATEGORY    spot | linear | inverse
  BINANCE_API_KEY   Binance API key (dacă EXCHANGE=binance)
  BINANCE_API_SECRET Binance API secret
"""
from __future__ import annotations

import os
from typing import Callable, Optional

_EXCHANGE = os.getenv("EXCHANGE", "bybit").lower()


def get_order_router(
    exchange: Optional[str] = None,
    mode:     Optional[str] = None,
):
    """
    Factory: returnează order router configurat din env.
    exchange: "bybit" | "binance" | None (auto-detect din EXCHANGE env)
    mode:     "paper" | "dry" | "live" | None (auto-detect din EXCHANGE_MODE env)
    """
    exch = (exchange or _EXCHANGE).lower()
    m    = mode or os.getenv("EXCHANGE_MODE", "paper")

    if exch == "bybit":
        from execution.bybit_order_router import BybitOrderRouter
        return BybitOrderRouter(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
            category=os.getenv("BYBIT_CATEGORY", "linear"),
            mode=m,
        )
    elif exch == "binance":
        from execution.binance_order_router import BinanceOrderRouter
        return BinanceOrderRouter(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
            mode=m,
        )
    else:
        raise ValueError(f"Unknown exchange: '{exch}'. Use 'bybit' or 'binance'.")


def get_ws_feed(
    symbol:       str,
    interval:     str = "1",
    on_bar:       Optional[Callable] = None,
    on_orderbook: Optional[Callable] = None,
    exchange:     Optional[str] = None,
):
    """
    Factory: returnează WS feed pentru exchange.
    """
    exch = (exchange or _EXCHANGE).lower()

    if exch == "bybit":
        from execution.bybit_ws_feed import BybitWsFeed
        return BybitWsFeed(
            symbol=symbol, interval=interval,
            on_bar=on_bar, on_orderbook=on_orderbook,
        )
    elif exch == "binance":
        # BinanceWsFeed existent din Sprint-uri anterioare
        from execution.ws_feed import BinanceWsFeed
        return BinanceWsFeed(symbol=symbol, interval=interval, on_bar=on_bar)
    else:
        raise ValueError(f"Unknown exchange: '{exch}'")

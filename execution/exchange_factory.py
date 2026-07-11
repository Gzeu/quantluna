"""
QuantLuna — ExchangeFactory
Sprint 27 + S20-fix

Factory pentru order routers și WS feeds.

Fix S20:
  - get_dual_ws_feed(symbol_y, symbol_x, interval) → BybitWsBarsAdapter
    Returnează un feed dual-symbol sincronizat cu get_bar() / stream_bars()
    compatibil cu _run_loop din BybitLiveRunner
  - get_ws_feed() extins: dacă symbol_x e furnizat → get_dual_ws_feed()
  - get_order_router() extins cu api_key/api_secret/testnet/dry_run params
    pentru compatibilitate cu _build_exchange_via_factory din runner

Usage:
    from execution.exchange_factory import get_order_router, get_dual_ws_feed

    router = get_order_router(api_key=..., api_secret=..., testnet=False, dry_run=True)
    feed   = get_dual_ws_feed(symbol_y="BTCUSDT", symbol_x="ETHUSDT", interval="5")
    bar    = await feed.get_bar()   # BarData(symbol_y, symbol_x, price_y, price_x, ts)

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
    exchange:   Optional[str]  = None,
    mode:       Optional[str]  = None,
    # S20-fix: parametri directi pentru compatibilitate cu _build_exchange_via_factory
    api_key:    Optional[str]  = None,
    api_secret: Optional[str]  = None,
    testnet:    Optional[bool] = None,
    dry_run:    Optional[bool] = None,
):
    """
    Factory: returnează order router configurat din env sau parametri direcţi.
    exchange: "bybit" | "binance" | None (auto-detect din EXCHANGE env)
    mode:     "paper" | "dry" | "live" | None (auto-detect din EXCHANGE_MODE env)
    """
    exch = (exchange or _EXCHANGE).lower()
    m    = mode or os.getenv("EXCHANGE_MODE", "paper")
    if dry_run is True:
        m = "paper"
    elif dry_run is False and m == "paper":
        m = "live"

    if exch == "bybit":
        from execution.bybit_order_router import BybitOrderRouter
        return BybitOrderRouter(
            api_key    = api_key    or os.getenv("BYBIT_API_KEY",    ""),
            api_secret = api_secret or os.getenv("BYBIT_API_SECRET", ""),
            testnet    = testnet    if testnet is not None else (os.getenv("BYBIT_TESTNET", "false").lower() == "true"),
            category   = os.getenv("BYBIT_CATEGORY", "linear"),
            mode       = m,
        )
    elif exch == "binance":
        from execution.binance_order_router import BinanceOrderRouter
        return BinanceOrderRouter(
            api_key    = api_key    or os.getenv("BINANCE_API_KEY",    ""),
            api_secret = api_secret or os.getenv("BINANCE_API_SECRET", ""),
            mode       = m,
        )
    else:
        raise ValueError(f"Unknown exchange: '{exch}'. Use 'bybit' or 'binance'.")


def get_dual_ws_feed(
    symbol_y:  str,
    symbol_x:  str,
    interval:  str | int = "5",
    exchange:  Optional[str] = None,
    testnet:   Optional[bool] = None,
    category:  Optional[str] = None,
):
    """
    Factory: returnează BybitWsBarsAdapter sincronizat pentru două simboluri.

    Acesta este feed-ul corect pentru BybitLiveRunner care tranzacţionează
    perechi (BTCUSDT / ETHUSDT): emite BarData cu price_y + price_x.

    Returns
    -------
    BybitWsBarsAdapter cu get_bar() / stream_bars() funcionale.
    """
    exch = (exchange or _EXCHANGE).lower()
    tn   = testnet if testnet is not None else (os.getenv("BYBIT_TESTNET", "false").lower() == "true")
    cat  = category or os.getenv("BYBIT_CATEGORY", "linear")
    ivl  = str(interval)

    if exch == "bybit":
        from execution.bybit_ws_feed import BybitWsFeed
        from execution.bybit_ws_bars import BybitWsBarsAdapter

        feed_y = BybitWsFeed(
            symbol   = symbol_y,
            interval = ivl,
            testnet  = tn,
            category = cat,
        )
        feed_x = BybitWsFeed(
            symbol   = symbol_x,
            interval = ivl,
            testnet  = tn,
            category = cat,
        )
        return BybitWsBarsAdapter(
            ws_feed   = feed_y,
            ws_feed_x = feed_x,
            interval  = ivl,
            symbol_y  = symbol_y,
            symbol_x  = symbol_x,
        )
    else:
        raise ValueError(f"get_dual_ws_feed: exchange '{exch}' not supported yet")


def get_ws_feed(
    symbol:       str,
    interval:     str | int = "1",
    on_bar:       Optional[Callable] = None,
    on_orderbook: Optional[Callable] = None,
    exchange:     Optional[str] = None,
    # S20-fix: parametri opţionali pentru compatibilitate cu runner
    testnet:      Optional[bool] = None,
    symbol_x:     Optional[str] = None,
):
    """
    Factory: returnează WS feed pentru exchange.

    Dacă symbol_x este furnizat, returnează automat un BybitWsBarsAdapter
    dual-symbol în loc de un BybitWsFeed single-symbol.
    """
    if symbol_x:
        return get_dual_ws_feed(
            symbol_y = symbol,
            symbol_x = symbol_x,
            interval = interval,
            exchange = exchange,
            testnet  = testnet,
        )

    exch = (exchange or _EXCHANGE).lower()

    if exch == "bybit":
        from execution.bybit_ws_feed import BybitWsFeed
        return BybitWsFeed(
            symbol   = symbol,
            interval = str(interval),
            on_bar   = on_bar,
            on_orderbook = on_orderbook,
            testnet  = testnet if testnet is not None else False,
        )
    elif exch == "binance":
        from execution.ws_feed import BinanceWsFeed
        return BinanceWsFeed(symbol=symbol, interval=str(interval), on_bar=on_bar)
    else:
        raise ValueError(f"Unknown exchange: '{exch}'")


# --- Clasele ExchangeFactory (pentru compatibilitate backward) ---
class ExchangeFactory:
    """Backward-compatible wrapper în jurul funcţiilor factory."""

    @staticmethod
    def get_order_router(**kwargs):
        return get_order_router(**kwargs)

    @staticmethod
    def get_ws_feed(**kwargs):
        return get_ws_feed(**kwargs)

    @staticmethod
    def get_dual_ws_feed(**kwargs):
        return get_dual_ws_feed(**kwargs)

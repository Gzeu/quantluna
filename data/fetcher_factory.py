"""
QuantLuna — FetcherFactory
Sprint 28

Factory pentru Historical Fetcher — returneaza BybitHistoricalFetcher
sau BinanceHistoricalFetcher bazat pe EXCHANGE env var.

CacheStore il foloseste automat, fara modificare de cod.

Usage:
    from data.fetcher_factory import get_fetcher
    fetcher = get_fetcher()              # auto din EXCHANGE env
    fetcher = get_fetcher("bybit")       # explicit Bybit
    fetcher = get_fetcher("binance")     # explicit Binance

    df = fetcher.fetch("BTCUSDT", interval="60", start="2024-01-01")

Interval mapping automat:
    Binance style -> Bybit style (si invers) e gestionat intern de fiecare fetcher.
    Recomandat: foloseste aliasuri universale: "1h","4h","1d" — ambii fetcheri le accepta.
"""
from __future__ import annotations

import os
from typing import Optional

_EXCHANGE = os.getenv("EXCHANGE", "bybit").lower()


def get_fetcher(
    exchange:  Optional[str] = None,
    cache_dir: Optional[str] = None,
    ttl_hours: float = 24.0,
    **kwargs,
):
    """
    Factory: returneaza fetcher configurat.
    exchange: "bybit" | "binance" | None (auto din EXCHANGE env)
    """
    exch = (exchange or _EXCHANGE).lower()
    kw   = {"ttl_hours": ttl_hours}
    if cache_dir:
        kw["cache_dir"] = cache_dir

    if exch == "bybit":
        from data.bybit_fetcher import BybitHistoricalFetcher
        return BybitHistoricalFetcher(**kw, **kwargs)
    elif exch == "binance":
        from data.historical_fetcher import BinanceHistoricalFetcher
        return BinanceHistoricalFetcher(**kw, **kwargs)
    else:
        raise ValueError(f"Unknown exchange for fetcher: '{exch}'. Use 'bybit' or 'binance'.")


def get_fetcher_for_optimizer(
    sym_y:    str,
    sym_x:    str,
    interval: str  = "60",
    start:    Optional[str] = None,
    end:      Optional[str] = None,
    exchange: Optional[str] = None,
    force_refresh: bool = False,
):
    """
    Shortcut: fetch aligned pair + returneaza (y_series, x_series).
    Direct input pentru WalkForwardOptimizer.run(y=y, x=x).
    """
    fetcher = get_fetcher(exchange=exchange)
    return fetcher.fetch_pair(
        sym_y=sym_y, sym_x=sym_x,
        interval=interval, start=start, end=end,
        force_refresh=force_refresh,
    )

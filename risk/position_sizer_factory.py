"""
QuantLuna — PositionSizerFactory
Sprint 28

Factory: returneaza BybitPositionSizer sau sizer-ul existent
bazat pe EXCHANGE env var.

Usage:
    from risk.position_sizer_factory import get_position_sizer
    sizer = get_position_sizer()            # auto din EXCHANGE env
    sizer = get_position_sizer("bybit")     # explicit

    result = sizer.calculate(SizingParams(...))
"""
from __future__ import annotations

import os
from typing import Optional

_EXCHANGE = os.getenv("EXCHANGE", "bybit").lower()


def get_position_sizer(
    exchange:         Optional[str]   = None,
    capital_usdt:     float = 10_000.0,
    max_leverage:     float = 3.0,
    kelly_fraction:   str   = "half",
    max_position_pct: float = 0.25,
    fixed_fraction:   float = 0.02,
    **kwargs,
):
    """
    Factory pentru PositionSizer.
    exchange: "bybit" | "binance" | None (auto din EXCHANGE env)
    """
    exch = (exchange or _EXCHANGE).lower()

    if exch in ("bybit", "binance"):  # ambele suporta same sizer pentru USDT contracts
        from risk.bybit_position_sizer import BybitPositionSizer
        return BybitPositionSizer(
            capital_usdt=capital_usdt,
            max_leverage=max_leverage,
            kelly_fraction=kelly_fraction,
            max_position_pct=max_position_pct,
            fixed_fraction=fixed_fraction,
            **kwargs,
        )
    raise ValueError(f"Unknown exchange: '{exch}'")

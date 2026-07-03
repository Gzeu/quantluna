"""
risk/dynamic_stop.py — stop loss dinamic bazat pe ATR si regim de volatilitate.

Calculeaza stop loss si take profit adaptat la volatilitatea curenta.
Suporta trailing stop si breakeven move.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StopLevels:
    entry: float
    stop_loss: float
    take_profit: float
    atr: float
    risk_usdt: float
    reward_usdt: float
    rr_ratio: float
    direction: str


class DynamicStop:
    """
    Stop loss dinamic bazat pe ATR cu trailing si breakeven.

    Usage::

        ds = DynamicStop(atr_multiplier=2.0, rr_ratio=2.0)
        levels = ds.calculate(entry=100.0, atr=1.5, direction="LONG", size_usdt=500.0)
        print(levels.stop_loss, levels.take_profit)
    """

    def __init__(
        self,
        atr_multiplier: float = 2.0,
        rr_ratio: float = 2.0,
        breakeven_atr: float = 1.0,
        min_stop_pct: float = 0.005,
    ) -> None:
        self._atr_mult = atr_multiplier
        self._rr = rr_ratio
        self._be_atr = breakeven_atr
        self._min_stop_pct = min_stop_pct

    def calculate(
        self,
        entry: float,
        atr: float,
        direction: str,
        size_usdt: float = 0.0,
    ) -> StopLevels:
        stop_dist = max(atr * self._atr_mult, entry * self._min_stop_pct)
        tp_dist = stop_dist * self._rr

        if direction.upper() == "LONG":
            sl = entry - stop_dist
            tp = entry + tp_dist
        else:
            sl = entry + stop_dist
            tp = entry - tp_dist

        risk_usdt = size_usdt * stop_dist / entry if entry > 0 else 0.0
        reward_usdt = risk_usdt * self._rr

        return StopLevels(
            entry=round(entry, 8),
            stop_loss=round(sl, 8),
            take_profit=round(tp, 8),
            atr=round(atr, 8),
            risk_usdt=round(risk_usdt, 4),
            reward_usdt=round(reward_usdt, 4),
            rr_ratio=self._rr,
            direction=direction.upper(),
        )

    def trailing_stop(
        self,
        current_price: float,
        current_stop: float,
        atr: float,
        direction: str,
    ) -> float:
        """Returneaza noul stop loss dupa trailing. Nu coboara niciodata."""
        dist = atr * self._atr_mult
        if direction.upper() == "LONG":
            new_stop = current_price - dist
            return max(new_stop, current_stop)
        else:
            new_stop = current_price + dist
            return min(new_stop, current_stop)

    def breakeven_stop(
        self,
        entry: float,
        current_price: float,
        current_stop: float,
        atr: float,
        direction: str,
        fee_pct: float = 0.001,
    ) -> float:
        """Muta stop-ul la breakeven daca pretul a avansat cu be_atr * ATR."""
        trigger_dist = atr * self._be_atr
        be_price = entry + fee_pct * entry  # acopera fee-ul

        if direction.upper() == "LONG":
            if current_price >= entry + trigger_dist:
                return max(current_stop, be_price)
        else:
            be_price = entry - fee_pct * entry
            if current_price <= entry - trigger_dist:
                return min(current_stop, be_price)

        return current_stop

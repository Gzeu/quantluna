"""
execution/sizing_engine.py  —  QuantLuna SizingEngine v1.0

Sprint S45 (2026-07-12):
  Calculeaza qty optima per trade bazat pe:
    1. Equity disponibil (din DailyPnLTracker / wallet)
    2. Volatilitate instrument (ATR sau sigma spread)
    3. Profit streak curent (ProfitOptimizer)
    4. Drawdown curent (din WatchdogMetrics)
    5. Kelly Criterion simplificat

  Reguli:
    - streak >= 3 wins  : +15% size (max 2x base_qty)
    - streak <= -3 loss : -30% size (min 0.3x base_qty)
    - drawdown > 5%     : -20% size suplimentar
    - volatility high   : -10% size suplimentar
    - Kelly ajustat     : cap la 25% equity per trade

Usage::

    engine = SizingEngine.from_env(pnl_tracker=tracker, notifier_bus=bus)
    qty = await engine.compute_qty(
        base_qty=0.01,
        symbol="BTCUSDT",
        streak=3,
        equity_usdt=1500.0,
        drawdown_pct=0.02,
        atr_pct=0.012,
    )
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from loguru import logger


@dataclass
class SizingConfig:
    # Streak adjustments
    streak_win_threshold: int   = 3
    streak_loss_threshold: int  = -3
    streak_win_boost: float     = 0.15    # +15% la streak >= 3 wins
    streak_loss_cut: float      = 0.30    # -30% la streak <= -3 loss
    max_size_multiplier: float  = 2.0     # max 2x base_qty
    min_size_multiplier: float  = 0.30    # min 0.3x base_qty

    # Drawdown adjustments
    dd_warning_pct: float       = 0.05    # 5% DD => -20% size
    dd_size_cut: float          = 0.20

    # Volatility adjustments
    atr_high_threshold: float   = 0.015   # ATR > 1.5% => high vol
    vol_size_cut: float         = 0.10

    # Kelly cap
    kelly_max_pct: float        = 0.25    # max 25% din equity per trade
    entry_price_usdt: float     = 0.0     # 0 = no kelly cap

    @classmethod
    def from_env(cls) -> "SizingConfig":
        return cls(
            streak_win_threshold=int(os.getenv("SIZING_STREAK_WIN_THR",   "3")),
            streak_loss_threshold=int(os.getenv("SIZING_STREAK_LOSS_THR", "-3")),
            streak_win_boost=float(os.getenv("SIZING_WIN_BOOST",   "0.15")),
            streak_loss_cut=float(os.getenv("SIZING_LOSS_CUT",    "0.30")),
            max_size_multiplier=float(os.getenv("SIZING_MAX_MULT", "2.0")),
            min_size_multiplier=float(os.getenv("SIZING_MIN_MULT", "0.30")),
            dd_warning_pct=float(os.getenv("SIZING_DD_WARNING",   "0.05")),
            dd_size_cut=float(os.getenv("SIZING_DD_CUT",          "0.20")),
            atr_high_threshold=float(os.getenv("SIZING_ATR_HIGH", "0.015")),
            vol_size_cut=float(os.getenv("SIZING_VOL_CUT",        "0.10")),
            kelly_max_pct=float(os.getenv("SIZING_KELLY_MAX",     "0.25")),
        )


class SizingEngine:
    """
    Calculeaza qty ajustata dinamic bazat pe streak, drawdown, volatilitate.

    Integrat in DecisionEngine / BybitLiveRunner la fiecare semnal ENTER.
    """

    def __init__(
        self,
        cfg: Optional[SizingConfig] = None,
        pnl_tracker=None,
        notifier_bus=None,
    ) -> None:
        self._cfg    = cfg or SizingConfig.from_env()
        self._tracker = pnl_tracker
        self._bus     = notifier_bus
        self._last_multiplier: float = 1.0

    @classmethod
    def from_env(
        cls,
        pnl_tracker=None,
        notifier_bus=None,
    ) -> "SizingEngine":
        return cls(
            cfg=SizingConfig.from_env(),
            pnl_tracker=pnl_tracker,
            notifier_bus=notifier_bus,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compute_qty(
        self,
        base_qty: float,
        symbol: str = "",
        streak: int = 0,
        equity_usdt: float = 0.0,
        drawdown_pct: float = 0.0,
        atr_pct: float = 0.0,
        entry_price_usdt: float = 0.0,
    ) -> float:
        """
        Calculeaza qty finala ajustata.

        Parameters
        ----------
        base_qty        : qty de baza din configuratie
        symbol          : simbol instrument
        streak          : profit streak curent (+N wins / -N losses)
        equity_usdt     : equity total disponibil
        drawdown_pct    : drawdown curent (0.05 = 5%)
        atr_pct         : ATR relativ la pret (0.012 = 1.2%)
        entry_price_usdt: pretul de intrare (pentru Kelly cap)

        Returns
        -------
        qty ajustata (float, >= min_size_multiplier * base_qty)
        """
        cfg = self._cfg
        multiplier = 1.0
        reasons = []

        # 1. Streak adjustment
        if streak >= cfg.streak_win_threshold:
            multiplier += cfg.streak_win_boost
            reasons.append(f"streak+{streak}:+{cfg.streak_win_boost:.0%}")
        elif streak <= cfg.streak_loss_threshold:
            multiplier -= cfg.streak_loss_cut
            reasons.append(f"streak{streak}:-{cfg.streak_loss_cut:.0%}")

        # 2. Drawdown adjustment
        if drawdown_pct >= cfg.dd_warning_pct:
            multiplier -= cfg.dd_size_cut
            reasons.append(f"dd{drawdown_pct:.1%}:-{cfg.dd_size_cut:.0%}")

        # 3. Volatility adjustment
        if atr_pct >= cfg.atr_high_threshold:
            multiplier -= cfg.vol_size_cut
            reasons.append(f"atr{atr_pct:.2%}:-{cfg.vol_size_cut:.0%}")

        # 4. Clamp multiplier
        multiplier = max(
            cfg.min_size_multiplier,
            min(cfg.max_size_multiplier, multiplier),
        )

        qty = base_qty * multiplier

        # 5. Kelly cap: max kelly_max_pct% din equity
        if equity_usdt > 0 and entry_price_usdt > 0:
            max_kelly_qty = (equity_usdt * cfg.kelly_max_pct) / entry_price_usdt
            if qty > max_kelly_qty:
                reasons.append(
                    f"kelly_cap:{qty:.6f}->{max_kelly_qty:.6f} "
                    f"(max {cfg.kelly_max_pct:.0%} equity)"
                )
                qty = max_kelly_qty

        self._last_multiplier = multiplier

        logger.info(
            "[SizingEngine] {} base={:.6f} mult={:.2f}x -> qty={:.6f} | {}",
            symbol or "??",
            base_qty,
            multiplier,
            qty,
            " | ".join(reasons) if reasons else "no_adjustment",
        )
        return qty

    @property
    def last_multiplier(self) -> float:
        """Ultimul multiplicator aplicat. Util pentru dashboard."""
        return self._last_multiplier

    def get_status(self) -> dict:
        return {
            "last_multiplier": self._last_multiplier,
            "streak_win_threshold":  self._cfg.streak_win_threshold,
            "streak_loss_threshold": self._cfg.streak_loss_threshold,
            "streak_win_boost":  self._cfg.streak_win_boost,
            "streak_loss_cut":   self._cfg.streak_loss_cut,
            "max_size_mult":     self._cfg.max_size_multiplier,
            "min_size_mult":     self._cfg.min_size_multiplier,
            "dd_warning_pct":    self._cfg.dd_warning_pct,
            "kelly_max_pct":     self._cfg.kelly_max_pct,
        }

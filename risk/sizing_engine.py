"""
QuantLuna — SizingEngine
Sprint 34

Wrapper stateful peste BybitPositionSizer care suporta:
  - Factor per pereche (set_pair_factor / reset_pair_factor)
  - Calcul sizing cu capital redus proportional cu factorul perechii
  - get_status() compatibil cu /sizing/live_status endpoint
  - Apelat de api/sizing.reduce_pair_size() — cale 1 (prioritate maxima)

Design:
  - SizingEngine nu modifica BybitPositionSizer (immutable sizer)
  - Factorul e aplicat la capital inainte de calculate():
      capital_efectiv = capital_original * factor
  - Un factor de 0.0 nu face sizing (returneaza zero result)
  - Factori in afara [0, 1] sunt clampati

Usage:
    from risk.sizing_engine import SizingEngine
    from risk.bybit_position_sizer import BybitPositionSizer, SizingParams

    sizer  = BybitPositionSizer(capital_usdt=50_000.0, kelly_fraction="half")
    engine = SizingEngine(sizer=sizer)

    # Watchdog reduce sizing pentru o pereche
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.5)   # -> 50% sizing
    result = engine.calculate("BTCUSDT-ETHUSDT", params)
    # result foloseste capital_usdt=25_000 (50% din 50_000)

    # Restaurare
    engine.reset_pair_factor("BTCUSDT-ETHUSDT")
    result = engine.calculate("BTCUSDT-ETHUSDT", params)
    # result foloseste capital_usdt=50_000 (full)

    # Status pentru /sizing/live_status
    engine.get_status()
    # -> {"capital_usdt": 50000, "pair_factors": {"BTCUSDT-ETHUSDT": 0.5}, ...}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from risk.bybit_position_sizer import BybitPositionSizer, SizingParams, SizingResult

logger = logging.getLogger(__name__)


class SizingEngine:
    """
    Wrapper stateful peste BybitPositionSizer.

    Mentine un dict de factori per pereche (_pair_factors) si aplica
    factorul corespunzator la capitalul sizer-ului inainte de calculate().

    Thread-safety: nu e garantata — designed pentru asyncio single-loop.
    """

    def __init__(self, sizer: BybitPositionSizer) -> None:
        self._sizer:            BybitPositionSizer  = sizer
        self._original_capital: float               = sizer.capital_usdt
        self._pair_factors:     Dict[str, float]    = {}

    # ------------------------------------------------------------------
    # Factor management (apelat de api/sizing.reduce_pair_size cale 1)
    # ------------------------------------------------------------------

    def set_pair_factor(self, pair_id: str, factor: float) -> None:
        """
        Seteaza factorul de sizing pentru o pereche.

        Args:
            pair_id: ID pereche (ex: "BTCUSDT-ETHUSDT")
            factor:  multiplicator [0.0, 1.0]
                       1.0 = sizing full (default implicit)
                       0.5 = 50% din capitalul original
                       0.0 = sizing zeroed (nu deschide pozitii noi)

        Raises:
            Nu ridica niciodata — failsafe.
        """
        factor = max(0.0, min(1.0, factor))  # clamp [0, 1]
        self._pair_factors[pair_id] = factor

        if factor == 0.0:
            logger.warning(
                "[SizingEngine] %s: factor=0.0 — sizing zeroed (nu se vor deschide pozitii noi)",
                pair_id,
            )
        else:
            logger.info(
                "[SizingEngine] set_pair_factor(%s, %.2f) — capital efectiv=%.2f USDT",
                pair_id, factor, self._original_capital * factor,
            )

    def get_pair_factor(self, pair_id: str) -> float:
        """
        Returneaza factorul curent al perechii.

        Returns:
            float in [0.0, 1.0]. Default 1.0 daca factorul nu a fost setat.
        """
        return self._pair_factors.get(pair_id, 1.0)

    def reset_pair_factor(self, pair_id: str) -> None:
        """
        Sterge factorul perechii, restaurand sizing-ul la 100%.

        No-op daca pair_id nu are factor setat.
        """
        removed = self._pair_factors.pop(pair_id, None)
        if removed is not None:
            logger.info(
                "[SizingEngine] reset_pair_factor(%s) — factor %.2f -> 1.0 (capital restaurat la %.2f USDT)",
                pair_id, removed, self._original_capital,
            )

    def reset_all_factors(self) -> None:
        """Sterge toti factorii activi, restaurand sizing-ul la 100% pentru toate perechile."""
        n = len(self._pair_factors)
        self._pair_factors.clear()
        logger.info("[SizingEngine] reset_all_factors() — %d factori sterse", n)

    # ------------------------------------------------------------------
    # Sizing cu factor aplicat
    # ------------------------------------------------------------------

    def calculate(
        self,
        pair_id: str,
        params:  SizingParams,
        method:  str = "kelly",
    ) -> SizingResult:
        """
        Calculeaza sizing-ul pentru o pereche, aplicand factorul activ.

        Factorul este aplicat la capitalul sizer-ului:
            capital_efectiv = capital_original * factor

        Daca factor == 0.0, returneaza imediat un zero result (fara calcul).

        Args:
            pair_id: ID pereche (folosit pentru lookup factor)
            params:  SizingParams complet (symbol, price, win_rate, etc.)
            method:  "kelly" | "fixed" (pasat direct la BybitPositionSizer)

        Returns:
            SizingResult cu sizing ajustat proportional cu factorul.
        """
        factor = self.get_pair_factor(pair_id)

        if factor == 0.0:
            logger.warning(
                "[SizingEngine] calculate(%s) cu factor=0.0 — returnez zero result",
                pair_id,
            )
            return self._sizer._zero_result(
                symbol=params.symbol,
                kelly_f=0.0,
                eff_f=0.0,
                method=f"{method}_zeroed_by_watchdog",
                warnings=[f"SizingEngine: factor=0.0 pentru {pair_id} — sizing suspendat de watchdog"],
            )

        # Aplica factorul la capital (temporar pe instanta sizer-ului)
        original_capital        = self._sizer.capital_usdt
        self._sizer.capital_usdt = round(self._original_capital * factor, 4)

        try:
            result = self._sizer.calculate(params, method=method)
        finally:
            # Restaureaza intotdeauna capitalul original, chiar si la exceptie
            self._sizer.capital_usdt = original_capital

        return result

    # ------------------------------------------------------------------
    # Status — compatibil cu /sizing/live_status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """
        Returneaza statusul curent al SizingEngine.

        Compatibil cu formatul asteptat de /sizing/live_status:
            return {"enabled": True, "source": "SizingEngine", **engine.get_status()}
        """
        active_factors = {pid: f for pid, f in self._pair_factors.items() if f < 1.0}
        return {
            "capital_usdt":     self._original_capital,
            "max_leverage":     self._sizer.max_leverage,
            "kelly_fraction":   self._sizer.kelly_fraction,
            "max_position_pct": self._sizer.max_position_pct,
            "min_notional":     self._sizer.min_notional,
            "fixed_fraction":   self._sizer.fixed_fraction,
            "pair_factors":     dict(self._pair_factors),
            "active_reductions": active_factors,
            "n_reduced_pairs":  len(active_factors),
            "sprint":           "S34",
        }

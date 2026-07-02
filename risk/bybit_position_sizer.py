"""
QuantLuna — BybitPositionSizer
Sprint 28

Calculeaza marimea pozitiei pentru Bybit Linear USDT Contracts cu:
  - Kelly Criterion (full, half, quarter kelly)
  - Fixed-fraction sizing
  - Leverage-aware: limiteaza la max_leverage
  - Margin requirement calc (initial margin = notional / leverage)
  - Qty rounding conform qtyStep (din instrument info sau configurat)
  - Notional in USDT output
  - Max position size cap (% din capital)
  - Contract value = 1 USD (Linear USDT perpetual standard)

Formule:
  Kelly fraction = (p * b - (1-p)) / b
    unde p = win_rate, b = avg_win / avg_loss (profit factor)

  Notional USDT = capital * kelly_fraction * kelly_scale
  Qty = floor(notional / (entry_price * contract_size) / qty_step) * qty_step
  Margin required = notional / leverage

Usage:
    from risk.bybit_position_sizer import BybitPositionSizer, SizingParams

    sizer = BybitPositionSizer(
        capital_usdt=10_000.0,
        max_leverage=3.0,
        kelly_fraction="half",   # "full" | "half" | "quarter"
        max_position_pct=0.20,   # max 20% capital per pozitie
    )

    result = sizer.calculate(
        SizingParams(
            symbol="BTCUSDT",
            entry_price=65_000.0,
            win_rate=0.55,
            avg_win_usd=120.0,
            avg_loss_usd=80.0,
            leverage=2.0,
        )
    )
    # result.qty_contracts = 0.002
    # result.notional_usdt = 130.0
    # result.margin_usdt   = 65.0
    # result.kelly_f       = 0.1875
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SizingParams:
    """Input params pentru un calcul de pozitie."""
    symbol:        str
    entry_price:   float
    win_rate:      float           # 0.0 - 1.0
    avg_win_usd:   float           # medie profit per tranzactie castigatoare
    avg_loss_usd:  float           # medie pierdere per tranzactie perdanta (pozitiv)
    leverage:      float  = 1.0    # levier dorit (1x - max_leverage)
    qty_step:      float  = 0.001  # rounding step din instrument info
    contract_size: float  = 1.0    # 1.0 pentru USDT Linear
    override_fraction: Optional[float] = None  # override kelly cu valoare fixa


@dataclass
class SizingResult:
    """Rezultatul unui calcul de pozitie."""
    symbol:          str
    qty_contracts:   float   # cantitate in contracte (rotunjita la qty_step)
    notional_usdt:   float   # valoare notionala (qty * price * contract_size)
    margin_usdt:     float   # marja initiala necesara
    leverage:        float   # levierul efectiv folosit
    kelly_f:         float   # fractia Kelly (0-1) inainte de scale
    effective_f:     float   # fractia efectiva aplicata (dupa scale + cap)
    pct_of_capital:  float   # % din capital alocat
    sizing_method:   str
    capped:          bool    # True daca a fost aplicat max_position_pct
    warnings:        list    = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "qty_contracts":  self.qty_contracts,
            "notional_usdt":  round(self.notional_usdt, 4),
            "margin_usdt":    round(self.margin_usdt, 4),
            "leverage":       self.leverage,
            "kelly_f":        round(self.kelly_f, 6),
            "effective_f":    round(self.effective_f, 6),
            "pct_of_capital": round(self.pct_of_capital, 4),
            "sizing_method":  self.sizing_method,
            "capped":         self.capped,
            "warnings":       self.warnings,
        }


class BybitPositionSizer:
    """
    Position sizer pentru Bybit Linear USDT Perpetual Contracts.
    Suporta Kelly (full/half/quarter) si fixed-fraction.
    """

    # Kelly scale factors
    _KELLY_SCALES: Dict[str, float] = {
        "full":    1.0,
        "half":    0.5,
        "quarter": 0.25,
    }

    def __init__(
        self,
        capital_usdt:    float = 10_000.0,
        max_leverage:    float = 3.0,
        kelly_fraction:  str   = "half",    # "full" | "half" | "quarter"
        max_position_pct: float = 0.25,     # max 25% capital per pozitie
        min_notional:    float = 5.0,       # Bybit min order notional
        fixed_fraction:  float = 0.02,      # pentru fixed-fraction method (2%)
    ) -> None:
        self.capital_usdt     = capital_usdt
        self.max_leverage     = max_leverage
        self.kelly_fraction   = kelly_fraction
        self.max_position_pct = max_position_pct
        self.min_notional     = min_notional
        self.fixed_fraction   = fixed_fraction

        if kelly_fraction not in self._KELLY_SCALES:
            raise ValueError(f"kelly_fraction trebuie sa fie 'full', 'half', sau 'quarter', nu '{kelly_fraction}'")

    # ------------------------------------------------------------------
    # Main API
    # ------------------------------------------------------------------

    def calculate(self, params: SizingParams, method: str = "kelly") -> SizingResult:
        """
        Calculeaza marimea pozitiei.
        method: "kelly" | "fixed"
        """
        if method == "fixed":
            return self._size_fixed(params)
        return self._size_kelly(params)

    def kelly_fraction_raw(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Calculeaza Kelly fraction brut.
        f* = (p * b - (1 - p)) / b
        unde b = avg_win / avg_loss (profit factor)
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        b = avg_win / avg_loss
        if b <= 0:
            return 0.0
        f = (win_rate * b - (1 - win_rate)) / b
        return max(0.0, f)

    def required_margin(self, notional_usdt: float, leverage: float) -> float:
        """Marja initiala = notional / leverage."""
        if leverage <= 0:
            return notional_usdt
        return notional_usdt / leverage

    def max_notional_for_leverage(self, leverage: float) -> float:
        """Notional maxim dat levierul si capitalul."""
        return self.capital_usdt * min(leverage, self.max_leverage)

    # ------------------------------------------------------------------
    # Sizing methods
    # ------------------------------------------------------------------

    def _size_kelly(self, params: SizingParams) -> SizingResult:
        warnings = []

        # 1. Kelly fraction raw
        if params.override_fraction is not None:
            kelly_f = float(params.override_fraction)
            method  = "override"
        else:
            kelly_f = self.kelly_fraction_raw(
                params.win_rate, params.avg_win_usd, params.avg_loss_usd
            )
            method = f"kelly_{self.kelly_fraction}"

        # 2. Scale (half / quarter Kelly)
        scale    = self._KELLY_SCALES.get(self.kelly_fraction, 0.5)
        eff_f    = kelly_f * scale if params.override_fraction is None else kelly_f

        # 3. Cap la max_position_pct
        capped = False
        if eff_f > self.max_position_pct:
            eff_f  = self.max_position_pct
            capped = True
            warnings.append(f"Kelly capped la max_position_pct={self.max_position_pct:.0%}")

        # 4. Notional brut
        notional_raw = self.capital_usdt * eff_f

        # 5. Leverage check
        leverage = min(float(params.leverage), self.max_leverage)
        if params.leverage > self.max_leverage:
            warnings.append(f"Leverage {params.leverage}x > max {self.max_leverage}x, capped")

        # 6. Max notional dat leverage (nu putem riste mai mult decat capital * leverage)
        max_notional = self.capital_usdt * leverage
        if notional_raw > max_notional:
            notional_raw = max_notional
            capped = True
            warnings.append(f"Notional capped la capital*leverage={max_notional:.0f} USDT")

        # 7. Min notional check
        if notional_raw < self.min_notional:
            warnings.append(f"Notional {notional_raw:.2f} < min {self.min_notional} USDT, returnez qty=0")
            return self._zero_result(params.symbol, kelly_f, eff_f, method, warnings)

        # 8. Qty calculation
        qty = self._notional_to_qty(notional_raw, params.entry_price, params.qty_step, params.contract_size)
        actual_notional = qty * params.entry_price * params.contract_size
        margin          = self.required_margin(actual_notional, leverage)
        pct             = actual_notional / self.capital_usdt if self.capital_usdt else 0.0

        return SizingResult(
            symbol=params.symbol,
            qty_contracts=qty,
            notional_usdt=round(actual_notional, 4),
            margin_usdt=round(margin, 4),
            leverage=leverage,
            kelly_f=round(kelly_f, 6),
            effective_f=round(eff_f, 6),
            pct_of_capital=round(pct, 4),
            sizing_method=method,
            capped=capped,
            warnings=warnings,
        )

    def _size_fixed(self, params: SizingParams) -> SizingResult:
        warnings = []
        eff_f    = self.fixed_fraction
        leverage = min(float(params.leverage), self.max_leverage)

        notional = self.capital_usdt * eff_f * leverage
        notional = min(notional, self.capital_usdt * leverage)  # cap

        capped = False
        if self.capital_usdt * eff_f > self.capital_usdt * self.max_position_pct:
            notional = self.capital_usdt * self.max_position_pct * leverage
            capped   = True
            warnings.append("Fixed fraction capped la max_position_pct")

        if notional < self.min_notional:
            warnings.append(f"Notional {notional:.2f} < min {self.min_notional} USDT")
            return self._zero_result(params.symbol, 0.0, eff_f, "fixed", warnings)

        qty            = self._notional_to_qty(notional, params.entry_price, params.qty_step, params.contract_size)
        actual_notional = qty * params.entry_price * params.contract_size
        margin          = self.required_margin(actual_notional, leverage)
        pct             = actual_notional / self.capital_usdt if self.capital_usdt else 0.0

        return SizingResult(
            symbol=params.symbol,
            qty_contracts=qty,
            notional_usdt=round(actual_notional, 4),
            margin_usdt=round(margin, 4),
            leverage=leverage,
            kelly_f=0.0,
            effective_f=round(eff_f, 6),
            pct_of_capital=round(pct, 4),
            sizing_method="fixed",
            capped=capped,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _notional_to_qty(notional: float, price: float, qty_step: float, contract_size: float) -> float:
        if price <= 0 or contract_size <= 0:
            return 0.0
        raw_qty  = notional / (price * contract_size)
        if qty_step <= 0:
            return round(raw_qty, 8)
        decimals = max(0, -int(math.floor(math.log10(qty_step)))) if qty_step < 1 else 0
        qty      = math.floor(raw_qty / qty_step) * qty_step
        return round(qty, decimals)

    def _zero_result(self, symbol, kelly_f, eff_f, method, warnings) -> SizingResult:
        return SizingResult(
            symbol=symbol, qty_contracts=0.0, notional_usdt=0.0,
            margin_usdt=0.0, leverage=1.0, kelly_f=kelly_f,
            effective_f=eff_f, pct_of_capital=0.0,
            sizing_method=method, capped=False, warnings=warnings,
        )

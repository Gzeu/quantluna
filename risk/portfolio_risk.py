"""
QuantLuna — Portfolio-Level Risk Monitor  (Sprint 4, updated Sprint 10)

Tracks:
  - Total exposure across all active pairs
  - Drawdown monitor with circuit breaker
  - Daily P&L attribution
  - Trade history recording (nou Sprint 10: record_trade())

Notă Sprint 10:
  Acest modul este încapsulat de PortfolioAllocator din risk/multi_pair_allocator.py.
  Nu îl instancia direct dacă folosești PortfolioAllocator — allocatorul
  îl creează și îl gestionează intern.
  Dacă ai cod legacy care folosește PortfolioRisk direct, funcționează
  în continuare fără modificări.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from loguru import logger


@dataclass
class PairExposure:
    pair: str
    notional_usdt: float
    current_pnl: float
    entry_zscore: float
    current_zscore: float
    beta: float
    open_since: Optional[pd.Timestamp] = None


class PortfolioRisk:
    """
    Portfolio-level risk aggregator and circuit breaker.
    """

    def __init__(
        self,
        max_total_exposure_pct: float = 0.80,
        max_pair_corr: float = 0.70,
        max_drawdown: float = 0.15,
        capital_usdt: float = 10000,
    ):
        self.max_exposure_pct = max_total_exposure_pct
        self.max_pair_corr = max_pair_corr
        self.max_dd = max_drawdown
        self.capital = capital_usdt

        self._positions: Dict[str, PairExposure] = {}
        self._equity_curve: List[float] = [capital_usdt]
        self._circuit_breaker = False
        self._trade_pnl_history: List[float] = []  # Sprint 10

    def add_position(self, exposure: PairExposure) -> bool:
        """Returns True if position is allowed, False if blocked by risk limits."""
        if self._circuit_breaker:
            logger.error("Circuit breaker active — no new positions")
            return False

        total_exposure = sum(p.notional_usdt for p in self._positions.values())
        if (total_exposure + exposure.notional_usdt) > self.max_exposure_pct * self.capital:
            logger.warning("Total exposure limit reached")
            return False

        self._positions[exposure.pair] = exposure
        logger.info(f"Position added: {exposure.pair} {exposure.notional_usdt:.0f} USDT")
        return True

    def update_pnl(self, pair: str, pnl: float) -> None:
        if pair in self._positions:
            self._positions[pair].current_pnl = pnl

    def record_trade(self, pnl_usd: float) -> None:
        """
        Înregistrează P&L-ul unui trade încheiat în USDT.
        Apelat din LiveTrader._close_position() după fiecare exit.
        Actualizează equity curve și verifică circuit breaker.
        """
        self._trade_pnl_history.append(pnl_usd)
        # menținem ultimele 500 trades
        if len(self._trade_pnl_history) > 500:
            self._trade_pnl_history = self._trade_pnl_history[-500:]
        # actualizează equity
        equity = self.capital + sum(self._trade_pnl_history)
        self._equity_curve.append(equity)
        # re-verifică drawdown
        dd = self.check_drawdown()
        logger.debug(f"record_trade: pnl={pnl_usd:.2f} equity={equity:.2f} dd={dd:.2%}")

    def check_drawdown(self) -> float:
        """Returns current drawdown fraction. Triggers circuit breaker if exceeded."""
        total_pnl = sum(p.current_pnl for p in self._positions.values())
        equity = self.capital + total_pnl
        peak = max(self._equity_curve) if self._equity_curve else self.capital
        dd = (peak - equity) / peak if peak > 0 else 0.0

        self._equity_curve.append(equity)

        if dd >= self.max_dd:
            self._circuit_breaker = True
            logger.critical(
                f"CIRCUIT BREAKER: DD={dd:.1%} ≥ {self.max_dd:.1%} — ALL TRADING HALTED"
            )

        return dd

    def remove_position(self, pair: str) -> Optional[PairExposure]:
        return self._positions.pop(pair, None)

    @property
    def is_active(self) -> bool:
        return not self._circuit_breaker

    def summary(self) -> dict:
        total_exp = sum(p.notional_usdt for p in self._positions.values())
        total_pnl = sum(p.current_pnl for p in self._positions.values())
        return {
            "n_positions": len(self._positions),
            "total_exposure_usdt": total_exp,
            "total_pnl_usdt": total_pnl,
            "capital": self.capital,
            "circuit_breaker": self._circuit_breaker,
            "drawdown": self.check_drawdown(),
            "n_trades_recorded": len(self._trade_pnl_history),
        }

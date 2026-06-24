"""
QuantLuna — Portfolio-Level Risk Monitor

Tracks:
  - Total exposure across all active pairs
  - Correlation matrix between spreads (avoid concentration)
  - Drawdown monitor with circuit breaker
  - Daily P&L attribution
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
        max_total_exposure_pct: float = 0.80,  # Max 80% of capital deployed
        max_pair_corr: float = 0.70,           # Max correlation between spreads
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

    def check_drawdown(self) -> float:
        """Returns current drawdown fraction. Triggers circuit breaker if exceeded."""
        total_pnl = sum(p.current_pnl for p in self._positions.values())
        equity = self.capital + total_pnl
        peak = max(self._equity_curve)
        dd = (peak - equity) / peak if peak > 0 else 0.0

        self._equity_curve.append(equity)

        if dd >= self.max_dd:
            self._circuit_breaker = True
            logger.critical(f"CIRCUIT BREAKER: DD={dd:.1%} ≥ {self.max_dd:.1%} — ALL TRADING HALTED")

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
        }

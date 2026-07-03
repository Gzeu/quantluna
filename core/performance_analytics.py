"""
core/performance_analytics.py — live performance metrics calculator.

Calculates Sharpe, Sortino, Calmar, max drawdown, profit factor,
and win rate from a list of PnL observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np


@dataclass
class PerformanceMetrics:
    n_trades: int
    total_pnl: float
    win_rate: float
    profit_factor: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    avg_win: float
    avg_loss: float
    expectancy: float

    def as_dict(self) -> dict:
        return {
            "n_trades": self.n_trades,
            "total_pnl": round(self.total_pnl, 4),
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "calmar": round(self.calmar, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "avg_win": round(self.avg_win, 4),
            "avg_loss": round(self.avg_loss, 4),
            "expectancy": round(self.expectancy, 4),
        }


def compute_max_drawdown(pnl_series: List[float]) -> float:
    if not pnl_series:
        return 0.0
    cum = np.cumsum(pnl_series)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    return float(dd.max())


def compute_sharpe(
    pnl_series: List[float],
    periods_per_year: float = 365.0,
    risk_free: float = 0.0,
) -> float:
    if len(pnl_series) < 2:
        return 0.0
    arr = np.array(pnl_series, dtype=float)
    excess = arr - risk_free / periods_per_year
    std = excess.std(ddof=1)
    if std < 1e-12:
        return 0.0
    return float((excess.mean() / std) * np.sqrt(periods_per_year))


def compute_sortino(
    pnl_series: List[float],
    periods_per_year: float = 365.0,
    risk_free: float = 0.0,
) -> float:
    if len(pnl_series) < 2:
        return 0.0
    arr = np.array(pnl_series, dtype=float)
    excess = arr - risk_free / periods_per_year
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf")
    down_std = downside.std(ddof=1) if len(downside) > 1 else abs(downside[0])
    if down_std < 1e-12:
        return 0.0
    return float((excess.mean() / down_std) * np.sqrt(periods_per_year))


def compute_calmar(total_pnl: float, max_drawdown: float) -> float:
    if max_drawdown < 1e-12:
        return float("inf") if total_pnl > 0 else 0.0
    return float(total_pnl / max_drawdown)


def compute_profit_factor(pnl_series: List[float]) -> float:
    gross_profit = sum(p for p in pnl_series if p > 0)
    gross_loss = abs(sum(p for p in pnl_series if p < 0))
    if gross_loss < 1e-12:
        return float("inf") if gross_profit > 0 else 1.0
    return gross_profit / gross_loss


def analyze(pnl_series: List[float], periods_per_year: float = 365.0) -> PerformanceMetrics:
    """Compute all performance metrics from a list of per-trade PnL values."""
    n = len(pnl_series)
    if n == 0:
        return PerformanceMetrics(
            n_trades=0, total_pnl=0.0, win_rate=0.0, profit_factor=1.0,
            sharpe=0.0, sortino=0.0, calmar=0.0, max_drawdown=0.0,
            avg_win=0.0, avg_loss=0.0, expectancy=0.0,
        )
    wins = [p for p in pnl_series if p > 0]
    losses = [p for p in pnl_series if p < 0]
    win_rate = len(wins) / n
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    total_pnl = sum(pnl_series)
    max_dd = compute_max_drawdown(pnl_series)
    return PerformanceMetrics(
        n_trades=n,
        total_pnl=total_pnl,
        win_rate=win_rate,
        profit_factor=compute_profit_factor(pnl_series),
        sharpe=compute_sharpe(pnl_series, periods_per_year),
        sortino=compute_sortino(pnl_series, periods_per_year),
        calmar=compute_calmar(total_pnl, max_dd),
        max_drawdown=max_dd,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
    )

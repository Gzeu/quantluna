"""
QuantLuna — RiskDashboardEngine
Sprint 27

Calculează și expune metrici de risc live:
  - Rolling Sharpe (window N bare, configurable)
  - Current Drawdown (și Max DD pe sesiune)
  - Win rate (și per pereche)
  - Exposure total și per pereche
  - Equity curve (timestamp, equity_usd)
  - Per-pair stats snapshot
  - Portfolio-level aggregation

Usage:
    from risk.dashboard_engine import RiskDashboardEngine
    engine = RiskDashboardEngine(initial_capital=10_000.0, sharpe_window=30)
    engine.record_trade(pair="BTC/ETH", pnl_usd=12.5, fees_usd=0.3,
                        is_win=True, notional_usd=500.0)
    snapshot = engine.snapshot()    # dict complet, JSON-serializable
    equity   = engine.equity_curve  # List[{ts, equity}]
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class PairStats:
    pair:          str
    trade_count:   int   = 0
    win_count:     int   = 0
    total_pnl:     float = 0.0
    total_fees:    float = 0.0
    current_notional: float = 0.0  # live exposure
    peak_pnl:      float = 0.0
    max_dd:        float = 0.0
    last_update:   float = field(default_factory=time.time)

    @property
    def win_rate(self) -> float:
        return self.win_count / self.trade_count if self.trade_count else 0.0

    @property
    def net_pnl(self) -> float:
        return self.total_pnl - self.total_fees

    def to_dict(self) -> dict:
        return {
            "pair":             self.pair,
            "trade_count":      self.trade_count,
            "win_rate":         round(self.win_rate, 4),
            "total_pnl_usd":    round(self.total_pnl, 4),
            "total_fees_usd":   round(self.total_fees, 4),
            "net_pnl_usd":      round(self.net_pnl, 4),
            "exposure_usd":     round(self.current_notional, 2),
            "max_dd":           round(self.max_dd, 4),
            "last_update":      self.last_update,
        }


class RiskDashboardEngine:
    """
    In-memory risk metrics engine.
    Thread-safe enough for single-event-loop async usage.
    For multi-process: wrap with Redis pub/sub (see REDIS.md).
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        sharpe_window:   int   = 30,
        risk_free_rate:  float = 0.0,
        bars_per_year:   int   = 8760,  # 1h bars
    ) -> None:
        self.initial_capital = initial_capital
        self.sharpe_window   = sharpe_window
        self.risk_free_rate  = risk_free_rate
        self.bars_per_year   = bars_per_year

        self._equity:        float = initial_capital
        self._peak_equity:   float = initial_capital
        self._equity_curve:  List[dict] = [{"ts": time.time(), "equity": initial_capital}]
        self._returns:       deque = deque(maxlen=sharpe_window)
        self._pair_stats:    Dict[str, PairStats] = {}
        self._session_start: float = time.time()
        self._total_trades:  int   = 0
        self._total_wins:    int   = 0
        self._total_pnl:     float = 0.0
        self._total_fees:    float = 0.0
        self._max_dd:        float = 0.0

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def record_trade(
        self,
        pair:         str,
        pnl_usd:      float,
        fees_usd:     float = 0.0,
        is_win:       bool  = True,
        notional_usd: float = 0.0,
    ) -> None:
        """Call this after each trade close."""
        net = pnl_usd - fees_usd
        self._equity += net
        self._total_pnl   += pnl_usd
        self._total_fees  += fees_usd
        self._total_trades += 1
        if is_win:
            self._total_wins += 1

        # Equity curve + return
        if self._equity_curve:
            prev_eq = self._equity_curve[-1]["equity"]
            if prev_eq != 0:
                ret = net / prev_eq
                self._returns.append(ret)
        self._equity_curve.append({"ts": time.time(), "equity": round(self._equity, 4)})

        # Drawdown
        if self._equity > self._peak_equity:
            self._peak_equity = self._equity
        dd = (self._peak_equity - self._equity) / self._peak_equity if self._peak_equity else 0.0
        if dd > self._max_dd:
            self._max_dd = dd

        # Per-pair
        ps = self._get_pair(pair)
        ps.trade_count   += 1
        ps.total_pnl     += pnl_usd
        ps.total_fees    += fees_usd
        if is_win:
            ps.win_count += 1
        if pnl_usd > ps.peak_pnl:
            ps.peak_pnl = pnl_usd
        pair_dd = (ps.peak_pnl - ps.total_pnl) / ps.peak_pnl if ps.peak_pnl > 0 else 0.0
        if pair_dd > ps.max_dd:
            ps.max_dd = pair_dd
        ps.last_update = time.time()

    def update_exposure(
        self,
        pair:         str,
        notional_usd: float,
    ) -> None:
        """Update live exposure (called on position open/close)."""
        self._get_pair(pair).current_notional = notional_usd

    # ------------------------------------------------------------------
    # Computed metrics
    # ------------------------------------------------------------------

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def equity_curve(self) -> List[dict]:
        return self._equity_curve

    @property
    def current_drawdown(self) -> float:
        if self._peak_equity == 0:
            return 0.0
        return (self._peak_equity - self._equity) / self._peak_equity

    @property
    def max_drawdown(self) -> float:
        return self._max_dd

    @property
    def win_rate(self) -> float:
        return self._total_wins / self._total_trades if self._total_trades else 0.0

    @property
    def rolling_sharpe(self) -> float:
        """Rolling Sharpe on last N trades."""
        rets = list(self._returns)
        if len(rets) < 2:
            return 0.0
        n    = len(rets)
        mean = sum(rets) / n
        var  = sum((r - mean) ** 2 for r in rets) / (n - 1)
        std  = math.sqrt(var) if var > 0 else 0.0
        if std == 0:
            return 0.0
        excess = mean - (self.risk_free_rate / self.bars_per_year)
        return round((excess / std) * math.sqrt(self.bars_per_year), 4)

    @property
    def total_exposure_usd(self) -> float:
        return sum(ps.current_notional for ps in self._pair_stats.values())

    @property
    def exposure_pct(self) -> float:
        return self.total_exposure_usd / self.initial_capital if self.initial_capital else 0.0

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Full JSON-serializable snapshot for SSE / API."""
        return {
            "ts":               time.time(),
            "equity_usd":       round(self._equity, 4),
            "initial_capital":  self.initial_capital,
            "pnl_usd":          round(self._equity - self.initial_capital, 4),
            "pnl_pct":          round((self._equity - self.initial_capital) / self.initial_capital, 6)
                                if self.initial_capital else 0.0,
            "rolling_sharpe":   self.rolling_sharpe,
            "current_dd":       round(self.current_drawdown, 6),
            "max_dd":           round(self.max_drawdown, 6),
            "win_rate":         round(self.win_rate, 4),
            "total_trades":     self._total_trades,
            "total_pnl_usd":    round(self._total_pnl, 4),
            "total_fees_usd":   round(self._total_fees, 4),
            "net_pnl_usd":      round(self._total_pnl - self._total_fees, 4),
            "exposure_usd":     round(self.total_exposure_usd, 2),
            "exposure_pct":     round(self.exposure_pct, 4),
            "n_active_pairs":   sum(
                1 for ps in self._pair_stats.values() if ps.current_notional > 0
            ),
            "pairs":            {k: v.to_dict() for k, v in self._pair_stats.items()},
            "session_uptime_s": round(time.time() - self._session_start, 1),
        }

    def pair_snapshot(self, pair: str) -> Optional[dict]:
        ps = self._pair_stats.get(pair)
        return ps.to_dict() if ps else None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_pair(self, pair: str) -> PairStats:
        if pair not in self._pair_stats:
            self._pair_stats[pair] = PairStats(pair=pair)
        return self._pair_stats[pair]

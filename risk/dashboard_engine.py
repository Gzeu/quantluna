"""
QuantLuna — RiskDashboardEngine  (S37 — metrics expansion)

Camari adaugate fata de S27:
  - avg_win_usd / avg_loss_usd / profit_factor
  - consecutive wins/losses tracking + current streak
  - daily_pnl / daily_pct  (reset la miezul noptii UTC)
  - unrealized_pnl  (set din exterior via update_unrealized)
  - pair_breakdown[]  in formatul asteptat de frontend
  - PairStats: avg_win, avg_loss, loss_count, win_pnl_sum, loss_pnl_sum

Usage:
    from risk.dashboard_engine import RiskDashboardEngine
    engine = RiskDashboardEngine(initial_capital=10_000.0)
    engine.record_trade(pair="BTC/ETH", pnl_usd=12.5, fees_usd=0.3,
                        is_win=True, notional_usd=500.0)
    snapshot = engine.snapshot()   # JSON-serializable, schema completa
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PairStats:
    pair:             str
    trade_count:      int   = 0
    win_count:        int   = 0
    loss_count:       int   = 0
    total_pnl:        float = 0.0
    total_fees:       float = 0.0
    win_pnl_sum:      float = 0.0   # suma PnL trade-uri castigatoare
    loss_pnl_sum:     float = 0.0   # suma ABS(PnL) trade-uri pierzatoare
    current_notional: float = 0.0
    peak_pnl:         float = 0.0
    max_dd:           float = 0.0
    last_update:      float = field(default_factory=time.time)
    active:           bool  = False

    @property
    def win_rate(self) -> float:
        return self.win_count / self.trade_count if self.trade_count else 0.0

    @property
    def net_pnl(self) -> float:
        return self.total_pnl - self.total_fees

    @property
    def avg_win(self) -> float:
        return self.win_pnl_sum / self.win_count if self.win_count else 0.0

    @property
    def avg_loss(self) -> float:
        """Valoare pozitiva = marimea medie a pierderii."""
        return self.loss_pnl_sum / self.loss_count if self.loss_count else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.net_pnl / self.trade_count if self.trade_count else 0.0

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

    def to_breakdown_dict(self) -> dict:
        """Format asteptat de frontend TradeBreakdown."""
        return {
            "pair":         self.pair,
            "wins":         self.win_count,
            "losses":       self.loss_count,
            "total_trades": self.trade_count,
            "win_rate":     round(self.win_rate, 4),
            "total_pnl":    round(self.net_pnl, 2),
            "avg_pnl":      round(self.avg_pnl, 2),
            "avg_win":      round(self.avg_win, 2),
            "avg_loss":     round(self.avg_loss, 2),
            "max_loss":     round(self.max_dd, 2),
            "active":       self.active,
        }


class RiskDashboardEngine:
    """
    In-memory risk metrics engine — S37 extended.
    Thread-safe for single-event-loop async usage.
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        sharpe_window:   int   = 30,
        risk_free_rate:  float = 0.0,
        bars_per_year:   int   = 8760,
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
        self._day_start_ts:  float = self._today_start()
        self._day_start_eq:  float = initial_capital

        self._total_trades:  int   = 0
        self._total_wins:    int   = 0
        self._total_losses:  int   = 0
        self._total_pnl:     float = 0.0
        self._total_fees:    float = 0.0
        self._max_dd:        float = 0.0

        self._gross_profit:  float = 0.0
        self._gross_loss:    float = 0.0

        self._current_streak:          int = 0
        self._max_consecutive_wins:    int = 0
        self._max_consecutive_losses:  int = 0

        self._unrealized_pnl: float = 0.0

    def update_equity(self, new_equity: float) -> None:
        """Update the equity baseline to match real exchange balance."""
        self.initial_capital = new_equity
        self._equity         = new_equity
        self._peak_equity    = new_equity
        self._day_start_eq   = new_equity
        self._equity_curve   = [{"ts": time.time(), "equity": new_equity}]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _today_start() -> float:
        import datetime
        now = datetime.datetime.utcnow()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp()

    def _maybe_reset_day(self) -> None:
        """Reset daily accumulators daca a trecut ziua UTC."""
        today = self._today_start()
        if today > self._day_start_ts:
            self._day_start_ts = today
            self._day_start_eq = self._equity

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
        """Apelat dupa fiecare trade inchis."""
        self._maybe_reset_day()
        net = pnl_usd - fees_usd
        self._equity       += net
        self._total_pnl    += pnl_usd
        self._total_fees   += fees_usd
        self._total_trades += 1

        # Win/loss accumulators
        if is_win:
            self._total_wins   += 1
            self._gross_profit += net
            # Streak
            self._current_streak = self._current_streak + 1 if self._current_streak >= 0 else 1
            if self._current_streak > self._max_consecutive_wins:
                self._max_consecutive_wins = self._current_streak
        else:
            self._total_losses   += 1
            self._gross_loss     += abs(net)
            self._current_streak = self._current_streak - 1 if self._current_streak <= 0 else -1
            if abs(self._current_streak) > self._max_consecutive_losses:
                self._max_consecutive_losses = abs(self._current_streak)

        # Equity curve + rolling return
        if self._equity_curve:
            prev_eq = self._equity_curve[-1]["equity"]
            if prev_eq != 0:
                self._returns.append(net / prev_eq)
        self._equity_curve.append({"ts": time.time(), "equity": round(self._equity, 4)})

        # Max drawdown
        if self._equity > self._peak_equity:
            self._peak_equity = self._equity
        dd = (
            (self._peak_equity - self._equity) / self._peak_equity
            if self._peak_equity else 0.0
        )
        if dd > self._max_dd:
            self._max_dd = dd

        # Per-pair stats
        ps = self._get_pair(pair)
        ps.trade_count += 1
        ps.total_pnl   += pnl_usd
        ps.total_fees  += fees_usd
        if is_win:
            ps.win_count   += 1
            ps.win_pnl_sum += net
        else:
            ps.loss_count   += 1
            ps.loss_pnl_sum += abs(net)
        if pnl_usd > ps.peak_pnl:
            ps.peak_pnl = pnl_usd
        pair_dd = (
            (ps.peak_pnl - ps.total_pnl) / ps.peak_pnl
            if ps.peak_pnl > 0 else 0.0
        )
        if pair_dd > ps.max_dd:
            ps.max_dd = pair_dd
        ps.last_update = time.time()

    def update_exposure(
        self,
        pair:         str,
        notional_usd: float,
        active:       bool = True,
    ) -> None:
        """Actualizeaza expunerea si statusul activ al unei perechi."""
        ps = self._get_pair(pair)
        ps.current_notional = notional_usd
        ps.active           = active and notional_usd > 0

    def update_unrealized(self, unrealized_pnl: float) -> None:
        """Seteaza PnL nerealizat total (pozitii deschise)."""
        self._unrealized_pnl = unrealized_pnl

    def update_position(
        self,
        pair_id: str,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        unrealised_pnl: float,
    ) -> None:
        """
        Actualizeaza starea unei pozitii individuale (de pe Bybit).

        Apelat periodic de MultiPairManager._run_pair() pentru a mentine
        metricile per-pereche sincronizate cu pozitiile reale.

        Args:
            pair_id: ID pereche (ex: "BTCUSDT-ETHUSDT")
            symbol:  simbolul individual (ex: "BTCUSDT")
            side:    "Long" | "Short"
            size:    cantitatea
            entry_price: pretul mediu de intrare
            unrealised_pnl: PnL nerealizat in USDT
        """
        # Actualizeaza expunerea per-pereche
        notional = size * entry_price
        self.update_exposure(pair_id, notional, active=(size > 0))

        # Actualizeaza PnL nerealizat total
        self._unrealized_pnl += unrealised_pnl

        # Actualizeaza PairStats pentru perechea respectiva
        ps = self._get_pair(pair_id)
        ps.current_notional = notional
        ps.active = (size > 0)
        ps.last_update = time.time()

        logger.debug(
            "RiskDashboardEngine.update_position: pair=%s symbol=%s side=%s "
            "size=%.4f entry=%.4f uPnL=%+.4f",
            pair_id, symbol, side, size, entry_price, unrealised_pnl,
        )

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
    def profit_factor(self) -> float:
        """Gross profit / gross loss. 0 daca nu exista pierderi."""
        if self._gross_loss == 0:
            return 0.0
        return round(self._gross_profit / self._gross_loss, 4)

    @property
    def avg_win_usd(self) -> float:
        return round(self._gross_profit / self._total_wins, 4) if self._total_wins else 0.0

    @property
    def avg_loss_usd(self) -> float:
        return round(self._gross_loss / self._total_losses, 4) if self._total_losses else 0.0

    @property
    def daily_pnl(self) -> float:
        self._maybe_reset_day()
        return round(self._equity - self._day_start_eq, 4)

    @property
    def daily_pct(self) -> float:
        if self._day_start_eq == 0:
            return 0.0
        return round(self.daily_pnl / self._day_start_eq, 6)

    @property
    def rolling_sharpe(self) -> float:
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

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Full JSON-serializable snapshot — schema completa S37."""
        return {
            # Core
            "ts":               time.time(),
            "equity_usd":       round(self._equity, 4),
            "initial_capital":  self.initial_capital,
            "pnl_usd":          round(self._equity - self.initial_capital, 4),
            "pnl_pct":          round(
                (self._equity - self.initial_capital) / self.initial_capital, 6
            ) if self.initial_capital else 0.0,

            # Daily
            "daily_pnl":        self.daily_pnl,
            "daily_pct":        self.daily_pct,

            # Unrealized
            "unrealized_pnl":   round(self._unrealized_pnl, 4),

            # Risk
            "rolling_sharpe":   self.rolling_sharpe,
            "drawdown_current": round(self.current_drawdown, 6),
            "max_drawdown":     round(self.max_drawdown, 6),
            "exposure_usd":     round(self.total_exposure_usd, 2),

            # Trade stats
            "wins":             self._total_wins,
            "losses":           self._total_losses,
            "total_trades":     self._total_trades,
            "win_rate":         round(self.win_rate, 4),
            "avg_win_usd":      self.avg_win_usd,
            "avg_loss_usd":     self.avg_loss_usd,
            "profit_factor":    self.profit_factor,

            # Consecutive
            "max_consecutive_wins":   self._max_consecutive_wins,
            "max_consecutive_losses": self._max_consecutive_losses,
            "current_streak":         self._current_streak,

            # Fees
            "total_fees_usd":   round(self._total_fees, 4),
            "net_pnl_usd":      round(self._total_pnl - self._total_fees, 4),

            # Exposure
            "exposure_pct":     round(
                self.total_exposure_usd / self.initial_capital, 4
            ) if self.initial_capital else 0.0,
            "n_active_pairs":   sum(
                1 for ps in self._pair_stats.values() if ps.current_notional > 0
            ),

            # Per-pair (format vechi — compatibilitate backwards)
            "pairs": {k: v.to_dict() for k, v in self._pair_stats.items()},

            # Per-pair (format nou frontend — TradeBreakdown)
            "pair_breakdown": [
                ps.to_breakdown_dict()
                for ps in sorted(
                    self._pair_stats.values(),
                    key=lambda p: abs(p.net_pnl),
                    reverse=True,
                )
            ],

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

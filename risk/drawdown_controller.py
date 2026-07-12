"""
QuantLuna — Portfolio Drawdown Controller  (Sprint 10)

DD Control on three levels:

  LEVEL 1 — Pair-level soft stop
    If an individual pair exceeds max_pair_dd, force-close it.
    Rationale: extended pair drawdown suggests cointegration breakdown
    or regime shift — do not wait for mean reversion that may not come.

  LEVEL 2 — Portfolio soft limit
    If aggregate DD exceeds portfolio_soft_dd:
    - No new positions
    - Existing positions remain open (avoid forced losses)
    - LogWarning + StateBus alert

  LEVEL 3 — Portfolio hard stop (circuit breaker)
    If aggregate DD exceeds portfolio_hard_dd:
    - All positions marked for immediate close
    - Trading halted completely
    - Manual reset required (explicit safety gate)

Why three levels, not one:
  A binary circuit breaker (on/off) is too aggressive on crypto:
  intraday volatility can trigger and clear the breaker multiple times
  per day. Three levels with distinct thresholds give the system room
  to breathe at the pair level while protecting total capital.

Equity curve tracking:
  DDController maintains its own equity curve to calculate drawdown
  against the high-water mark (HWM), not initial capital.
  This is the correct metric for prop trading.

Real-world limitations:
  - Pair-level DD is calculated on open PnL (mark-to-market).
    At high slippage on exit, actual loss may exceed limits.
  - Hard stop triggered at low-liquidity hours may coincide with
    wide spreads. Consider an execution delay config for such cases.
  - DDController does not know the cause of drawdown. It may be
    cointegration breakdown or a temporary wick. Post-hoc analysis
    is mandatory before re-activation.

Changes (code review 2026-07-12):
  - Patch 3: replaced unbounded List[float] equity_curve with
    collections.deque(maxlen=1000) to prevent memory leak in
    long-running live sessions (appended on every update() call).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Deque, List, Optional

import numpy as np


class DDLevel(Enum):
    NORMAL = "NORMAL"
    SOFT_LIMIT = "SOFT_LIMIT"     # no new positions
    HARD_STOP = "HARD_STOP"       # all positions closed, trading halted


@dataclass
class DDConfig:
    pair_soft_dd: float = 0.05        # 5% DD on pair -> force-close pair
    portfolio_soft_dd: float = 0.08   # 8% portfolio DD -> no new positions
    portfolio_hard_dd: float = 0.15   # 15% portfolio DD -> full circuit breaker
    capital_usd: float = 10_000.0
    hwm_reset_on_manual_resume: bool = True  # reset HWM on manual re-activation
    equity_curve_maxlen: int = 1000   # max bars retained in equity curve deque


@dataclass
class PairDDState:
    pair_id: str
    entry_equity_snapshot: float     # equity at position open
    current_open_pnl: float = 0.0
    max_open_pnl: float = 0.0        # peak open PnL for pair-level HWM
    force_close: bool = False


@dataclass
class DDSnapshot:
    level: DDLevel
    portfolio_dd_pct: float
    portfolio_equity: float
    hwm: float
    pairs_force_close: List[str]
    notes: List[str]


class DrawdownController:
    """
    Three-level drawdown controller for multi-pair portfolios.

    Integration with LiveTrader::

        snap = dd_ctrl.update(open_pnl_per_pair)
        if snap.level == DDLevel.HARD_STOP:
            await live_trader.close_all()
        elif snap.level == DDLevel.SOFT_LIMIT:
            live_trader.block_new_entries()
        for pair_id in snap.pairs_force_close:
            await live_trader.close_pair(pair_id)
    """

    def __init__(self, cfg: Optional[DDConfig] = None) -> None:
        self.cfg = cfg or DDConfig()
        # Patch 3: bounded deque prevents unbounded memory growth.
        # Only the last value is needed for equity calculation; the deque
        # retains a window useful for debugging / dashboard display.
        self._equity_curve: Deque[float] = deque(
            [self.cfg.capital_usd],
            maxlen=self.cfg.equity_curve_maxlen,
        )
        self._hwm: float = self.cfg.capital_usd
        self._level: DDLevel = DDLevel.NORMAL
        self._pair_states: Dict[str, PairDDState] = {}
        self._hard_stop_triggered: bool = False
        self._resume_armed: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_pair(self, pair_id: str) -> None:
        """Register a newly opened pair position."""
        equity_now = self._equity_curve[-1]
        self._pair_states[pair_id] = PairDDState(
            pair_id=pair_id,
            entry_equity_snapshot=equity_now,
        )

    def close_pair(self, pair_id: str) -> Optional[PairDDState]:
        """Mark a pair as closed and return its final state."""
        return self._pair_states.pop(pair_id, None)

    def update(self, open_pnl_per_pair: Dict[str, float]) -> DDSnapshot:
        """
        Update DD state with current per-pair PnL.

        Parameters
        ----------
        open_pnl_per_pair : dict {pair_id: open_pnl_usd}
        """
        notes: List[str] = []
        pairs_force_close: List[str] = []
        cfg = self.cfg

        total_open_pnl = 0.0
        for pair_id, pnl in open_pnl_per_pair.items():
            if pair_id not in self._pair_states:
                self.open_pair(pair_id)
            state = self._pair_states[pair_id]
            state.current_open_pnl = float(pnl)
            state.max_open_pnl = max(state.max_open_pnl, float(pnl))
            total_open_pnl += float(pnl)

            # Level 1: pair-level soft stop
            pair_dd = self._pair_dd(state)
            if pair_dd >= cfg.pair_soft_dd:
                state.force_close = True
                pairs_force_close.append(pair_id)
                notes.append(
                    f"PAIR_DD: {pair_id} DD={pair_dd:.1%} >= {cfg.pair_soft_dd:.1%}"
                )

        equity = cfg.capital_usd + total_open_pnl
        self._equity_curve.append(equity)

        if equity > self._hwm:
            self._hwm = equity

        portfolio_dd = max(0.0, (self._hwm - equity) / self._hwm) if self._hwm > 0 else 0.0

        if portfolio_dd >= cfg.portfolio_hard_dd:
            self._level = DDLevel.HARD_STOP
            self._hard_stop_triggered = True
            pairs_force_close = list(self._pair_states.keys())
            notes.append(
                f"HARD_STOP: portfolio DD={portfolio_dd:.1%} >= {cfg.portfolio_hard_dd:.1%}"
            )
        elif portfolio_dd >= cfg.portfolio_soft_dd:
            self._level = DDLevel.SOFT_LIMIT
            notes.append(
                f"SOFT_LIMIT: portfolio DD={portfolio_dd:.1%} >= {cfg.portfolio_soft_dd:.1%}"
            )
        elif not self._hard_stop_triggered:
            self._level = DDLevel.NORMAL

        return DDSnapshot(
            level=self._level,
            portfolio_dd_pct=portfolio_dd,
            portfolio_equity=equity,
            hwm=self._hwm,
            pairs_force_close=list(set(pairs_force_close)),
            notes=notes,
        )

    def manual_resume(self) -> bool:
        """
        Manual re-activation after HARD_STOP.
        Requires explicit call — does not auto-reset.
        Returns True if re-activation succeeded.
        """
        if not self._hard_stop_triggered:
            return False
        self._hard_stop_triggered = False
        self._level = DDLevel.NORMAL
        if self.cfg.hwm_reset_on_manual_resume:
            self._hwm = self._equity_curve[-1]
        return True

    @property
    def level(self) -> DDLevel:
        return self._level

    @property
    def is_trading_allowed(self) -> bool:
        return self._level == DDLevel.NORMAL

    @property
    def can_open_new(self) -> bool:
        return self._level == DDLevel.NORMAL

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pair_dd(self, state: PairDDState) -> float:
        """
        Drawdown of a pair relative to its peak open PnL.
        Uses peak PnL, not zero, to avoid penalising pairs that were
        never profitable. If the pair has never been profitable,
        calculates DD relative to entry capital snapshot.
        """
        if state.max_open_pnl > 0:
            dd = (state.max_open_pnl - state.current_open_pnl) / (
                state.entry_equity_snapshot + state.max_open_pnl + 1e-9
            )
        else:
            dd = abs(state.current_open_pnl) / (state.entry_equity_snapshot + 1e-9)
        return max(0.0, float(dd))

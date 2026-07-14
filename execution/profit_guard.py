"""
execution/profit_guard.py — Real-time profit protection for open positions.

Sprint 48: Monitors open pairs-trading positions bar-by-bar and emits
close/partial-close actions when profit targets are hit.  Fixes the
"good entries → losses" problem by capturing profit BEFORE mean reversion
completes or reverses.

Protection layers (evaluated in order):
  1. Emergency stop:   close if unrealized PnL < emergency_stop_pct (-5%)
  2. Trailing stop:    close if z-score retreats from best by trailing_distance
  3. Take-profit:      close if z-score improved by tp_zscore_improvement
  4. Profit ladder:    partial closes at configurable PnL thresholds
  5. Time decay:       close if profit plateaus for N bars

Usage::

    cfg = ProfitGuardConfig()
    guard = ProfitGuard(cfg)

    # On entry:
    guard.register(GuardedPosition(
        pair="BTCUSDT/ETHUSDT", entry_zscore=-2.5, entry_spread=20.0,
        entry_prices=(50000, 2500), side="LONG_SPREAD",
        qty_y=0.01, qty_x=0.20,
    ))

    # Per bar:
    action = guard.update(
        pair="BTCUSDT/ETHUSDT", zscore=-0.9, spread=19.8,
        prices=(50100, 2495), order_manager=mgr,
    )
    if action.action == "FULL_CLOSE":
        # Exit the position immediately
        pass
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ProfitGuardConfig:
    """All profit-protection parameters — override via env vars or constructor."""

    # ── Take-profit on z-score improvement ────────────────────────────────
    tp_zscore_improvement: float = 1.0   # close when |z| improves by this much
    tp_profit_pct: float = 0.03          # close when unrealized PnL >= 3%

    # ── Profit ladder (partial closes) ────────────────────────────────────
    # Each tuple: (profit_pct, close_fraction)
    # At 2% → close 30%, at 3% → close 40% more, at 5% → close remaining 30%
    ladder_enabled: bool = True
    ladder_levels: Tuple[Tuple[float, float], ...] = (
        (0.02, 0.30),
        (0.03, 0.40),
        (0.05, 0.30),
    )

    # ── Trailing stop on z-score ──────────────────────────────────────────
    trailing_enabled: bool = True
    trailing_activation_z: float = 1.0   # arm after |z| improves by 1.0
    trailing_distance_z: float = 0.5     # close if |z| worsens by 0.5 from best

    # ── Time-based profit decay ───────────────────────────────────────────
    time_decay_enabled: bool = True
    time_decay_bars: int = 20            # if PnL doesn't improve for N bars
    time_decay_min_profit_pct: float = 0.01  # only trigger if >= 1% profit

    # ── Hard protections ──────────────────────────────────────────────────
    emergency_stop_pct: float = -0.05    # -5% hard stop
    max_hold_bars: int = 500             # never hold longer than this

    # ── Enabled ───────────────────────────────────────────────────────────
    enabled: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# Position tracking
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class GuardedPosition:
    """State for one protected position."""

    pair: str
    entry_zscore: float
    entry_spread: float
    entry_prices: Tuple[float, float]  # (price_y, price_x)
    side: str                          # "LONG_SPREAD" | "SHORT_SPREAD"
    qty_y: float
    qty_x: float

    entry_time: float = field(default_factory=time.time)
    best_zscore: float = 0.0           # closest to zero seen
    best_profit_pct: float = 0.0       # best unrealized PnL seen
    bars_held: int = 0
    partial_closes: int = 0            # number of ladder levels executed
    last_profit_update_bar: int = 0    # bar when profit last improved


@dataclass
class GuardAction:
    """Action emitted by ProfitGuard."""
    action: str = "HOLD"      # "PARTIAL_CLOSE" | "FULL_CLOSE" | "HOLD"
    close_ratio: float = 0.0  # fraction to close (0.0 – 1.0)
    reason: str = ""
    profit_pct: float = 0.0   # current profit estimate
    zscore: float = 0.0       # current z-score


# ═══════════════════════════════════════════════════════════════════════════════
# ProfitGuard
# ═══════════════════════════════════════════════════════════════════════════════


class ProfitGuard:
    """
    Monitors open positions and emits close actions when profit targets are hit.

    Designed to be called on every bar in the trading loop.  All logic is
    synchronous and fast (< 100 µs per call) — no I/O.
    """

    def __init__(
        self,
        cfg: Optional[ProfitGuardConfig] = None,
        notifier_bus=None,
    ) -> None:
        self._cfg = cfg or ProfitGuardConfig()
        self._bus = notifier_bus
        self._positions: Dict[str, GuardedPosition] = {}
        self._bar_counter: int = 0

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def active_count(self) -> int:
        return len(self._positions)

    def register(self, pos: GuardedPosition) -> None:
        """Register a position for monitoring (call on entry)."""
        pos.best_zscore = abs(pos.entry_zscore)
        pos.best_profit_pct = 0.0
        self._positions[pos.pair] = pos
        logger.info(
            "ProfitGuard: registered {} | entry_z={:.3f} side={}",
            pos.pair, pos.entry_zscore, pos.side,
        )

    def register_from_entry(
        self,
        pair: str,
        zscore: float,
        spread: float,
        prices: Tuple[float, float],
        side: str,
        qty_y: float = 0.0,
        qty_x: float = 0.0,
    ) -> None:
        """Convenience: register from entry signal parameters."""
        pos = GuardedPosition(
            pair=pair,
            entry_zscore=zscore,
            entry_spread=spread,
            entry_prices=prices,
            side=side,
            qty_y=qty_y,
            qty_x=qty_x,
        )
        self.register(pos)

    def unregister(self, pair: str) -> None:
        """Remove position (call on exit)."""
        self._positions.pop(pair, None)

    def update(
        self,
        pair: str,
        zscore: float,
        spread: float,
        prices: Tuple[float, float],
        order_manager: Optional[Any] = None,
    ) -> GuardAction:
        """
        Evaluate position and return recommended action.

        Call on EVERY bar while position is open.
        Returns HOLD if no action needed.

        Parameters
        ----------
        pair : str
            Position identifier (e.g. "BTCUSDT/ETHUSDT")
        zscore : float
            Current spread z-score
        spread : float
            Current raw spread (price_y / price_x)
        prices : (float, float)
            Current (price_y, price_x)
        order_manager : optional
            OrderManager for position size info (used for PnL calc)
        """
        self._bar_counter += 1
        pos = self._positions.get(pair)
        if pos is None:
            return GuardAction(action="HOLD", reason="not_registered")

        if not self._cfg.enabled:
            return GuardAction(action="HOLD", reason="disabled")

        pos.bars_held += 1

        # Compute unrealized PnL estimate
        profit_pct = self._estimate_profit(pos, spread, prices, order_manager)

        # Track best values
        best_z = abs(zscore)
        if best_z < abs(pos.best_zscore):
            pos.best_zscore = zscore
            pos.last_profit_update_bar = self._bar_counter
        if profit_pct > pos.best_profit_pct:
            pos.best_profit_pct = profit_pct
            pos.last_profit_update_bar = self._bar_counter

        # ── 1. Emergency stop ──────────────────────────────────────────
        if profit_pct < self._cfg.emergency_stop_pct:
            self.unregister(pair)
            return GuardAction(
                action="FULL_CLOSE", close_ratio=1.0,
                reason=f"emergency_stop ({profit_pct:.1%})",
                profit_pct=profit_pct, zscore=zscore,
            )

        # ── 2. Max hold time ───────────────────────────────────────────
        if pos.bars_held > self._cfg.max_hold_bars:
            self.unregister(pair)
            return GuardAction(
                action="FULL_CLOSE", close_ratio=1.0,
                reason=f"max_hold ({pos.bars_held} bars)",
                profit_pct=profit_pct, zscore=zscore,
            )

        # ── 3. Trailing stop on z-score ─────────────────────────────────
        if self._cfg.trailing_enabled:
            z_improvement = abs(pos.entry_zscore) - abs(zscore)
            if z_improvement >= self._cfg.trailing_activation_z:
                # Trailing stop is armed: check if z retreated from best
                z_retreat = abs(zscore) - abs(pos.best_zscore)
                if z_retreat >= self._cfg.trailing_distance_z:
                    self.unregister(pair)
                    return GuardAction(
                        action="FULL_CLOSE", close_ratio=1.0,
                        reason=(
                            f"trailing_stop (z_retreat={z_retreat:.2f}, "
                            f"best_z={pos.best_zscore:.3f})"
                        ),
                        profit_pct=profit_pct, zscore=zscore,
                    )

        # ── 4. Take-profit on z-score improvement ───────────────────────
        z_improvement = abs(pos.entry_zscore) - abs(zscore)
        if z_improvement >= self._cfg.tp_zscore_improvement and profit_pct > 0:
            self.unregister(pair)
            return GuardAction(
                action="FULL_CLOSE", close_ratio=1.0,
                reason=(
                    f"tp_zscore (improvement={z_improvement:.2f}z, "
                    f"pnl={profit_pct:.1%})"
                ),
                profit_pct=profit_pct, zscore=zscore,
            )

        # Take-profit on PnL %
        if profit_pct >= self._cfg.tp_profit_pct:
            self.unregister(pair)
            return GuardAction(
                action="FULL_CLOSE", close_ratio=1.0,
                reason=f"tp_pnl ({profit_pct:.1%})",
                profit_pct=profit_pct, zscore=zscore,
            )

        # ── 5. Profit ladder (partial closes) ──────────────────────────
        if self._cfg.ladder_enabled and self._cfg.ladder_levels:
            executed = pos.partial_closes
            if executed < len(self._cfg.ladder_levels):
                trigger_pct, close_frac = self._cfg.ladder_levels[executed]
                if profit_pct >= trigger_pct:
                    pos.partial_closes += 1
                    return GuardAction(
                        action="PARTIAL_CLOSE",
                        close_ratio=close_frac,
                        reason=(
                            f"ladder L{executed + 1} "
                            f"({profit_pct:.1%} >= {trigger_pct:.1%})"
                        ),
                        profit_pct=profit_pct, zscore=zscore,
                    )

        # ── 6. Time decay (plateauing profit) ──────────────────────────
        if self._cfg.time_decay_enabled and profit_pct >= self._cfg.time_decay_min_profit_pct:
            bars_since_improvement = self._bar_counter - pos.last_profit_update_bar
            if bars_since_improvement >= self._cfg.time_decay_bars:
                self.unregister(pair)
                return GuardAction(
                    action="FULL_CLOSE", close_ratio=1.0,
                    reason=(
                        f"time_decay (plateau {bars_since_improvement} bars, "
                        f"pnl={profit_pct:.1%})"
                    ),
                    profit_pct=profit_pct, zscore=zscore,
                )

        return GuardAction(
            action="HOLD",
            reason=f"monitoring ({pos.bars_held} bars, pnl={profit_pct:.1%})",
            profit_pct=profit_pct, zscore=zscore,
        )

    def get_active_positions(self) -> List[GuardedPosition]:
        return list(self._positions.values())

    def snapshot(self) -> dict:
        """Return state for API / logging."""
        positions = []
        for pos in self._positions.values():
            positions.append({
                "pair": pos.pair,
                "entry_zscore": pos.entry_zscore,
                "side": pos.side,
                "bars_held": pos.bars_held,
                "best_zscore": pos.best_zscore,
                "best_profit_pct": pos.best_profit_pct,
                "partial_closes": pos.partial_closes,
            })
        return {
            "active_count": len(self._positions),
            "positions": positions,
            "config": {
                "tp_zscore_improvement": self._cfg.tp_zscore_improvement,
                "tp_profit_pct": self._cfg.tp_profit_pct,
                "trailing_enabled": self._cfg.trailing_enabled,
                "ladder_enabled": self._cfg.ladder_enabled,
                "time_decay_enabled": self._cfg.time_decay_enabled,
            },
        }

    # ── Internal helpers ────────────────────────────────────────────────────

    def _estimate_profit(
        self,
        pos: GuardedPosition,
        spread: float,
        prices: Tuple[float, float],
        order_manager: Optional[Any],
    ) -> float:
        """
        Estimate unrealized PnL as a fraction.

        For pairs trading:
          LONG_SPREAD = long Y / short X
            Profit when spread narrows (spread < entry_spread)
          SHORT_SPREAD = short Y / long X
            Profit when spread widens (spread > entry_spread)
        """
        if abs(pos.entry_spread) < 1e-12:
            return 0.0

        if pos.side == "LONG_SPREAD":
            # Long Y, short X → profit when spread decreases
            pnl_pct = (pos.entry_spread - spread) / pos.entry_spread
        elif pos.side == "SHORT_SPREAD":
            # Short Y, long X → profit when spread increases
            pnl_pct = (spread - pos.entry_spread) / pos.entry_spread
        else:
            # Unknown side — use z-score direction as guess
            if pos.entry_zscore < 0:
                pnl_pct = (pos.entry_spread - spread) / pos.entry_spread
            else:
                pnl_pct = (spread - pos.entry_spread) / pos.entry_spread

        return float(pnl_pct)

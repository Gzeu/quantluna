"""
tests/test_profit_guard.py — Unit tests for ProfitGuard (S48).

Tests TP, trailing stop, profit ladder, time decay, and emergency stop.
"""
from __future__ import annotations

import numpy as np
import pytest

from execution.profit_guard import (
    GuardAction,
    GuardedPosition,
    ProfitGuard,
    ProfitGuardConfig,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def cfg():
    return ProfitGuardConfig()


@pytest.fixture
def guard(cfg):
    return ProfitGuard(cfg)


@pytest.fixture
def sample_pos():
    """A typical LONG_SPREAD position."""
    return GuardedPosition(
        pair="BTCUSDT/ETHUSDT",
        entry_zscore=-2.5,
        entry_spread=20.0,
        entry_prices=(50000.0, 2500.0),
        side="LONG_SPREAD",
        qty_y=0.01,
        qty_x=0.20,
    )


# ── Basic tests ─────────────────────────────────────────────────────────


class TestProfitGuardInit:
    def test_default_config(self):
        pg = ProfitGuard()
        assert pg.active_count == 0
        assert pg._cfg.tp_zscore_improvement == 1.0

    def test_custom_config(self):
        cfg = ProfitGuardConfig(tp_profit_pct=0.05, emergency_stop_pct=-0.08)
        pg = ProfitGuard(cfg)
        assert pg._cfg.tp_profit_pct == 0.05

    def test_disabled(self):
        cfg = ProfitGuardConfig(enabled=False)
        pg = ProfitGuard(cfg)
        pg.register_from_entry("test", -2.5, 20.0, (50000, 2500), "LONG_SPREAD")
        action = pg.update("test", -1.0, 19.5, (50100, 2495))
        assert action.action == "HOLD"
        assert "disabled" in action.reason


class TestRegistration:
    def test_register(self, guard, sample_pos):
        guard.register(sample_pos)
        assert guard.active_count == 1

    def test_register_from_entry(self, guard):
        guard.register_from_entry("test", -2.0, 20.0, (50000, 2500), "LONG_SPREAD")
        assert guard.active_count == 1

    def test_unregister(self, guard, sample_pos):
        guard.register(sample_pos)
        guard.unregister(sample_pos.pair)
        assert guard.active_count == 0

    def test_update_unknown_pair(self, guard):
        action = guard.update("unknown", 0.0, 20.0, (50000, 2500))
        assert action.action == "HOLD"
        assert "not_registered" in action.reason


# ── Take-profit tests ───────────────────────────────────────────────────


class TestTakeProfit:
    def test_tp_zscore_improvement(self, guard, sample_pos):
        """Close when |z| improves by tp_zscore_improvement (1.0)."""
        guard.register(sample_pos)
        # Entry z=-2.5, now z=-1.0: improvement = 1.5 >= 1.0
        action = guard.update(sample_pos.pair, zscore=-1.0, spread=19.5,
                              prices=(50100, 2495))
        assert action.action == "FULL_CLOSE"
        assert "tp_zscore" in action.reason

    def test_tp_not_reached(self, guard, sample_pos):
        guard.register(sample_pos)
        # Entry z=-2.5, now z=-2.0: improvement = 0.5 < 1.0
        action = guard.update(sample_pos.pair, zscore=-2.0, spread=19.9,
                              prices=(50050, 2498))
        assert action.action == "HOLD"

    def test_tp_pnl_percent(self, sample_pos):
        """Close at 3% PnL when z-score TP is disabled."""
        cfg_pnl = ProfitGuardConfig(
            tp_zscore_improvement=10.0,  # disable z-score TP
            tp_profit_pct=0.03,
        )
        guard_pnl = ProfitGuard(cfg_pnl)
        guard_pnl.register(sample_pos)
        # Spread narrows from 20.0 → 19.0: PnL = (20-19)/20 = 5%
        action = guard_pnl.update(sample_pos.pair, zscore=-1.0, spread=19.0,
                                  prices=(50500, 2450))
        assert action.action == "FULL_CLOSE"
        assert "tp_pnl" in action.reason


# ── Trailing stop tests ─────────────────────────────────────────────────


class TestTrailingStop:
    def test_trailing_stop_triggered(self, sample_pos):
        """After z improves past activation, retreat from best → close."""
        cfg_trail = ProfitGuardConfig(
            tp_zscore_improvement=10.0,   # disable z-score TP
            tp_profit_pct=10.0,            # disable PnL TP
            trailing_enabled=True,
            trailing_activation_z=0.5,     # arm after improvement of just 0.5
            trailing_distance_z=0.3,       # close if z retreats 0.3 from best
        )
        guard_trail = ProfitGuard(cfg_trail)
        guard_trail.register(sample_pos)

        # Improve to z=-1.5 (improvement = 1.0 >= 0.5, trailing armed)
        guard_trail.update(sample_pos.pair, zscore=-1.5, spread=19.5, prices=(50100, 2495))
        # Now retreat to z=-2.0 (retreat from best -1.5 to -2.0 = 0.5 >= 0.3)
        action = guard_trail.update(sample_pos.pair, zscore=-2.0, spread=19.8,
                                    prices=(50020, 2499))
        assert action.action == "FULL_CLOSE"
        assert "trailing_stop" in action.reason

    def test_trailing_not_activated(self, guard, sample_pos):
        """Without activation (improvement < 1.0), no trailing stop."""
        guard.register(sample_pos)
        guard.update(sample_pos.pair, zscore=-1.6, spread=19.7, prices=(50080, 2490))
        action = guard.update(sample_pos.pair, zscore=-2.1, spread=19.9,
                              prices=(50010, 2499))
        assert action.action == "HOLD"


# ── Profit ladder tests ─────────────────────────────────────────────────


class TestProfitLadder:
    def test_ladder_first_level(self, guard, sample_pos):
        """At 2% PnL, close 30%."""
        guard.register(sample_pos)
        # Spread 20.0 → 19.5: PnL = (20-19.5)/20 = 2.5% → triggers ladder L1
        action = guard.update(sample_pos.pair, zscore=-1.0, spread=19.5,
                              prices=(50100, 2495))
        # Note: tp_zscore triggers first because improvement=1.5 >= 1.0
        # But if PnL < tp_profit_pct (3%), only z-score TP triggers
        assert action.action in ("FULL_CLOSE", "PARTIAL_CLOSE")

    def test_ladder_progression(self, guard, sample_pos):
        """Multiple ladder levels trigger sequentially."""
        cfg_ladder = ProfitGuardConfig(
            tp_zscore_improvement=10.0,  # disable z-score TP
            tp_profit_pct=10.0,          # disable PnL TP
        )
        guard_ladder = ProfitGuard(cfg_ladder)
        guard_ladder.register(sample_pos)

        # L1: 2% → close 30%
        a1 = guard_ladder.update(sample_pos.pair, zscore=-1.8, spread=19.5,
                                 prices=(50100, 2495))
        assert a1.action == "PARTIAL_CLOSE"
        assert a1.close_ratio == 0.30

        # L2: 3% → close 40% more
        a2 = guard_ladder.update(sample_pos.pair, zscore=-1.8, spread=19.2,
                                 prices=(50200, 2490))
        assert a2.action == "PARTIAL_CLOSE"
        assert a2.close_ratio == 0.40


# ── Emergency stop tests ────────────────────────────────────────────────


class TestEmergencyStop:
    def test_emergency_stop(self, guard, sample_pos):
        """Close at -5% unrealized PnL."""
        guard.register(sample_pos)
        # Spread 20.0 → 21.2: PnL = (20-21.2)/20 = -6%
        action = guard.update(sample_pos.pair, zscore=-3.5, spread=21.2,
                              prices=(49000, 2530))
        assert action.action == "FULL_CLOSE"
        assert "emergency_stop" in action.reason


# ── Time decay tests ────────────────────────────────────────────────────


class TestTimeDecay:
    def test_time_decay_plateau(self, guard, sample_pos):
        """If profit plateaus for 20 bars, take it."""
        cfg_td = ProfitGuardConfig(
            tp_zscore_improvement=10.0,  # disable z-score TP
            tp_profit_pct=10.0,          # disable PnL TP
            trailing_enabled=False,       # disable trailing
            ladder_enabled=False,         # disable ladder
            time_decay_enabled=True,
            time_decay_bars=5,            # speed up for test
            time_decay_min_profit_pct=0.005,
        )
        guard_td = ProfitGuard(cfg_td)
        pos = GuardedPosition(
            pair="test", entry_zscore=-2.5, entry_spread=20.0,
            entry_prices=(50000, 2500), side="LONG_SPREAD",
            qty_y=0.01, qty_x=0.20,
        )
        guard_td.register(pos)

        # Run 6 bars at same spread (profit plateaus)
        for i in range(6):
            action = guard_td.update(
                "test", zscore=-1.5, spread=19.8,
                prices=(50050, 2495),
            )
            if i < 5:
                assert action.action == "HOLD", f"bar {i}: {action.reason}"
            else:
                assert action.action == "FULL_CLOSE", f"bar {i}: {action.reason}"
                assert "time_decay" in action.reason


# ── Snapshot tests ──────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_empty(self, guard):
        snap = guard.snapshot()
        assert snap["active_count"] == 0
        assert "config" in snap

    def test_snapshot_with_positions(self, guard, sample_pos):
        guard.register(sample_pos)
        snap = guard.snapshot()
        assert snap["active_count"] == 1
        assert len(snap["positions"]) == 1


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_zero_spread(self, guard, sample_pos):
        guard.register(sample_pos)
        guard._cfg.emergency_stop_pct = -0.99
        action = guard.update(sample_pos.pair, zscore=0.0, spread=0.0001,
                              prices=(50000, 2500))
        assert action.action in ("HOLD", "FULL_CLOSE")

    def test_extreme_zscore(self, guard, sample_pos):
        guard.register(sample_pos)
        action = guard.update(sample_pos.pair, zscore=-10.0, spread=25.0,
                              prices=(48000, 2600))
        # Either emergency stop or trailing — but should not crash
        assert action.action in ("HOLD", "FULL_CLOSE")

    def test_max_hold_bars(self, guard, sample_pos):
        guard._cfg.max_hold_bars = 3
        guard.register(sample_pos)
        guard.update(sample_pos.pair, zscore=-2.0, spread=19.9, prices=(50050, 2498))
        guard.update(sample_pos.pair, zscore=-2.0, spread=19.9, prices=(50050, 2498))
        guard.update(sample_pos.pair, zscore=-2.0, spread=19.9, prices=(50050, 2498))
        action = guard.update(sample_pos.pair, zscore=-2.0, spread=19.9, prices=(50050, 2498))
        assert action.action == "FULL_CLOSE"
        assert "max_hold" in action.reason

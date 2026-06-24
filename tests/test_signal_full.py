"""
Tests for strategy/signal.py v2 -- Sprint 3
Covers: time-stop, funding gate, cooldown, uncertainty gate,
        breakdown regime block, batch, live, reset.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from typing import Optional

import numpy as np
import pandas as pd
import pytest

from strategy.signal import Signal, SignalGenerator, TradeSignal
from config.settings import SignalConfig


# --- Helpers ------------------------------------------------------------------

def _cfg(entry=2.0, exit_z=0.5, stop=3.5, max_unc=0.5) -> SignalConfig:
    cfg = SignalConfig()
    cfg.zscore_entry = entry
    cfg.zscore_exit  = exit_z
    cfg.zscore_stop  = stop
    cfg.max_uncertainty = max_unc
    return cfg


def _mock_engine(zscores, betas=None, alphas=None, is_warm=True, uncertainties=None):
    """Return a mock SpreadEngine whose update_one() replays provided sequences."""
    n = len(zscores)
    betas       = betas       or [1.2] * n
    alphas      = alphas      or [0.0] * n
    uncertainties = uncertainties or [0.1] * n
    call_idx = {"i": 0}

    def _update_one(y, x, ts=None):
        i = call_idx["i"]
        state = {
            "zscore":    zscores[i],
            "beta":      betas[i],
            "alpha":     alphas[i],
            "spread":    float(y - betas[i] * x),
            "uncertainty": uncertainties[i],
            "kalman_gain": 0.05,
            "is_warm":   is_warm if not callable(is_warm) else is_warm(i),
            "half_life_hours": 24.0,
            "spread_std": 100.0,
            "spread_mean": 0.0,
        }
        call_idx["i"] = min(i + 1, n - 1)
        return state

    eng = MagicMock()
    eng.update_one.side_effect = _update_one
    return eng


def _make_spread_df(zscores, is_warm=True, p_beta=0.01):
    """Make a minimal spread DataFrame for generate_batch()."""
    warm = is_warm if not isinstance(is_warm, list) else is_warm
    return pd.DataFrame({
        "zscore":     zscores,
        "P_beta":     [p_beta] * len(zscores),
        "half_life_hours": [24.0] * len(zscores),
        "spread":     [0.0] * len(zscores),
        "spread_std": [100.0] * len(zscores),
        "spread_mean": [0.0] * len(zscores),
        "is_warm":    [warm] * len(zscores) if not isinstance(warm, list) else warm,
    })


# --- Signal.EXIT when warming up ----------------------------------------------

class TestWarmUpGuard:
    def test_live_cold_filter_returns_exit(self):
        eng = _mock_engine([2.5], is_warm=False)
        gen = SignalGenerator(eng, _cfg())
        sig = gen.generate_live(100.0, 80.0)
        assert sig.signal == Signal.EXIT
        assert sig.reason == "warming_up"

    def test_batch_cold_rows_are_exit(self):
        df = _make_spread_df([2.5, 3.0], is_warm=[False, False])
        gen = SignalGenerator(MagicMock(), _cfg())
        out = gen.generate_batch(df)
        assert (out["signal"] == int(Signal.EXIT)).all()


# --- Uncertainty gate ---------------------------------------------------------

class TestUncertaintyGate:
    def test_high_uncertainty_blocks_entry(self):
        eng = _mock_engine([2.5], uncertainties=[0.8])
        gen = SignalGenerator(eng, _cfg(max_unc=0.5))
        sig = gen.generate_live(100.0, 80.0)
        assert sig.signal == Signal.EXIT
        assert sig.reason == "high_uncertainty"

    def test_low_uncertainty_allows_entry(self):
        eng = _mock_engine([2.5], uncertainties=[0.1])
        gen = SignalGenerator(eng, _cfg(max_unc=0.5))
        sig = gen.generate_live(100.0, 80.0)
        assert sig.signal == Signal.LONG_SPREAD


# --- Entry signals ------------------------------------------------------------

class TestEntrySignals:
    def test_long_spread_on_low_zscore(self):
        eng = _mock_engine([-2.5])
        gen = SignalGenerator(eng, _cfg(entry=2.0))
        sig = gen.generate_live(100.0, 80.0)
        assert sig.signal == Signal.LONG_SPREAD

    def test_short_spread_on_high_zscore(self):
        eng = _mock_engine([2.5])
        gen = SignalGenerator(eng, _cfg(entry=2.0))
        sig = gen.generate_live(100.0, 80.0)
        assert sig.signal == Signal.SHORT_SPREAD

    def test_no_signal_inside_band(self):
        eng = _mock_engine([1.0])
        gen = SignalGenerator(eng, _cfg(entry=2.0))
        sig = gen.generate_live(100.0, 80.0)
        assert sig.signal == Signal.EXIT


# --- Hard stop ----------------------------------------------------------------

class TestHardStop:
    def test_hard_stop_on_extreme_zscore(self):
        # Enter at z=-2.5, then z=4.0 -> hard stop
        eng = _mock_engine([-2.5, 4.0])
        gen = SignalGenerator(eng, _cfg(stop=3.5))
        gen.generate_live(100.0, 80.0)  # enter
        sig = gen.generate_live(100.0, 80.0)  # stop
        assert sig.signal == Signal.EXIT
        assert sig.reason == "hard_stop"


# --- Time-stop ----------------------------------------------------------------

class TestTimeStop:
    def test_time_stop_after_2x_half_life(self):
        # half_life=4 bars -> time_stop at bars_in_trade > 8
        n = 15
        zscores = [-2.5] + [-1.5] * (n - 1)  # enters, then holds without exit
        eng = _mock_engine(zscores)
        # Override half_life to 4h so time_stop triggers early
        def patched_update(y, x, ts=None):
            i = eng._call_count
            eng._call_count += 1
            return {
                "zscore": zscores[min(i, n-1)],
                "beta": 1.2, "alpha": 0.0,
                "spread": 0.0, "uncertainty": 0.1,
                "kalman_gain": 0.05, "is_warm": True,
                "half_life_hours": 4.0,
                "spread_std": 100.0, "spread_mean": 0.0,
            }
        eng._call_count = 0
        eng.update_one.side_effect = patched_update

        gen = SignalGenerator(eng, _cfg())
        signals = [gen.generate_live(100.0, 80.0) for _ in range(n)]
        reasons = [s.reason for s in signals]
        assert "time_stop" in reasons, f"Expected time_stop in {reasons}"


# --- Mean reversion exit ------------------------------------------------------

class TestMeanReversionExit:
    def test_exit_when_zscore_returns_to_zero(self):
        eng = _mock_engine([-2.5, -1.5, -0.5, 0.1])
        gen = SignalGenerator(eng, _cfg(exit_z=0.5))
        gen.generate_live(100.0, 80.0)  # enter
        gen.generate_live(100.0, 80.0)  # hold
        gen.generate_live(100.0, 80.0)  # hold
        sig = gen.generate_live(100.0, 80.0)  # exit
        assert sig.signal == Signal.EXIT
        assert sig.reason == "mean_reversion"


# --- Cooldown -----------------------------------------------------------------

class TestCooldown:
    def test_no_reentry_during_cooldown(self):
        # enter, exit (mean reversion), then immediate re-entry signal blocked
        zs = [-2.5, 0.1, -2.5, -2.5, -2.5]  # enter, exit, try re-entry x3
        eng = _mock_engine(zs)
        gen = SignalGenerator(eng, _cfg(), cooldown_bars=3)
        sigs = [gen.generate_live(100.0, 80.0) for _ in range(5)]
        # After exit at bar 1, bars 2-4 should be EXIT (cooldown)
        assert sigs[2].reason == "cooldown"
        assert sigs[3].reason == "cooldown"


# --- Funding gate -------------------------------------------------------------

class TestFundingGate:
    def test_entry_blocked_by_high_funding(self):
        eng = _mock_engine([-2.5])
        gen = SignalGenerator(eng, _cfg(), funding_threshold_annual=0.05)
        sig = gen.generate_live(100.0, 80.0, funding_annual=0.10)
        assert sig.signal == Signal.EXIT
        assert sig.reason == "funding_gate"

    def test_entry_allowed_when_funding_low(self):
        eng = _mock_engine([-2.5])
        gen = SignalGenerator(eng, _cfg(), funding_threshold_annual=0.05)
        sig = gen.generate_live(100.0, 80.0, funding_annual=0.01)
        assert sig.signal == Signal.LONG_SPREAD


# --- Regime breakdown block ---------------------------------------------------

class TestRegimeBlock:
    def test_breakdown_regime_blocks_entry(self):
        eng = _mock_engine([-2.5])
        gen = SignalGenerator(eng, _cfg())
        sig = gen.generate_live(100.0, 80.0, regime_multiplier=0.0)
        assert sig.signal == Signal.EXIT
        assert sig.reason == "regime_breakdown"


# --- Batch generation ---------------------------------------------------------

class TestBatchGeneration:
    def test_batch_returns_signal_column(self):
        df = _make_spread_df([0.0, -2.5, -2.0, -1.0, 0.1, 0.2])
        gen = SignalGenerator(MagicMock(), _cfg())
        out = gen.generate_batch(df)
        assert "signal" in out.columns
        assert "confidence" in out.columns
        assert "reason" in out.columns

    def test_batch_preserves_row_count(self):
        df = _make_spread_df(list(np.linspace(-3, 3, 50)))
        gen = SignalGenerator(MagicMock(), _cfg())
        out = gen.generate_batch(df)
        assert len(out) == 50

    def test_reset_clears_state(self):
        df = _make_spread_df([-2.5] * 10)
        gen = SignalGenerator(MagicMock(), _cfg())
        gen.generate_batch(df)
        gen.reset()
        assert not gen._in_trade
        assert gen._bars_in_trade == 0
        assert gen._cooldown_remaining == 0

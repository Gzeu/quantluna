"""
tests/test_signal_v4.py  —  Tests pentru SignalGenerator v4 (P0+P1 features)

Acopera:
  - P0-1: Volatility-adjusted threshold
  - P0-2: Delta-z momentum filter
  - P1-1: Dynamic cooldown
  - P1-2: Partial exit at z=0 (one-shot per trade)
  - P1-3: Cointegration validity gate
  - Backward compatibility: toate feature-urile off = comportament v3
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from config.settings import SignalConfig
from strategy.signal import Signal, SignalGenerator, TradeSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(warm: bool = True, z: float = 0.0, beta: float = 1.0,
                 half_life: float = 24.0, uncertainty: float = 0.01):
    state = {
        'zscore': z, 'beta': beta, 'alpha': 0.0, 'spread': 0.0,
        'uncertainty': uncertainty, 'kalman_gain': 0.05,
        'half_life_hours': half_life, 'spread_std': 0.1,
        'spread_mean': 0.0, 'is_warm': warm, 'P_beta': uncertainty ** 2,
    }
    engine = MagicMock()
    engine.update_one.return_value = state
    return engine, state


def _cfg_all_on(**overrides) -> SignalConfig:
    cfg = SignalConfig(
        zscore_entry=2.0, zscore_exit=0.5, zscore_stop=3.5,
        vol_adj_enabled=True, vol_adj_factor=0.40,
        vol_adj_lookback=10, vol_adj_max_multiplier=1.6,
        dz_filter_enabled=True, dz_lookback=3, dz_block_ratio=0.25,
        dynamic_cooldown_enabled=True, cooldown_min=2,
        cooldown_hl_factor=0.5, cooldown_max=20,
        partial_exit_enabled=True, partial_exit_zscore=0.0,
        partial_exit_pct=0.50,
        max_uncertainty=0.5,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _cfg_all_off() -> SignalConfig:
    return SignalConfig(
        zscore_entry=2.0, zscore_exit=0.5, zscore_stop=3.5,
        vol_adj_enabled=False,
        dz_filter_enabled=False,
        dynamic_cooldown_enabled=False,
        partial_exit_enabled=False,
        max_uncertainty=0.5,
    )


# ---------------------------------------------------------------------------
# P0-1: Volatility-adjusted threshold
# ---------------------------------------------------------------------------

class TestVolAdjThreshold:

    def test_threshold_increases_with_vol_rank(self):
        engine, _ = _make_engine(z=2.1, warm=True)
        cfg = _cfg_all_on(vol_adj_factor=0.4, vol_adj_max_multiplier=1.6)
        gen = SignalGenerator(engine, cfg)
        gen._vol_buffer.extend([0.001] * 9)
        gen._vol_buffer.append(0.1)
        gen._last_spread_for_vol = 1.0

        vol_rank = gen._compute_vol_rank()
        threshold = gen._effective_entry_threshold(vol_rank)

        assert vol_rank > 0.5, f"vol_rank asteptat > 0.5, got {vol_rank}"
        assert threshold > 2.0, f"threshold ajustat asteptat > 2.0, got {threshold}"
        assert threshold <= 2.0 * 1.6, "threshold nu trebuie sa depaseasca cap"

    def test_threshold_not_adjusted_without_buffer(self):
        engine, _ = _make_engine()
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg)
        assert gen._compute_vol_rank() == 0.0
        assert gen._effective_entry_threshold(0.0) == pytest.approx(2.0)

    def test_vol_adj_disabled_returns_base(self):
        engine, _ = _make_engine()
        cfg = _cfg_all_off()
        gen = SignalGenerator(engine, cfg)
        gen._vol_buffer.extend([0.1] * 10)
        assert gen._effective_entry_threshold(0.9) == pytest.approx(2.0)

    def test_entry_blocked_by_high_threshold(self):
        engine, _ = _make_engine(z=2.2, warm=True)
        cfg = _cfg_all_on(vol_adj_factor=0.6, vol_adj_lookback=10)
        gen = SignalGenerator(engine, cfg)
        gen._vol_buffer.extend([0.0001] * 9)
        gen._vol_buffer.append(1.0)
        gen._last_spread_for_vol = 1.0
        gen._zscore_buffer.extend([2.2, 2.2, 2.2, 2.2])

        threshold = gen._effective_entry_threshold(gen._compute_vol_rank())
        if threshold > 2.2:
            sig = gen.generate_live(y=1.0, x=1.0)
            assert sig.signal == Signal.EXIT


# ---------------------------------------------------------------------------
# P0-2: Delta-z momentum filter
# ---------------------------------------------------------------------------

class TestDeltaZFilter:

    def test_blocks_when_spread_accelerating(self):
        engine, _ = _make_engine(z=2.5)
        cfg = _cfg_all_on(dz_block_ratio=0.25, dz_lookback=3)
        gen = SignalGenerator(engine, cfg)
        gen._zscore_buffer.extend([1.0, 1.8, 2.3, 2.5])
        assert gen._is_dz_blocked(2.5) is True

    def test_allows_when_spread_decelerating(self):
        engine, _ = _make_engine(z=2.5)
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg)
        gen._zscore_buffer.extend([2.8, 2.7, 2.6, 2.5])
        assert gen._is_dz_blocked(2.5) is False

    def test_allows_when_dz_small(self):
        engine, _ = _make_engine(z=2.5)
        cfg = _cfg_all_on(dz_block_ratio=0.25)
        gen = SignalGenerator(engine, cfg)
        gen._zscore_buffer.extend([2.3, 2.4, 2.45, 2.5])
        assert gen._is_dz_blocked(2.5) is False

    def test_disabled_never_blocks(self):
        engine, _ = _make_engine(z=2.5)
        cfg = _cfg_all_off()
        gen = SignalGenerator(engine, cfg)
        gen._zscore_buffer.extend([1.0, 1.8, 2.3, 2.5])
        assert gen._is_dz_blocked(2.5) is False

    def test_not_blocked_with_empty_buffer(self):
        engine, _ = _make_engine(z=2.5)
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg)
        assert gen._is_dz_blocked(2.5) is False


# ---------------------------------------------------------------------------
# P1-1: Dynamic cooldown
# ---------------------------------------------------------------------------

class TestDynamicCooldown:

    def test_cooldown_scales_with_half_life(self):
        engine, _ = _make_engine()
        cfg = _cfg_all_on(cooldown_hl_factor=0.5, cooldown_min=2, cooldown_max=20)
        gen = SignalGenerator(engine, cfg)
        assert gen._dynamic_cooldown(4.0)  == 2
        assert gen._dynamic_cooldown(10.0) == 5
        assert gen._dynamic_cooldown(48.0) == 20

    def test_cooldown_fallback_on_nan_half_life(self):
        engine, _ = _make_engine()
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg, cooldown_bars=3)
        assert gen._dynamic_cooldown(float('nan')) == 3

    def test_cooldown_disabled_uses_base(self):
        engine, _ = _make_engine()
        cfg = _cfg_all_off()
        gen = SignalGenerator(engine, cfg, cooldown_bars=5)
        assert gen._dynamic_cooldown(12.0) == 5


# ---------------------------------------------------------------------------
# P1-2: Partial exit at z=0
# ---------------------------------------------------------------------------

class TestPartialExit:

    def _enter(self, gen: SignalGenerator):
        gen._in_trade = True
        gen._current_signal = Signal.LONG_SPREAD
        gen._bars_in_trade = 3
        gen._partial_exit_done = False
        gen._entry_side = 1

    def test_partial_exit_emitted_at_z_zero(self):
        engine, _ = _make_engine(z=0.0, warm=True)
        cfg = _cfg_all_on(partial_exit_zscore=0.0, partial_exit_pct=0.50)
        gen = SignalGenerator(engine, cfg)
        self._enter(gen)
        gen._zscore_buffer.extend([-2.5, -1.5, -0.5, 0.0])

        sig, conf, reason, meta = gen._compute_signal(
            z=0.0, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
        )
        assert sig == Signal.PARTIAL_EXIT
        assert meta['partial_close_pct'] == pytest.approx(0.50)
        assert reason == 'partial_exit_z0'

    def test_partial_exit_only_once_per_trade(self):
        engine, _ = _make_engine(z=0.0, warm=True)
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg)
        self._enter(gen)
        gen._partial_exit_done = True

        sig, _, reason, _ = gen._compute_signal(
            z=0.0, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
        )
        assert sig != Signal.PARTIAL_EXIT

    def test_partial_exit_resets_on_exit(self):
        engine, _ = _make_engine(z=0.0, warm=True)
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg)
        gen._in_trade = True
        gen._partial_exit_done = True
        gen._entry_side = 1
        gen._exit("mean_reversion", half_life=24.0)
        assert gen._partial_exit_done is False
        assert gen._entry_side == 0

    def test_partial_exit_disabled(self):
        engine, _ = _make_engine(z=0.0, warm=True)
        cfg = _cfg_all_on(partial_exit_enabled=False)
        gen = SignalGenerator(engine, cfg)
        gen._in_trade = True
        gen._current_signal = Signal.LONG_SPREAD
        gen._entry_side = 1
        gen._partial_exit_done = False

        sig, _, reason, _ = gen._compute_signal(
            z=0.0, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
        )
        assert sig != Signal.PARTIAL_EXIT

    def test_short_spread_partial_exit(self):
        engine, _ = _make_engine(z=0.0, warm=True)
        cfg = _cfg_all_on(partial_exit_zscore=0.0)
        gen = SignalGenerator(engine, cfg)
        gen._in_trade = True
        gen._current_signal = Signal.SHORT_SPREAD
        gen._bars_in_trade = 3
        gen._partial_exit_done = False
        gen._entry_side = -1

        sig, _, reason, meta = gen._compute_signal(
            z=0.0, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
        )
        assert sig == Signal.PARTIAL_EXIT
        assert meta['partial_close_pct'] == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# P1-3: Cointegration validity gate
# ---------------------------------------------------------------------------

class TestCointValidGate:

    def test_blocks_entry_when_invalid(self):
        engine, _ = _make_engine(z=2.5, warm=True)
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg)

        sig, _, reason, _ = gen._compute_signal(
            z=2.5, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
            coint_valid=False,
        )
        assert sig == Signal.EXIT
        assert reason == 'stale_pair'

    def test_hold_when_in_trade_and_invalid(self):
        engine, _ = _make_engine(z=1.5, warm=True)
        cfg = _cfg_all_on()
        gen = SignalGenerator(engine, cfg)
        gen._in_trade = True
        gen._current_signal = Signal.LONG_SPREAD
        gen._bars_in_trade = 5
        gen._entry_side = 1
        gen._partial_exit_done = True

        sig, _, reason, _ = gen._compute_signal(
            z=1.5, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
            coint_valid=False,
        )
        assert sig == Signal.LONG_SPREAD
        assert reason == 'hold'

    def test_valid_coint_allows_entry(self):
        engine, _ = _make_engine(z=2.5, warm=True)
        cfg = _cfg_all_on(dz_filter_enabled=False, vol_adj_enabled=False)
        gen = SignalGenerator(engine, cfg)

        sig, _, _, _ = gen._compute_signal(
            z=2.5, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
            coint_valid=True,
        )
        assert sig == Signal.SHORT_SPREAD


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:

    def test_all_features_off_behaves_like_v3(self):
        engine, _ = _make_engine(z=2.5, warm=True)
        cfg = _cfg_all_off()
        gen = SignalGenerator(engine, cfg, cooldown_bars=3)

        sig, conf, reason, meta = gen._compute_signal(
            z=2.5, uncertainty=0.01, half_life=24.0,
            funding_annual=0.0, regime_multiplier=1.0,
        )
        assert sig == Signal.SHORT_SPREAD
        assert meta['effective_threshold'] == pytest.approx(2.0)
        assert meta['dz_blocked'] is False

    def test_trade_signal_v4_fields(self):
        ts = TradeSignal(
            signal=Signal.SHORT_SPREAD, zscore=2.5, beta=1.0, alpha=0.0,
            spread=0.1, uncertainty=0.01, kalman_gain=0.05,
            half_life_hours=24.0, confidence=0.8,
            effective_threshold=2.3, vol_rank=0.6, dz_blocked=False,
        )
        d = ts.as_dict()
        for field in ('effective_threshold', 'vol_rank', 'dz_blocked',
                      'partial_close_pct', 'coint_valid'):
            assert field in d, f"TradeSignal.as_dict() lipseste camp: {field}"

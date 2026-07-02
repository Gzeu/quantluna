"""
Sprint 16 tests:
  - MultiTimeframeConfirmation
  - VolatilityRegime
  - OKXOrderRouter (import + interface only, no live API calls)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# MultiTimeframeConfirmation tests
# ---------------------------------------------------------------------------

class TestMTFConfig:
    def test_import(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        cfg = MTFConfig(htf_resample="4h", htf_zscore_min=0.5)
        assert cfg.htf_resample == "4h"
        assert cfg.htf_zscore_min == 0.5

    def test_defaults(self):
        from strategy.multi_timeframe import MTFConfig
        cfg = MTFConfig()
        assert cfg.htf_zscore_window == 20
        assert cfg.require_htf_alignment is True
        assert cfg.htf_neutral_band > 0


class TestMTFConfirmation:
    def test_neutral_band_always_passes(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        mtf = MultiTimeframeConfirmation(MTFConfig(htf_neutral_band=0.5))
        # HTF in neutral band → always pass
        assert mtf.confirm(ltf_zscore=2.5, htf_zscore=0.2) is True
        assert mtf.confirm(ltf_zscore=-2.5, htf_zscore=-0.1) is True

    def test_aligned_signals_pass(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        mtf = MultiTimeframeConfirmation(MTFConfig(htf_neutral_band=0.1, htf_zscore_min=0.5))
        # Both positive → aligned
        assert mtf.confirm(ltf_zscore=2.2, htf_zscore=1.5) is True
        # Both negative → aligned
        assert mtf.confirm(ltf_zscore=-2.2, htf_zscore=-1.5) is True

    def test_misaligned_signals_block(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        mtf = MultiTimeframeConfirmation(MTFConfig(htf_neutral_band=0.1, htf_zscore_min=0.5))
        # LTF positive, HTF negative → misaligned
        assert mtf.confirm(ltf_zscore=2.2, htf_zscore=-1.5) is False
        assert mtf.confirm(ltf_zscore=-2.2, htf_zscore=1.5) is False

    def test_htf_below_minimum_blocks(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        mtf = MultiTimeframeConfirmation(MTFConfig(htf_neutral_band=0.1, htf_zscore_min=1.0))
        # HTF has signal but below minimum AND outside neutral band
        assert mtf.confirm(ltf_zscore=2.2, htf_zscore=0.6) is False

    def test_alignment_disabled_always_passes(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        mtf = MultiTimeframeConfirmation(
            MTFConfig(require_htf_alignment=False, htf_zscore_min=0.5, htf_neutral_band=0.1)
        )
        # Even misaligned, passes when require_htf_alignment=False
        assert mtf.confirm(ltf_zscore=2.2, htf_zscore=-2.0) is True

    def test_build_htf_zscore(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        # Build synthetic 1h data
        idx = pd.date_range("2025-01-01", periods=200, freq="1h")
        rng = np.random.default_rng(42)
        prices = np.cumsum(rng.normal(0, 1, 200)) + 100
        df = pd.DataFrame({"spread": prices}, index=idx)

        mtf = MultiTimeframeConfirmation(MTFConfig(htf_resample="4h", htf_zscore_window=10))
        z_htf = mtf.build_htf_zscore(df)
        assert isinstance(z_htf, pd.Series)
        assert len(z_htf) > 0
        assert not z_htf.isnull().all()

    def test_batch_confirm_returns_bool_series(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        idx = pd.date_range("2025-01-01", periods=300, freq="1h")
        rng = np.random.default_rng(99)
        spread = np.cumsum(rng.normal(0, 1, 300)) + 100
        zscore = rng.normal(0, 2, 300)
        df = pd.DataFrame({"spread": spread, "zscore": zscore}, index=idx)

        mtf = MultiTimeframeConfirmation(MTFConfig(htf_resample="4h", htf_zscore_window=10))
        confirmed = mtf.batch_confirm(df)
        assert isinstance(confirmed, pd.Series)
        assert confirmed.dtype == bool
        assert len(confirmed) == len(df)

    def test_missing_spread_col_raises(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        idx = pd.date_range("2025-01-01", periods=50, freq="1h")
        df = pd.DataFrame({"close": np.ones(50)}, index=idx)
        mtf = MultiTimeframeConfirmation(MTFConfig())
        with pytest.raises(ValueError):
            mtf.build_htf_zscore(df)

    def test_confirm_from_df(self):
        from strategy.multi_timeframe import MultiTimeframeConfirmation, MTFConfig
        idx = pd.date_range("2025-01-01", periods=200, freq="1h")
        rng = np.random.default_rng(7)
        df = pd.DataFrame(
            {"spread": np.cumsum(rng.normal(0, 1, 200)) + 100},
            index=idx
        )
        mtf = MultiTimeframeConfirmation(MTFConfig(htf_resample="4h", htf_zscore_window=10))
        result = mtf.confirm_from_df(
            ltf_zscore=2.0,
            ltf_df=df,
            current_ts=idx[-1],
        )
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# VolatilityRegime tests
# ---------------------------------------------------------------------------

class TestVolRegimeConfig:
    def test_import_and_defaults(self):
        from core.volatility_regime import VolRegimeConfig, RegimeLabel
        cfg = VolRegimeConfig()
        assert cfg.lookback == 100
        assert cfg.low_pct < cfg.high_pct < cfg.extreme_pct
        assert cfg.size_mult_extreme == 0.0
        assert cfg.block_entry_regime == RegimeLabel.EXTREME


class TestVolatilityRegime:
    def test_regime_starts_normal(self):
        from core.volatility_regime import VolatilityRegime, RegimeLabel
        vr = VolatilityRegime()
        assert vr.current_regime == RegimeLabel.NORMAL

    def test_insufficient_data_stays_normal(self):
        from core.volatility_regime import VolatilityRegime, RegimeLabel
        vr = VolatilityRegime()
        for _ in range(5):
            vr.update(0.001)
        assert vr.current_regime == RegimeLabel.NORMAL

    def test_extreme_vol_triggers_extreme_regime(self):
        from core.volatility_regime import VolatilityRegime, VolRegimeConfig, RegimeLabel
        cfg = VolRegimeConfig(lookback=50, extreme_pct=95.0, hysteresis_bars=1)
        vr = VolatilityRegime(cfg)
        # Populate buffer with small values
        for _ in range(60):
            vr.update(0.001)
        # Now inject large spikes
        for _ in range(5):
            vr.update(1.0)
        assert vr.current_regime == RegimeLabel.EXTREME

    def test_low_vol_triggers_low_regime(self):
        from core.volatility_regime import VolatilityRegime, VolRegimeConfig, RegimeLabel
        cfg = VolRegimeConfig(lookback=50, low_pct=25.0, hysteresis_bars=1)
        vr = VolatilityRegime(cfg)
        # Populate with large values
        for _ in range(60):
            vr.update(0.5)
        # Now inject tiny values
        for _ in range(5):
            vr.update(0.0001)
        assert vr.current_regime == RegimeLabel.LOW

    def test_size_multiplier_decreases_in_high_vol(self):
        from core.volatility_regime import VolatilityRegime, VolRegimeConfig, RegimeLabel
        cfg = VolRegimeConfig(
            lookback=50, high_pct=75.0, hysteresis_bars=1,
            size_mult_normal=1.0, size_mult_high=0.6
        )
        vr = VolatilityRegime(cfg)
        for _ in range(50):
            vr.update(0.001)
        mult_normal = vr.size_multiplier

        # Force high regime
        for _ in range(10):
            vr.update(0.9)
        mult_high = vr.size_multiplier

        assert mult_high <= mult_normal

    def test_extreme_blocks_entry(self):
        from core.volatility_regime import VolatilityRegime, VolRegimeConfig, RegimeLabel
        cfg = VolRegimeConfig(
            lookback=50, extreme_pct=95.0, hysteresis_bars=1,
            block_entry_regime=RegimeLabel.EXTREME
        )
        vr = VolatilityRegime(cfg)
        for _ in range(50):
            vr.update(0.001)
        for _ in range(5):
            vr.update(1.0)
        assert vr.current_regime == RegimeLabel.EXTREME
        assert vr.entry_allowed is False

    def test_normal_allows_entry(self):
        from core.volatility_regime import VolatilityRegime, RegimeLabel
        vr = VolatilityRegime()
        assert vr.entry_allowed is True

    def test_percentile_in_valid_range(self):
        from core.volatility_regime import VolatilityRegime
        vr = VolatilityRegime()
        for v in [0.001, 0.002, 0.005, 0.01, 0.05, 0.1, 0.5, 0.001, 0.002, 0.003, 0.004]:
            vr.update(v)
        assert 0.0 <= vr.percentile <= 100.0

    def test_as_dict_keys(self):
        from core.volatility_regime import VolatilityRegime
        vr = VolatilityRegime()
        d = vr.as_dict()
        assert set(d.keys()) == {"regime", "percentile", "size_multiplier", "entry_allowed", "buffer_len"}

    def test_reset_clears_state(self):
        from core.volatility_regime import VolatilityRegime, RegimeLabel
        vr = VolatilityRegime()
        for _ in range(20):
            vr.update(0.01)
        vr.reset()
        assert vr.current_regime == RegimeLabel.NORMAL
        assert vr.percentile == 50.0

    def test_update_from_prices(self):
        from core.volatility_regime import VolatilityRegime
        vr = VolatilityRegime()
        prev = 100.0
        for i in range(30):
            y = 100.0 + np.sin(i * 0.1)
            x = 50.0
            beta = 2.0
            vr.update_from_prices(y, x, beta, prev_spread=prev)
            prev = y - beta * x
        assert 0.0 <= vr.percentile <= 100.0

    def test_hysteresis_delays_regime_change(self):
        from core.volatility_regime import VolatilityRegime, VolRegimeConfig, RegimeLabel
        cfg = VolRegimeConfig(lookback=50, extreme_pct=90.0, hysteresis_bars=5)
        vr = VolatilityRegime(cfg)
        for _ in range(50):
            vr.update(0.001)
        # Only 2 spikes → not enough to switch with hysteresis=5
        for _ in range(2):
            vr.update(1.0)
        assert vr.current_regime != RegimeLabel.EXTREME


# ---------------------------------------------------------------------------
# OKXOrderRouter interface tests (no live calls)
# ---------------------------------------------------------------------------

class TestOKXOrderRouterImport:
    def test_import(self):
        from execution.okx_order_router import OKXOrderRouter, OKXConfig
        cfg = OKXConfig(api_key="test", api_secret="test", passphrase="test")
        router = OKXOrderRouter(cfg)
        assert router is not None
        assert not router._connected

    def test_config_defaults(self):
        from execution.okx_order_router import OKXConfig
        cfg = OKXConfig()
        assert cfg.instrument_type == "swap"
        assert cfg.max_retries == 3
        assert cfg.default_leverage == 1
        assert cfg.margin_mode == "cross"
        assert cfg.testnet is False

    def test_not_connected_raises(self):
        from execution.okx_order_router import OKXOrderRouter
        router = OKXOrderRouter()
        with pytest.raises(RuntimeError, match="not connected"):
            router._assert_connected()

    def test_ccxt_unavailable_raises_on_connect(self, monkeypatch):
        import execution.okx_order_router as mod
        monkeypatch.setattr(mod, "_CCXT_AVAILABLE", False)
        from execution.okx_order_router import OKXOrderRouter
        router = OKXOrderRouter()

        async def _run():
            import pytest as pt
            with pt.raises(RuntimeError, match="ccxt package"):
                await router.connect()

        import asyncio
        asyncio.get_event_loop().run_until_complete(_run())

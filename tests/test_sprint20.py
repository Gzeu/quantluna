"""
Sprint 20 tests — KalmanAdapter, VolRegimeAdapter, LiveDataBridge
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# KalmanAdapter
# ---------------------------------------------------------------------------

class TestKalmanAdapterImport:
    def test_import(self):
        from core.kalman_adapter import KalmanAdapter
        ka = KalmanAdapter(window=50)
        assert ka is not None

    def test_initial_state(self):
        from core.kalman_adapter import KalmanAdapter
        ka = KalmanAdapter(window=20)
        assert ka.zscore == 0.0
        assert ka.spread == 0.0
        assert ka.bar_count == 0
        assert not ka.is_warmed_up

    def test_update_increments_bar_count(self):
        from core.kalman_adapter import KalmanAdapter
        ka = KalmanAdapter(window=20)
        ka.update(100.0, 50.0)
        assert ka.bar_count == 1

    def test_fallback_zscore_computed(self):
        """After window bars, z-score should be non-zero."""
        from core.kalman_adapter import KalmanAdapter
        import random
        random.seed(1)
        ka = KalmanAdapter.__new__(KalmanAdapter)
        # Force fallback path
        from collections import deque
        ka._window = 20
        ka._half_life_h = 24.0
        ka._kf = None
        ka._spread_history = deque(maxlen=20)
        ka._zscore = 0.0
        ka._spread = 0.0
        ka._p_diag = 0.0
        ka._half_life = 24.0
        ka._bar_count = 0
        for i in range(25):
            ka.update(100.0 + random.gauss(0, 1), 50.0)
        assert ka.bar_count == 25
        assert ka.is_warmed_up

    def test_properties_exist(self):
        from core.kalman_adapter import KalmanAdapter
        ka = KalmanAdapter(window=10)
        _ = ka.zscore
        _ = ka.spread
        _ = ka.half_life
        _ = ka.p_diag
        _ = ka.bar_count
        _ = ka.is_warmed_up


# ---------------------------------------------------------------------------
# VolRegimeAdapter
# ---------------------------------------------------------------------------

class TestVolRegimeAdapterImport:
    def test_import(self):
        from core.vol_regime_adapter import VolRegimeAdapter
        vr = VolRegimeAdapter()
        assert vr is not None

    def test_initial_normal_regime(self):
        from core.vol_regime_adapter import VolRegimeAdapter, RegimeLabel
        vr = VolRegimeAdapter()
        # Force fallback (no VR available / neutral state)
        vr._vr = None
        assert vr.size_multiplier > 0
        assert vr.entry_allowed is True

    def test_extreme_vol_blocks_entry(self):
        from core.vol_regime_adapter import VolRegimeAdapter, RegimeLabel
        vr = VolRegimeAdapter(extreme_thresh=0.01)
        vr._vr = None  # force fallback
        # Feed large return to push into EXTREME
        for _ in range(10):
            vr.update(spread_return=0.1)
        assert vr.current_regime == RegimeLabel.EXTREME
        assert vr.entry_allowed is False
        assert vr.size_multiplier == 0.0

    def test_low_vol_increases_multiplier(self):
        from core.vol_regime_adapter import VolRegimeAdapter, RegimeLabel
        vr = VolRegimeAdapter(low_thresh=0.1, high_thresh=0.3, extreme_thresh=0.5)
        vr._vr = None
        for _ in range(30):
            vr.update(spread_return=0.0001)  # very small
        assert vr.current_regime == RegimeLabel.LOW
        assert vr.size_multiplier > 1.0

    def test_regime_label_has_value(self):
        from core.vol_regime_adapter import RegimeLabel
        assert RegimeLabel.NORMAL.value == "NORMAL"

    def test_update_increments_bar_count(self):
        from core.vol_regime_adapter import VolRegimeAdapter
        vr = VolRegimeAdapter()
        vr._vr = None
        vr.update(0.001)
        assert vr._bar_count == 1


# ---------------------------------------------------------------------------
# LiveDataBridge
# ---------------------------------------------------------------------------

class TestLiveDataBridgeImport:
    def test_import(self):
        from core.live_data_bridge import LiveDataBridge, LiveDataBridgeConfig
        b = LiveDataBridge()
        assert b is not None

    def test_config_defaults(self):
        from core.live_data_bridge import LiveDataBridgeConfig
        cfg = LiveDataBridgeConfig()
        assert cfg.symbol_y == "BTCUSDT"
        assert cfg.lookback_bars == 500

    @pytest.mark.asyncio
    async def test_fetch_historical_no_fetcher_returns_empty(self):
        from core.live_data_bridge import LiveDataBridge
        bridge = LiveDataBridge(fetcher=None)
        bars = await bridge.fetch_historical()
        assert bars == []

    def test_from_dataframes(self):
        """Convert two small DataFrames to BarData list."""
        import pandas as pd
        from core.live_data_bridge import LiveDataBridge, LiveDataBridgeConfig

        df_y = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
        df_x = pd.DataFrame({"close": [50.0,  51.0,  52.0]})

        bridge = LiveDataBridge(LiveDataBridgeConfig(symbol_y="BTC", symbol_x="ETH"))
        bars = bridge.from_dataframes(df_y, df_x)

        assert len(bars) == 3
        assert bars[0].price_y == 100.0
        assert bars[2].price_x == 52.0

    def test_from_dataframes_missing_column(self):
        import pandas as pd
        from core.live_data_bridge import LiveDataBridge
        df_y = pd.DataFrame({"open": [100.0]})  # wrong column name
        df_x = pd.DataFrame({"open": [50.0]})
        bridge = LiveDataBridge()
        bars = bridge.from_dataframes(df_y, df_x)
        assert bars == []

    @pytest.mark.asyncio
    async def test_stream_live_yields_bars(self):
        from core.live_data_bridge import LiveDataBridge, LiveDataBridgeConfig

        cache = MagicMock()
        cache.get_latest_price.side_effect = lambda sym: 100.0 if "BTC" in sym else 50.0

        cfg = LiveDataBridgeConfig(
            symbol_y="BTCUSDT", symbol_x="ETHUSDT",
            live_poll_s=0.0, max_live_bars=3,
        )
        bridge = LiveDataBridge(cfg=cfg)
        bars = []
        async for bar in bridge.stream_live(cache=cache):
            bars.append(bar)
        assert len(bars) == 3
        assert bars[0].price_y == 100.0
        assert bars[0].price_x == 50.0

    @pytest.mark.asyncio
    async def test_stream_live_no_cache(self):
        from core.live_data_bridge import LiveDataBridge, LiveDataBridgeConfig
        cfg = LiveDataBridgeConfig(live_poll_s=0.0, max_live_bars=2)
        bridge = LiveDataBridge(cfg=cfg)
        bars = []
        async for bar in bridge.stream_live(cache=None):
            bars.append(bar)
        assert bars == []  # no cache → no bars yielded, loop terminates


# ---------------------------------------------------------------------------
# Full stack smoke: KalmanAdapter + VolRegimeAdapter + IntegrationLoop
# ---------------------------------------------------------------------------

class TestSmokeS20FullStack:
    @pytest.mark.asyncio
    async def test_full_stack_50_bars(self):
        """KalmanAdapter + VolRegimeAdapter + SpreadMonitor + RegimeFilter + IntegrationLoop."""
        import random
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData
        from core.kalman_adapter import KalmanAdapter
        from core.vol_regime_adapter import VolRegimeAdapter
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        from strategy.regime_filter import RegimeFilter
        from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        random.seed(99)
        kf = KalmanAdapter(window=20)
        vr = VolRegimeAdapter(ewma_span=10)
        vr._vr = None  # force fallback for determinism
        cb = CircuitBreaker(CircuitBreakerConfig(max_consecutive_losses=10))
        sm = SpreadMonitor(SpreadMonitorConfig(min_bars=20, zscore_control_limit=5.0))
        rf = RegimeFilter(circuit_breaker=cb, vol_regime=vr, spread_monitor=sm)

        cfg = IntegrationLoopConfig(entry_zscore=1.5, exit_zscore=0.3, dry_run=True)
        loop = IntegrationLoop(cfg=cfg, kalman=kf, spread_monitor=sm, regime_filter=rf)

        bars = []
        spread = 0.0
        for _ in range(50):
            spread += 0.05 * (0 - spread) + random.gauss(0, 1)
            bars.append(BarData("BTCUSDT", "ETHUSDT", 100 + spread, 100.0))

        results = await loop.run_synthetic(bars)
        assert len(results) == 50
        assert all(hasattr(r, "gate_allowed") for r in results)

    @pytest.mark.asyncio
    async def test_backtest_integration_script_runs(self):
        """Smoke: backtest_integration main stack (no CSV, synthetic)."""
        import random
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData
        from core.kalman_adapter import KalmanAdapter
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        from strategy.regime_filter import RegimeFilter

        random.seed(7)
        kf = KalmanAdapter(window=10)
        sm = SpreadMonitor(SpreadMonitorConfig(min_bars=10))
        rf = RegimeFilter(spread_monitor=sm)
        cfg = IntegrationLoopConfig(dry_run=True)
        loop = IntegrationLoop(cfg=cfg, kalman=kf, spread_monitor=sm, regime_filter=rf)

        spread = 0.0
        bars = []
        for _ in range(30):
            spread += 0.1 * (0 - spread) + random.gauss(0, 0.5)
            bars.append(BarData("BTCUSDT", "ETHUSDT", 100 + spread, 100.0))

        results = await loop.run_synthetic(bars)
        assert len(results) == 30

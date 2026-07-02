"""
Sprint 19 tests — IntegrationLoop end-to-end
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestIntegrationLoopImport:
    def test_import(self):
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig
        loop = IntegrationLoop()
        assert loop is not None

    def test_bar_data(self):
        from execution.integration_loop import BarData
        bar = BarData(symbol_y="BTCUSDT", symbol_x="ETHUSDT", price_y=100.0, price_x=50.0)
        assert bar.price_y == 100.0


class TestIntegrationLoopSynthetic:
    @pytest.mark.asyncio
    async def test_run_empty_bars(self):
        from execution.integration_loop import IntegrationLoop
        loop = IntegrationLoop()
        results = await loop.run_synthetic([])
        assert results == []

    @pytest.mark.asyncio
    async def test_run_single_bar_no_entry(self):
        """z-score 0.0 → no entry."""
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData
        cfg = IntegrationLoopConfig(entry_zscore=2.0, dry_run=True)
        loop = IntegrationLoop(cfg=cfg)
        bars = [BarData("BTCUSDT", "ETHUSDT", 100.0, 100.0)]
        results = await loop.run_synthetic(bars)
        assert len(results) == 1
        assert results[0].order_submitted is False

    @pytest.mark.asyncio
    async def test_dry_run_submits_order(self):
        """When z-score > entry_zscore in dry_run, order_submitted=True."""
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData
        import math

        class FakeKalman:
            zscore = 2.5
            half_life = 24.0
            p_diag = 0.0
            spread = 0.5
            def update(self, y, x): pass

        cfg = IntegrationLoopConfig(entry_zscore=2.0, dry_run=True)
        loop = IntegrationLoop(cfg=cfg, kalman=FakeKalman())
        bars = [BarData("BTCUSDT", "ETHUSDT", 102.5, 100.0)]
        results = await loop.run_synthetic(bars)
        assert results[0].order_submitted is True
        assert results[0].zscore == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_entry(self):
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData
        from strategy.regime_filter import RegimeFilter

        cb = MagicMock()
        cb.is_open = False  # tripped
        rf = RegimeFilter(circuit_breaker=cb)

        class FakeKalman:
            zscore = 3.0
            half_life = 24.0
            p_diag = 0.0
            spread = 1.0
            def update(self, y, x): pass

        cfg = IntegrationLoopConfig(entry_zscore=2.0, dry_run=True)
        loop = IntegrationLoop(cfg=cfg, kalman=FakeKalman(), regime_filter=rf)
        bars = [BarData("BTCUSDT", "ETHUSDT", 103.0, 100.0)]
        results = await loop.run_synthetic(bars)
        assert results[0].gate_allowed is False
        assert results[0].order_submitted is False
        assert "circuit_breaker" in results[0].gate_blocked_by

    @pytest.mark.asyncio
    async def test_entry_then_exit(self):
        """Two bars: entry on bar 0, exit on bar 1."""
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData

        zscores = [2.5, 0.2]  # entry then exit
        call_count = 0

        class FakeKalman:
            zscore = 2.5
            half_life = 24.0
            p_diag = 0.0
            spread = 0.5
            def update(self, y, x):
                nonlocal call_count
                self.zscore = zscores[min(call_count, len(zscores)-1)]
                call_count += 1

        cfg = IntegrationLoopConfig(entry_zscore=2.0, exit_zscore=0.5, dry_run=True)
        loop = IntegrationLoop(cfg=cfg, kalman=FakeKalman())
        bars = [
            BarData("BTCUSDT", "ETHUSDT", 102.5, 100.0),
            BarData("BTCUSDT", "ETHUSDT", 100.2, 100.0),
        ]
        results = await loop.run_synthetic(bars)
        assert results[0].order_submitted is True   # entry
        assert results[1].order_submitted is True   # exit

    @pytest.mark.asyncio
    async def test_notifier_bus_called_on_entry(self):
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData

        bus = AsyncMock(spec=["send_entry_signal"])

        class FakeKalman:
            zscore = 3.0
            half_life = 24.0
            p_diag = 0.0
            spread = 1.0
            def update(self, y, x): pass

        cfg = IntegrationLoopConfig(entry_zscore=2.0, dry_run=True)
        loop = IntegrationLoop(cfg=cfg, kalman=FakeKalman(), notifier_bus=bus)
        bars = [BarData("BTCUSDT", "ETHUSDT", 103.0, 100.0)]
        await loop.run_synthetic(bars)
        bus.send_entry_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_spread_monitor_blocks_unhealthy(self):
        from execution.integration_loop import IntegrationLoop, IntegrationLoopConfig, BarData
        from core.spread_monitor import SpreadMonitor, SpreadMonitorConfig
        from strategy.regime_filter import RegimeFilter

        cfg_sm = SpreadMonitorConfig(min_bars=0, zscore_control_limit=1.0)
        sm = SpreadMonitor(cfg_sm)
        rf = RegimeFilter(spread_monitor=sm)

        class FakeKalman:
            zscore = 3.0
            half_life = 24.0
            p_diag = 0.0
            spread = 2.0
            def update(self, y, x): pass

        cfg = IntegrationLoopConfig(entry_zscore=2.0, dry_run=True)
        loop = IntegrationLoop(cfg=cfg, kalman=FakeKalman(), spread_monitor=sm, regime_filter=rf)
        bars = [BarData("BTCUSDT", "ETHUSDT", 103.0, 100.0)]
        results = await loop.run_synthetic(bars)
        assert results[0].gate_allowed is False
        assert results[0].spread_healthy is False

    @pytest.mark.asyncio
    async def test_results_property(self):
        from execution.integration_loop import IntegrationLoop, BarData
        loop = IntegrationLoop()
        bars = [BarData("A", "B", 1.0, 1.0) for _ in range(5)]
        await loop.run_synthetic(bars)
        assert len(loop.results) == 5

    @pytest.mark.asyncio
    async def test_cycle_result_fields(self):
        from execution.integration_loop import IntegrationLoop, BarData
        loop = IntegrationLoop()
        bars = [BarData("A", "B", 1.0, 1.0)]
        results = await loop.run_synthetic(bars)
        r = results[0]
        assert hasattr(r, "bar_idx")
        assert hasattr(r, "zscore")
        assert hasattr(r, "gate_allowed")
        assert hasattr(r, "duration_ms")
        assert r.duration_ms >= 0

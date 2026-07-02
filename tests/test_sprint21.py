"""
Sprint 21 tests — BybitLiveRunner + BybitWsBarsAdapter
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# BybitLiveRunnerConfig
# ---------------------------------------------------------------------------

class TestBybitLiveRunnerConfig:
    def test_import(self):
        from execution.bybit_live_runner import BybitLiveRunnerConfig
        cfg = BybitLiveRunnerConfig()
        assert cfg.dry_run is True
        assert cfg.symbol_y == "BTCUSDT"

    def test_from_env_defaults(self, monkeypatch):
        from execution.bybit_live_runner import BybitLiveRunnerConfig
        monkeypatch.delenv("DRY_RUN", raising=False)
        cfg = BybitLiveRunnerConfig.from_env()
        assert cfg.dry_run is True  # default safe

    def test_from_env_live(self, monkeypatch):
        from execution.bybit_live_runner import BybitLiveRunnerConfig
        monkeypatch.setenv("DRY_RUN", "false")
        monkeypatch.setenv("SYMBOL_Y", "SOLUSDT")
        monkeypatch.setenv("ENTRY_ZSCORE", "1.8")
        cfg = BybitLiveRunnerConfig.from_env()
        assert cfg.dry_run is False
        assert cfg.symbol_y == "SOLUSDT"
        assert cfg.entry_zscore == pytest.approx(1.8)


# ---------------------------------------------------------------------------
# BybitLiveRunner
# ---------------------------------------------------------------------------

class TestBybitLiveRunner:
    def test_import(self):
        from execution.bybit_live_runner import BybitLiveRunner
        r = BybitLiveRunner()
        assert r is not None

    def test_status_initial(self):
        from execution.bybit_live_runner import BybitLiveRunner
        r = BybitLiveRunner()
        s = r.status()
        assert s["bars_processed"] == 0
        assert s["dry_run"] is True
        assert s["warmed_up"] is False

    @pytest.mark.asyncio
    async def test_start_stop_no_ws(self):
        """start() with no ws_feed should wait for stop()."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        cfg = BybitLiveRunnerConfig(dry_run=True)
        r = BybitLiveRunner(cfg=cfg)

        async def stopper():
            await asyncio.sleep(0.05)
            await r.stop()

        asyncio.create_task(stopper())
        await r.start()
        assert r.status()["bars_processed"] == 0

    @pytest.mark.asyncio
    async def test_build_components_creates_kalman(self):
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        cfg = BybitLiveRunnerConfig(dry_run=True, kalman_window=20)
        r = BybitLiveRunner(cfg=cfg)
        await r._build_components()
        assert r._kalman is not None
        assert r._spread_monitor is not None
        assert r._regime_filter is not None
        assert r._order_manager is not None

    @pytest.mark.asyncio
    async def test_injected_components_not_overwritten(self):
        """Injected components must NOT be replaced by _build_components."""
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        mock_kalman = MagicMock()
        mock_om     = MagicMock()
        cfg = BybitLiveRunnerConfig(dry_run=True)
        r = BybitLiveRunner(cfg=cfg, kalman=mock_kalman, order_manager=mock_om)
        await r._build_components()
        assert r._kalman is mock_kalman
        assert r._order_manager is mock_om


# ---------------------------------------------------------------------------
# BybitWsBarsAdapter
# ---------------------------------------------------------------------------

class TestBybitWsBarsAdapter:
    def test_import(self):
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        a = BybitWsBarsAdapter()
        assert a is not None

    def test_extract_price_v5_format(self):
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        a = BybitWsBarsAdapter(price_field="close")
        msg = {"topic": "kline.5.BTCUSDT", "data": [{"close": "50000.5"}]}
        assert a._extract_price(msg) == pytest.approx(50000.5)

    def test_extract_price_c_field(self):
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        a = BybitWsBarsAdapter(price_field="close")
        msg = {"data": [{"c": "1234.56"}]}
        assert a._extract_price(msg) == pytest.approx(1234.56)

    def test_extract_price_numeric(self):
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        a = BybitWsBarsAdapter()
        assert a._extract_price(99.9) == pytest.approx(99.9)

    def test_extract_price_invalid(self):
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        a = BybitWsBarsAdapter()
        assert a._extract_price({"data": []}) is None

    @pytest.mark.asyncio
    async def test_mock_stream_yields_bars(self):
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        adapter = BybitWsBarsAdapter(ws_feed=None)
        bars = []
        async for bar in adapter._mock_stream("BTCUSDT", "ETHUSDT", n_bars=10):
            bars.append(bar)
        assert len(bars) == 10
        assert bars[0].symbol_y == "BTCUSDT"
        assert bars[0].symbol_x == "ETHUSDT"
        assert isinstance(bars[0].price_y, float)

    @pytest.mark.asyncio
    async def test_stream_bars_no_feed_yields_mock(self):
        from execution.bybit_ws_bars import BybitWsBarsAdapter
        adapter = BybitWsBarsAdapter(ws_feed=None)
        bars = []
        async for bar in adapter.stream_bars("BTCUSDT", "ETHUSDT"):
            bars.append(bar)
            if len(bars) >= 5:
                break
        assert len(bars) == 5


# ---------------------------------------------------------------------------
# Full E2E smoke: BybitLiveRunner cu WsBarsAdapter mock
# ---------------------------------------------------------------------------

class TestSmokeS21E2E:
    @pytest.mark.asyncio
    async def test_runner_processes_bars_via_mock_ws(self):
        """
        BybitLiveRunner + BybitWsBarsAdapter (mock) → process 30 bars,
        verify stats updated.
        """
        from execution.bybit_live_runner import BybitLiveRunner, BybitLiveRunnerConfig
        from execution.bybit_ws_bars import BybitWsBarsAdapter

        cfg = BybitLiveRunnerConfig(
            dry_run=True,
            warmup_bars=5,
            entry_zscore=1.0,
            exit_zscore=0.3,
            ws_max_reconnects=1,
        )

        adapter = BybitWsBarsAdapter(ws_feed=None)
        bar_limit = 30
        orig_mock = adapter._mock_stream

        async def limited_mock(sy, sx, n_bars=200, interval_s=0.0):
            count = 0
            async for bar in orig_mock(sy, sx, n_bars=bar_limit, interval_s=0.0):
                yield bar
                count += 1

        adapter._mock_stream = limited_mock

        runner = BybitLiveRunner(cfg=cfg, ws_feed=adapter)
        await runner.start()

        s = runner.status()
        assert s["bars_processed"] >= bar_limit - cfg.warmup_bars
        assert s["errors"] == 0
        assert s["dry_run"] is True
        assert s["warmed_up"] is True

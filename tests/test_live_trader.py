"""
QuantLuna — Tests: execution/live_trader.py + execution/paper_account.py
Sprint 24  |  8 unit tests

No real WebSocket connection needed — tests inject BarData directly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from execution.live_trader import BarData, LiveTrader, TraderState
from execution.paper_account import PaperAccount


class _MockCfg:
    sym_y = "BTCUSDT"; sym_x = "ETHUSDT"; bar_freq = "1h"
    capital_usdt = 100.0; zscore_window = 10; zscore_entry = 2.0
    regime_window = 10;   adx_window = 5;    regime_min_persistence = 2
    funding_threshold_annual = 0.20; half_life_hours = 24.0


def _bar(sym: str, close: float, high: float = 0.0, low: float = 0.0) -> BarData:
    return BarData(
        symbol=sym,
        timestamp=datetime.now(timezone.utc),
        open=close, high=high or close * 1.001,
        low=low or close * 0.999, close=close, volume=1000.0,
    )


# ---------------------------------------------------------------------------
# PaperAccount
# ---------------------------------------------------------------------------

class TestPaperAccount:

    def test_open_and_close_long(self):
        acc = PaperAccount(capital_usdt=1000.0, fee_rate=0.0)
        acc.open_position(side=1, qty_y=1.0, qty_x=1.0, price_y=100.0, price_x=50.0)
        pnl = acc.close_position(price_y=110.0, price_x=50.0)
        assert pnl == pytest.approx(10.0, abs=0.01)

    def test_open_and_close_short(self):
        acc = PaperAccount(capital_usdt=1000.0, fee_rate=0.0)
        acc.open_position(side=-1, qty_y=1.0, qty_x=1.0, price_y=100.0, price_x=50.0)
        pnl = acc.close_position(price_y=90.0, price_x=50.0)
        assert pnl == pytest.approx(10.0, abs=0.01)

    def test_win_rate(self):
        acc = PaperAccount(capital_usdt=1000.0, fee_rate=0.0)
        # 2 winners, 1 loser
        for price_exit in [110.0, 110.0, 90.0]:
            acc.open_position(side=1, qty_y=1.0, qty_x=0.0, price_y=100.0, price_x=0.0)
            acc.close_position(price_y=price_exit, price_x=0.0)
        assert acc.win_rate == pytest.approx(2 / 3, abs=0.01)

    def test_double_open_ignored(self):
        acc = PaperAccount()
        acc.open_position(side=1, qty_y=1.0, qty_x=0.0, price_y=100.0, price_x=0.0)
        acc.open_position(side=-1, qty_y=1.0, qty_x=0.0, price_y=200.0, price_x=0.0)
        assert acc._open.side == 1  # first open wins


# ---------------------------------------------------------------------------
# LiveTrader (no WebSocket, inject bars directly)
# ---------------------------------------------------------------------------

class TestLiveTrader:

    def test_initial_state(self):
        trader = LiveTrader(_MockCfg())
        assert trader._state == TraderState.IDLE
        assert trader._position.side == 0
        assert trader.mode == "paper"

    def test_status_before_start(self):
        trader = LiveTrader(_MockCfg())
        s = trader.status()
        assert s.state == "idle"
        assert s.n_trades == 0
        assert s.bars_processed == 0

    def test_process_bars_increments_counter(self):
        """Inject bar pairs directly to bypass WebSocket."""
        trader = LiveTrader(_MockCfg())
        trader._start_ts = __import__("time").monotonic()

        async def _run():
            # Feed 30 bar pairs to warm up zscore
            for i in range(30):
                by = _bar("BTCUSDT", 100.0 + i * 0.1)
                bx = _bar("ETHUSDT", 50.0  + i * 0.05)
                await trader._process_bar_pair(by, bx)

        asyncio.run(_run())
        assert trader._bars_processed == 30

    def test_stop_from_idle(self):
        trader = LiveTrader(_MockCfg())

        async def _run():
            await trader.stop()

        asyncio.run(_run())
        assert trader._state == TraderState.STOPPED

"""
QuantLuna — Tests: execution/paper_engine.py
Sprint 30  |  10 teste
"""
from __future__ import annotations

import asyncio
import pytest

from execution.paper_engine import PaperTradingEngine
from execution.paper_order  import OrderStatus


@pytest.fixture
def engine():
    return PaperTradingEngine(
        initial_capital=10_000.0,
        simulate_latency=False,  # dezactivat in teste pentru viteza
    )


class TestPaperEngine:

    @pytest.mark.asyncio
    async def test_market_buy_fills(self, engine):
        order = await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=65000.0)
        assert order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)
        assert order.avg_fill_price > 0
        assert order.commission > 0

    @pytest.mark.asyncio
    async def test_market_sell_fills(self, engine):
        order = await engine.submit_order("ETHUSDT", "sell", 0.1, mid_price=3500.0)
        assert order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)

    @pytest.mark.asyncio
    async def test_slippage_applied(self, engine):
        # Buy trebuie sa fie >= mid_price (slippage pozitiv)
        order = await engine.submit_order("BTCUSDT", "buy", 0.001, mid_price=65000.0)
        if order.status == OrderStatus.FILLED:
            assert order.avg_fill_price >= 65000.0

    @pytest.mark.asyncio
    async def test_commission_correct(self, engine):
        order = await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=65000.0)
        if order.status == OrderStatus.FILLED:
            expected_comm = order.avg_fill_price * order.filled_qty * 0.00055
            assert abs(order.commission - expected_comm) < 0.01

    @pytest.mark.asyncio
    async def test_position_opens(self, engine):
        await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=65000.0)
        positions = engine.positions()
        assert any(p["symbol"] == "BTCUSDT" for p in positions)

    @pytest.mark.asyncio
    async def test_close_position_records_trade(self, engine):
        await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=65000.0)
        trade = engine.close_position("BTCUSDT", current_price=65500.0)
        assert trade is not None
        assert "pnl" in trade
        assert len(engine.trades()) == 1

    @pytest.mark.asyncio
    async def test_equity_decreases_on_commission(self, engine):
        initial = engine.snapshot()["equity_usdt"]
        await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=65000.0)
        snap = engine.snapshot()
        assert snap["equity_usdt"] < initial

    @pytest.mark.asyncio
    async def test_equity_curve_recorded(self, engine):
        await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=65000.0)
        curve = engine.equity_curve()
        assert len(curve) >= 2  # initial + dupa fill

    @pytest.mark.asyncio
    async def test_reset(self, engine):
        await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=65000.0)
        engine.reset()
        snap = engine.snapshot()
        assert snap["equity_usdt"]   == 10_000.0
        assert snap["n_trades"]       == 0
        assert snap["open_positions"] == 0

    @pytest.mark.asyncio
    async def test_invalid_price_rejected(self, engine):
        order = await engine.submit_order("BTCUSDT", "buy", 0.01, mid_price=0.0)
        assert order.status == OrderStatus.REJECTED

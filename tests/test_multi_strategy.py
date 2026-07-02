"""
Module: tests/test_multi_strategy.py
Sprint: 31 — T (Multi-Strategy Engine)
Description:
    10 pytest tests for MultiStrategyEngine, BaseStrategy, and
    concrete strategy implementations.
"""

from __future__ import annotations

import asyncio
import pytest

from strategy.base_strategy import StrategyState, SignalDirection
from strategy.mean_reversion import MeanReversionStrategy
from strategy.momentum import MomentumStrategy
from strategy.stat_arb import StatArbStrategy
from strategy.multi_strategy_engine import (
    MultiStrategyEngine, CapitalSplitMode, ConflictResolution
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mr_strategy() -> MeanReversionStrategy:
    return MeanReversionStrategy("mr1", {"entry_z": 2.0, "exit_z": 0.5, "stop_z": 3.5})


@pytest.fixture
def mom_strategy() -> MomentumStrategy:
    return MomentumStrategy("mom1", {"roc_period": 5, "roc_threshold": 0.01})


@pytest.fixture
def engine() -> MultiStrategyEngine:
    return MultiStrategyEngine(total_capital=10000.0)


# ---------------------------------------------------------------------------
# Tests — StrategyState
# ---------------------------------------------------------------------------

class TestStrategyState:
    def test_pause_resume(self, mr_strategy) -> None:
        mr_strategy.pause()
        assert mr_strategy.state == StrategyState.PAUSED
        assert not mr_strategy.is_active()
        mr_strategy.resume()
        assert mr_strategy.state == StrategyState.ACTIVE

    def test_stop(self, mr_strategy) -> None:
        mr_strategy.stop()
        assert mr_strategy.state == StrategyState.STOPPED


# ---------------------------------------------------------------------------
# Tests — MeanReversionStrategy
# ---------------------------------------------------------------------------

class TestMeanReversionStrategy:
    @pytest.mark.asyncio
    async def test_no_signal_below_threshold(self, mr_strategy) -> None:
        sig = await mr_strategy.generate_signal({"symbol": "BTCUSDT", "z_score": 1.0})
        assert sig.direction == SignalDirection.FLAT

    @pytest.mark.asyncio
    async def test_long_entry_on_negative_z(self, mr_strategy) -> None:
        sig = await mr_strategy.generate_signal({"symbol": "BTCUSDT", "z_score": -2.5})
        assert sig.direction == SignalDirection.LONG

    @pytest.mark.asyncio
    async def test_short_entry_on_positive_z(self, mr_strategy) -> None:
        sig = await mr_strategy.generate_signal({"symbol": "BTCUSDT", "z_score": 2.5})
        assert sig.direction == SignalDirection.SHORT

    @pytest.mark.asyncio
    async def test_exit_when_z_crosses_zero(self, mr_strategy) -> None:
        await mr_strategy.generate_signal({"symbol": "BTCUSDT", "z_score": 2.5})
        sig = await mr_strategy.generate_signal({"symbol": "BTCUSDT", "z_score": 0.3})
        assert sig.direction == SignalDirection.EXIT


# ---------------------------------------------------------------------------
# Tests — MultiStrategyEngine
# ---------------------------------------------------------------------------

class TestMultiStrategyEngine:
    @pytest.mark.asyncio
    async def test_register_and_list(self, engine, mr_strategy) -> None:
        engine.register(mr_strategy)
        metrics = engine.get_metrics()
        assert len(metrics) == 1
        assert metrics[0]["strategy_id"] == "mr1"

    @pytest.mark.asyncio
    async def test_equal_allocation(self, engine, mr_strategy, mom_strategy) -> None:
        engine.register(mr_strategy)
        engine.register(mom_strategy)
        alloc_mr = engine._allocations["mr1"]
        alloc_mom = engine._allocations["mom1"]
        assert abs(alloc_mr - 0.5) < 1e-6
        assert abs(alloc_mom - 0.5) < 1e-6

    @pytest.mark.asyncio
    async def test_conflict_ignore(self, engine) -> None:
        mr = MeanReversionStrategy("mr_c", {"entry_z": 2.0})
        mom = MomentumStrategy("mom_c", {"roc_period": 5, "roc_threshold": 0.01})
        eng = MultiStrategyEngine(
            conflict_resolution=ConflictResolution.IGNORE, total_capital=10000
        )
        eng.register(mr)
        eng.register(mom)
        # Force conflicting signals manually via _resolve_conflicts
        from strategy.base_strategy import SignalResult, SignalDirection
        results = {
            "mr_c": SignalResult(direction=SignalDirection.LONG, symbol="BTCUSDT"),
            "mom_c": SignalResult(direction=SignalDirection.SHORT, symbol="BTCUSDT"),
        }
        resolved = eng._resolve_conflicts(results, "BTCUSDT")
        assert resolved["mr_c"].direction == SignalDirection.FLAT
        assert resolved["mom_c"].direction == SignalDirection.FLAT

    @pytest.mark.asyncio
    async def test_tick_emits_events(self, engine, mr_strategy) -> None:
        engine.register(mr_strategy)
        await engine.start()
        events = await engine.tick({"symbol": "BTCUSDT", "z_score": 2.5})
        assert isinstance(events, list)
        assert any(e["event"] == "STRATEGY_SIGNAL" for e in events)

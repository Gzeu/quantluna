"""
QuantLuna — Tests: execution/multi_pair_manager.py
Sprint 27  |  6 tests
"""
from __future__ import annotations

import asyncio
import pytest

from execution.multi_pair_manager import MultiPairManager, PairConfig, PairState


@pytest.fixture
def manager():
    return MultiPairManager(total_capital_usd=10_000.0, max_pairs=3)


class TestMultiPairManager:

    def test_add_pair_registers(self, manager):
        manager.add_pair(PairConfig(sym_y="BTCUSDT", sym_x="ETHUSDT", alloc_usd=2000.0))
        status = manager.status()
        assert "BTCUSDT-ETHUSDT" in status["pairs"]
        assert status["n_pairs"] == 1

    def test_max_pairs_enforced(self, manager):
        for i in range(3):
            manager.add_pair(PairConfig(sym_y=f"SYM{i}USDT", sym_x="ETHUSDT", alloc_usd=1000.0))
        with pytest.raises(ValueError, match="max_pairs"):
            manager.add_pair(PairConfig(sym_y="XYZUSDT", sym_x="ETHUSDT", alloc_usd=500.0))

    def test_duplicate_pair_skipped(self, manager):
        manager.add_pair(PairConfig(sym_y="BTCUSDT", sym_x="ETHUSDT", alloc_usd=1000.0))
        manager.add_pair(PairConfig(sym_y="BTCUSDT", sym_x="ETHUSDT", alloc_usd=1000.0))
        assert manager.status()["n_pairs"] == 1

    def test_start_and_stop_pair(self):
        async def _run():
            mgr = MultiPairManager(total_capital_usd=5000.0, max_pairs=2)
            mgr.add_pair(PairConfig(sym_y="BTCUSDT", sym_x="ETHUSDT", alloc_usd=2000.0))
            await mgr.start_pair("BTCUSDT-ETHUSDT")
            await asyncio.sleep(0.05)
            assert mgr._pairs["BTCUSDT-ETHUSDT"].state == PairState.RUNNING
            await mgr.stop_pair("BTCUSDT-ETHUSDT")
            assert mgr._pairs["BTCUSDT-ETHUSDT"].state == PairState.STOPPED
        asyncio.run(_run())

    def test_halt_all_stops_all_pairs(self):
        async def _run():
            mgr = MultiPairManager(total_capital_usd=5000.0, max_pairs=2)
            mgr.add_pair(PairConfig(sym_y="BTCUSDT", sym_x="ETHUSDT", alloc_usd=1000.0))
            mgr.add_pair(PairConfig(sym_y="BNBUSDT", sym_x="SOLUSDT", alloc_usd=1000.0))
            await mgr.start_all()
            await asyncio.sleep(0.05)
            await mgr.halt_all(reason="TEST")
            assert mgr._halted is True
            for ps in mgr._pairs.values():
                assert ps.state in (PairState.STOPPED, PairState.HALTED)
        asyncio.run(_run())

    def test_resume_clears_halted_flag(self, manager):
        manager._halted = True
        manager.resume()
        assert manager._halted is False

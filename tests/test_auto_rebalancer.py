"""
QuantLuna — Tests: risk/auto_rebalancer.py
Sprint 30  |  8 teste
"""
from __future__ import annotations

import pytest

from risk.auto_rebalancer import AutoRebalancer


@pytest.fixture
def rebalancer():
    rb = AutoRebalancer(
        total_capital=10_000.0,
        min_alloc_pct=0.05,
        max_alloc_pct=0.40,
        cooldown_h=0.0,    # dezactivat in teste
    )
    rb.update_pair("BTC/ETH",  sharpe=1.8, current_alloc=2000.0, n_trades=50)
    rb.update_pair("SOL/BNB",  sharpe=0.4, current_alloc=1500.0, n_trades=20)
    rb.update_pair("ETH/AVAX", sharpe=1.1, current_alloc=2500.0, n_trades=35)
    return rb


class TestAutoRebalancer:

    def test_compute_dry_run_keys(self, rebalancer):
        result = rebalancer.compute_rebalance(dry_run=True)
        assert not result.skipped
        assert "BTC/ETH"  in result.allocations
        assert "SOL/BNB"  in result.allocations
        assert "ETH/AVAX" in result.allocations

    def test_high_sharpe_gets_more(self, rebalancer):
        result = rebalancer.compute_rebalance(dry_run=True)
        allocs = result.allocations
        # BTC/ETH (sharpe=1.8) trebuie > SOL/BNB (sharpe=0.4)
        assert allocs["BTC/ETH"] > allocs["SOL/BNB"]

    def test_max_alloc_respected(self, rebalancer):
        result = rebalancer.compute_rebalance(dry_run=True)
        max_allowed = 10_000.0 * 0.40
        for v in result.allocations.values():
            assert v <= max_allowed + 0.01  # toleranta float

    def test_min_alloc_respected(self, rebalancer):
        result = rebalancer.compute_rebalance(dry_run=True)
        min_allowed = 10_000.0 * 0.05
        for v in result.allocations.values():
            assert v >= min_allowed - 0.01

    def test_total_capital_not_exceeded(self, rebalancer):
        result = rebalancer.compute_rebalance(dry_run=True)
        total = sum(result.allocations.values())
        assert total <= 10_000.0 * 1.00 + 0.01

    def test_apply_updates_current_alloc(self, rebalancer):
        result = rebalancer.compute_rebalance(dry_run=False)
        assert not result.skipped
        # Dupa apply, current_alloc == noua alocare
        for pair, new_alloc in result.allocations.items():
            assert abs(rebalancer._pairs[pair].current_alloc - new_alloc) < 0.01

    def test_history_recorded_after_apply(self, rebalancer):
        rebalancer.compute_rebalance(dry_run=False)
        assert len(rebalancer.history()) == 1

    def test_insufficient_pairs_skipped(self):
        rb = AutoRebalancer(total_capital=10_000.0, min_pairs=3, cooldown_h=0.0)
        rb.update_pair("BTC/ETH", sharpe=1.5, current_alloc=5000.0)
        result = rb.compute_rebalance(dry_run=False)
        assert result.skipped is True

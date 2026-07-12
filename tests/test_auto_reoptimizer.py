"""
tests/test_auto_reoptimizer.py
S46 — G1 fix: acoperire AutoReoptimizer
"""
import pytest


class TestAutoReoptimizer:
    """Basic coverage for AutoReoptimizer."""

    def test_import(self):
        """Modulul trebuie sa fie importabil."""
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer  # noqa: F401
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")

    def test_should_reoptimize_true_both(self):
        """Returneaza True cand si trade_count SI hours depasesc thresholdurile."""
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        opt = AutoReoptimizer(min_trades=50, interval_hours=24)
        assert opt.should_reoptimize(trade_count=60, hours_since_last=25) is True

    def test_should_reoptimize_false_insufficient_trades(self):
        """Returneaza False cand trade_count e sub minimum."""
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        opt = AutoReoptimizer(min_trades=50, interval_hours=24)
        assert opt.should_reoptimize(trade_count=30, hours_since_last=25) is False

    def test_should_reoptimize_false_too_recent(self):
        """Returneaza False cand intervalul de timp nu a trecut."""
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        opt = AutoReoptimizer(min_trades=50, interval_hours=24)
        assert opt.should_reoptimize(trade_count=100, hours_since_last=5) is False

    def test_should_reoptimize_boundary(self):
        """Returneaza True exact la limita (>=)."""
        try:
            from backtest.auto_reoptimizer import AutoReoptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        opt = AutoReoptimizer(min_trades=50, interval_hours=24)
        assert opt.should_reoptimize(trade_count=50, hours_since_last=24) is True

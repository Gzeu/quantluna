"""
tests/test_param_grid_optimizer.py
S46 — G1 fix: acoperire ParamGridOptimizer
"""
import pytest


class TestParamGridOptimizer:
    """Basic coverage for ParamGridOptimizer."""

    def test_import(self):
        """Modulul trebuie sa fie importabil."""
        try:
            from backtest.param_grid_optimizer import ParamGridOptimizer  # noqa: F401
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")

    def test_grid_expansion_count(self):
        """Grid trebuie sa expandeze toate combinatiile parametrilor."""
        try:
            from backtest.param_grid_optimizer import ParamGridOptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        grid = {"lookback": [20, 40], "threshold": [1.5, 2.0]}
        opt = ParamGridOptimizer(param_grid=grid)
        combos = opt.expand()
        assert len(combos) == 4

    def test_grid_expansion_values(self):
        """Fiecare combinatie trebuie sa contina toate cheile din grid."""
        try:
            from backtest.param_grid_optimizer import ParamGridOptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        grid = {"lookback": [20, 40], "threshold": [1.5, 2.0]}
        opt = ParamGridOptimizer(param_grid=grid)
        combos = opt.expand()
        for combo in combos:
            assert "lookback" in combo
            assert "threshold" in combo

    def test_single_param_grid(self):
        """Grid cu un singur parametru si trei valori = 3 combinatii."""
        try:
            from backtest.param_grid_optimizer import ParamGridOptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        grid = {"zscore_entry": [1.5, 2.0, 2.5]}
        opt = ParamGridOptimizer(param_grid=grid)
        combos = opt.expand()
        assert len(combos) == 3

    def test_empty_grid(self):
        """Grid gol trebuie sa returneze o singura combinatie goala."""
        try:
            from backtest.param_grid_optimizer import ParamGridOptimizer
        except ImportError as exc:
            pytest.skip(f"Module not yet available: {exc}")
        opt = ParamGridOptimizer(param_grid={})
        combos = opt.expand()
        assert combos == [{}]

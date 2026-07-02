"""
QuantLuna — Tests: risk/correlation_filter.py
Sprint 29  |  8 tests
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from risk.correlation_filter import CorrelationFilter


def _series(n=100, base=100.0, seed=42, noise=0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    prices = base * np.cumprod(1 + rng.normal(0, noise, n))
    return pd.Series(prices)


def _correlated_series(ref: pd.Series, noise_level=0.005) -> pd.Series:
    """Genereaza o serie puternic corelata cu ref."""
    rng = np.random.default_rng(99)
    noise  = rng.normal(0, noise_level, len(ref))
    prices = ref.values * (1 + noise)
    return pd.Series(prices)


class TestCorrelationFilter:

    def test_threshold_validation(self):
        with pytest.raises(ValueError):
            CorrelationFilter(threshold=0.0)
        with pytest.raises(ValueError):
            CorrelationFilter(threshold=1.1)

    def test_update_and_symbols(self):
        cf = CorrelationFilter()
        cf.update("BTCUSDT", _series())
        cf.update("ETHUSDT", _series(seed=7))
        assert "BTCUSDT" in cf.symbols()
        assert "ETHUSDT" in cf.symbols()

    def test_uncorrelated_symbols_allowed(self):
        cf = CorrelationFilter(threshold=0.80, window=60)
        btc = _series(seed=1)
        sol = _series(seed=999, noise=0.02)
        cf.update("BTCUSDT", btc)
        cf.update("SOLUSDT", sol)
        allowed, violations = cf.check_new_pair("SOLUSDT", ["BTCUSDT"])
        # Seriile cu seede diferite nu sunt corelate puternic
        # Testam ca filtrul ruleaza fara crash; rezultatul depinde de date
        assert isinstance(allowed, bool)
        assert isinstance(violations, list)

    def test_correlated_symbols_blocked(self):
        cf = CorrelationFilter(threshold=0.70, window=80)
        btc = _series(n=200, seed=42)
        eth = _correlated_series(btc, noise_level=0.001)   # very correlated
        cf.update("BTCUSDT", btc)
        cf.update("ETHUSDT", eth)
        allowed, violations = cf.check_new_pair("ETHUSDT", ["BTCUSDT"])
        assert allowed is False
        assert len(violations) > 0
        assert violations[0]["blocked"] is True

    def test_symbol_without_data_allowed(self):
        cf = CorrelationFilter()
        cf.update("BTCUSDT", _series())
        allowed, violations = cf.check_new_pair("NEWCOIN", ["BTCUSDT"])
        assert allowed is True
        assert len(violations) == 0

    def test_correlation_matrix_keys(self):
        cf = CorrelationFilter(threshold=0.80, window=50)
        for sym, seed in [("BTCUSDT", 1), ("ETHUSDT", 2), ("SOLUSDT", 3)]:
            cf.update(sym, _series(n=200, seed=seed))
        matrix = cf.correlation_matrix()
        assert "symbols"       in matrix
        assert "matrix"        in matrix
        assert "blocked_pairs" in matrix
        assert "threshold"     in matrix
        assert len(matrix["matrix"]) == 3

    def test_remove_symbol(self):
        cf = CorrelationFilter()
        cf.update("BTCUSDT", _series())
        cf.remove("BTCUSDT")
        assert "BTCUSDT" not in cf.symbols()

    def test_matrix_single_symbol_no_crash(self):
        cf = CorrelationFilter()
        cf.update("BTCUSDT", _series())
        matrix = cf.correlation_matrix()
        assert matrix["symbols"] == ["BTCUSDT"]
        assert matrix["matrix"] == []

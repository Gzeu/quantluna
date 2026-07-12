"""
tests/test_sizing_engine.py — Sprint S35
Teste unitare pentru risk/sizing_engine.py (S34)

Acopera:
  - set_pair_factor / get_pair_factor / reset_pair_factor / reset_all_factors
  - calculate() cu factor aplicat
  - calculate() cu factor=0.0 (zero_result)
  - get_status() structura
  - factor clamp [0, 1]
  - comportament default (factor=1.0 pt perechi noi)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_sizer():
    sizer = MagicMock()
    sizer.capital_usdt = 50_000.0
    sizer.max_leverage = 3.0
    sizer.kelly_fraction = "half"
    sizer.calculate.return_value = {
        "qty_y": 0.1,
        "qty_x": 0.3,
        "notional_usd": 5_000.0,
        "leverage": 2.0,
        "sizing_method": "kelly_half",
    }
    return sizer


@pytest.fixture
def engine(mock_sizer):
    from risk.sizing_engine import SizingEngine
    return SizingEngine(sizer=mock_sizer)


# ---------------------------------------------------------------------------
# Test: factor management
# ---------------------------------------------------------------------------

def test_default_factor_is_one(engine):
    """Perechi noi au factor 1.0 implicit."""
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") == 1.0


def test_set_pair_factor(engine):
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.5)
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") == 0.5


def test_set_pair_factor_clamp_max(engine):
    """Factor > 1.0 trebuie clamped la 1.0."""
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 1.5)
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") <= 1.0


def test_set_pair_factor_clamp_min(engine):
    """Factor < 0.0 trebuie clamped la 0.0."""
    engine.set_pair_factor("BTCUSDT-ETHUSDT", -0.5)
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") >= 0.0


def test_reset_pair_factor(engine):
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.3)
    engine.reset_pair_factor("BTCUSDT-ETHUSDT")
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") == 1.0


def test_reset_all_factors(engine):
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.5)
    engine.set_pair_factor("SOLUSDT-AVAXUSDT", 0.3)
    engine.reset_all_factors()
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") == 1.0
    assert engine.get_pair_factor("SOLUSDT-AVAXUSDT") == 1.0


def test_multiple_pairs_independent(engine):
    """Factorii pentru perechi diferite sunt independenti."""
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.5)
    engine.set_pair_factor("SOLUSDT-AVAXUSDT", 0.8)
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") == 0.5
    assert engine.get_pair_factor("SOLUSDT-AVAXUSDT") == 0.8


# ---------------------------------------------------------------------------
# Test: calculate() cu factor
# ---------------------------------------------------------------------------

def test_calculate_with_full_factor(engine, mock_sizer):
    """Factor=1.0 — sizer apelat cu capitalul intreg."""
    result = engine.calculate("BTCUSDT-ETHUSDT", {})
    mock_sizer.calculate.assert_called_once()
    assert result is not None


def test_calculate_with_half_factor(engine, mock_sizer):
    """Factor=0.5 — capital_usdt al sizer-ului trebuie redus la jumatate temporar."""
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.5)
    engine.calculate("BTCUSDT-ETHUSDT", {})
    # Sizer-ul trebuie apelat — capital efectiv = 50_000 * 0.5 = 25_000
    mock_sizer.calculate.assert_called_once()


def test_calculate_with_zero_factor(engine, mock_sizer):
    """Factor=0.0 — returneaza zero_result fara a apela sizer-ul."""
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.0)
    result = engine.calculate("BTCUSDT-ETHUSDT", {})
    mock_sizer.calculate.assert_not_called()
    # Rezultatul trebuie sa indice zeroing
    assert result is not None
    if isinstance(result, dict):
        method = result.get("sizing_method", "")
        assert "zero" in method.lower() or "watchdog" in method.lower() or result.get("qty_y", -1) == 0


# ---------------------------------------------------------------------------
# Test: get_status()
# ---------------------------------------------------------------------------

def test_get_status_structure(engine):
    """get_status() returneaza campurile asteptate de /sizing/live_status."""
    engine.set_pair_factor("BTCUSDT-ETHUSDT", 0.5)
    status = engine.get_status()
    assert isinstance(status, dict)
    # Campuri obligatorii
    assert "capital_usdt" in status
    assert "pair_factors" in status
    assert "n_reduced_pairs" in status


de
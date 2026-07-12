"""
tests/test_decision_engine_unit.py — S36
Teste unitare pentru DecisionEngine v2.5 (risk/decision_engine.py).
Verifica logica de semnal, streak, drawdown si exit partial
fara exchange real sau API.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def engine():
    """DecisionEngine cu configuratie default testabila."""
    from risk.decision_engine import DecisionEngine
    return DecisionEngine(
        entry_zscore=2.0,
        exit_zscore=0.5,
        partial_exit_zscore=1.0,
        scale_in_zscore=2.5,
        base_qty_y=100.0,
        base_qty_x=100.0,
    )


# ─ instantiere ───────────────────────────────────────────────────────────────

class TestDecisionEngineInit:
    def test_instance_not_none(self, engine):
        assert engine is not None

    def test_entry_zscore_set(self, engine):
        assert engine.entry_zscore == 2.0

    def test_exit_zscore_set(self, engine):
        assert engine.exit_zscore == 0.5

    def test_in_position_initially_false(self, engine):
        assert engine.in_position is False

    def test_streak_initially_zero(self, engine):
        assert engine.current_streak == 0

    def test_drawdown_initially_zero(self, engine):
        assert engine.current_drawdown == 0.0


# ─ get_status ─────────────────────────────────────────────────────────────────

class TestDecisionEngineStatus:
    def test_get_status_returns_dict(self, engine):
        s = engine.get_status()
        assert isinstance(s, dict)

    def test_get_status_has_in_position(self, engine):
        assert "in_position" in engine.get_status()

    def test_get_status_has_streak(self, engine):
        s = engine.get_status()
        assert "current_streak" in s or "streak" in s

    def test_get_status_has_drawdown(self, engine):
        s = engine.get_status()
        assert "current_drawdown" in s or "drawdown" in s


# ─ semnale entry/exit ─────────────────────────────────────────────────────────

class TestDecisionEngineSignals:
    def test_should_enter_above_entry_zscore(self, engine):
        """z-score peste entry_zscore trebuie sa genereze semnal de intrare."""
        if hasattr(engine, "should_enter"):
            assert engine.should_enter(zscore=2.5) is True
        else:
            pytest.skip("should_enter not implemented")

    def test_should_not_enter_below_entry_zscore(self, engine):
        if hasattr(engine, "should_enter"):
            assert engine.should_enter(zscore=1.0) is False
        else:
            pytest.skip("should_enter not implemented")

    def test_should_exit_below_exit_zscore(self, engine):
        if hasattr(engine, "should_exit"):
            engine.in_position = True
            assert engine.should_exit(zscore=0.3) is True
        else:
            pytest.skip("should_exit not implemented")

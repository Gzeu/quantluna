"""
tests/test_sprint46.py — Sprint S35
Teste pentru S46: DecisionEngine v2.5 dashboard (api/decision.py)

Acopera:
  - GET /api/decision/status -> enabled=False cand engine-ul nu e injectat
  - GET /api/decision/status -> enabled=True cu toate campurile prezente
  - set_decision_state() injection
  - Campuri: entry_zscore, exit_zscore, partial_exit_zscore, scale_in_zscore
  - Campuri: base_qty_y, base_qty_x, current_streak, current_drawdown, in_position
  - in_position=True reflectat corect
  - streak negativ (loss streak) reflectat corect
  - GET /sizing/decision_status -> alias compat
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_decision():
    """FastAPI cu decision_router si sizing_router."""
    from fastapi import FastAPI
    from api.decision import decision_router, set_decision_state
    from api.sizing import router as sizing_router, set_sizing_state

    set_decision_state({"decision_engine": None})
    set_sizing_state({"sizing_engine": None, "decision_engine": None})

    _app = FastAPI()
    _app.include_router(decision_router, prefix="/api/decision")
    _app.include_router(sizing_router)
    return _app


@pytest.fixture
def client_decision(app_decision):
    return TestClient(app_decision)


@pytest.fixture
def mock_decision_engine():
    engine = MagicMock()
    engine._entry_z        = 2.0
    engine._exit_z         = 0.5
    engine._partial_z      = 1.5
    engine._scale_z        = 2.5
    engine._base_qty_y     = 0.1
    engine._base_qty_x     = 0.3
    engine._current_streak = 3
    engine._current_dd     = 0.02
    engine._in_position    = False
    return engine


# ---------------------------------------------------------------------------
# Tests: GET /api/decision/status
# ---------------------------------------------------------------------------

def test_decision_status_no_engine(client_decision):
    """enabled=False cand decision_engine nu e injectat."""
    resp = client_decision.get("/api/decision/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert "status" in data


def test_decision_status_with_engine(app_decision, mock_decision_engine):
    """enabled=True cu toate campurile prezente."""
    from api.decision import set_decision_state
    set_decision_state({"decision_engine": mock_decision_engine})
    client = TestClient(app_decision)
    resp = client.get("/api/decision/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True


def test_decision_status_all_fields(app_decision, mock_decision_engine):
    """Toate campurile asteptate sunt prezente in response."""
    from api.decision import set_decision_state
    set_decision_state({"decision_engine": mock_decision_engine})
    client = TestClient(app_decision)
    resp = client.get("/api/decision/status")
    data = resp.json()
    expected_fields = [
        "enabled", "entry_zscore", "exit_zscore",
        "partial_exit_zscore", "scale_in_zscore",
        "base_qty_y", "base_qty_x",
        "current_streak", "current_drawdown", "in_position",
    ]
    for field in expected_fields:
        assert field in data, f"Camp lipsa: {field}"


def test_decision_status_values(app_decision, mock_decision_engine):
    """Valorile din engine sunt reflectate corect in response."""
    from api.decision import set_decision_state
    set_decision_state({"decision_engine": mock_decision_engine})
    client = TestClient(app_decision)
    resp = client.get("/api/decision/status")
    data = resp.json()
    assert data["entry_zscore"]    == 2.0
    assert data["exit_zscore"]     == 0.5
    assert data["current_streak"]  == 3
    assert data["current_drawdown"] == 0.02
    assert data["in_position"]     is False


def test_decision_status_in_position_true(app_decision, mock_decision_engine):
    """in_position=True reflectat corect."""
    mock_decision_engine._in_position = True
    from api.decision import set_decision_state
    set_decision_state({"decision_engine": mock_decision_engine})
    client = TestClient(app_decision)
    resp = client.get("/api/decision/status")
    data = resp.json()
    assert data["in_position"] is True


def test_decision_status_negative_streak(app_decision, mock_decision_engine):
    """Streak negativ (loss streak) reflectat corect."""
    mock_decision_engine._current_streak = -4
    from api.decision import set_decision_state
    set_decision_state({"decision_engine": mock_decision_engine})
    client = TestClient(app_decision)
    resp = client.get("/api/decision/status")
    data = resp.json()
    assert data["current_streak"] == -4


# ---------------------------------------------------------------------------
# Tests: GET /sizing/decision_status (alias compat S46)
# ---------------------------------------------------------------------------

def test_sizing_decision_status_alias_no_engine(client_decision):
    """GET /sizing/decision_status -> enabled=False fara engine."""
    from api.sizing import set_sizing_state
    set_sizing_state({"decision_engine": None})
    resp = client_decision.get("/sizing/decision_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False


def test_sizing_decision_status_alias_with_engine(app_decision, mock_decision_engine):
    """GET /sizing/decision_status -> enabled=True cu engine injectat."""
    from api.sizing import set_sizing_state
    set_sizing_state({"decision_engine": mock_decision_engine})
    client = TestClient(app_decision)
    resp = client.get("/sizing/decision_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert "entry_zscore" in data

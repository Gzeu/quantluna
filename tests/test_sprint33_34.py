"""
tests/test_sprint33_34.py — Sprint S35
Teste smoke pentru S33 (api/pairs + api/sizing hooks) si S34 (SizingEngine).

Acopera:
  - POST /sizing/reduce/{pair_id} -> cale 1 SizingEngine.set_pair_factor
  - POST /sizing/reduce/{pair_id} -> cale 2 fallback (no engine)
  - GET  /sizing/reduce/history   -> audit log
  - GET  /sizing/live_status      -> SizingEngine injectat vs None
  - reduce_pair_size() callable programmatic
  - Lant complet: reduce_pair_size -> set_pair_factor -> factor persistat
  - _record_reduce() inregistreaza succese si esecuri
  - ReduceRecord campuri (timestamp, pair, factor, success, detail)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# App fixture minimal
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Creeaza o instanta FastAPI cu doar sizing router, fara lifespan."""
    from fastapi import FastAPI
    from api.sizing import router as sizing_router, set_sizing_state

    # Reset state
    set_sizing_state({"sizing_engine": None, "decision_engine": None})

    _app = FastAPI()
    _app.include_router(sizing_router)
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def mock_sizing_engine():
    engine = MagicMock()
    engine.set_pair_factor = MagicMock()
    engine.get_pair_factor = MagicMock(return_value=0.5)
    engine.get_status = MagicMock(return_value={
        "capital_usdt": 50000.0,
        "pair_factors": {"BTCUSDT-ETHUSDT": 0.5},
        "n_reduced_pairs": 1,
        "active_reductions": ["BTCUSDT-ETHUSDT"],
    })
    return engine


# ---------------------------------------------------------------------------
# Tests: GET /sizing/live_status
# ---------------------------------------------------------------------------

def test_live_status_no_engine(client):
    """live_status returneaza enabled=False cand engine-ul nu e injectat."""
    resp = client.get("/sizing/live_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False


def test_live_status_with_engine(app, mock_sizing_engine):
    """live_status returneaza enabled=True si datele din get_status()."""
    from api.sizing import set_sizing_state
    set_sizing_state({"sizing_engine": mock_sizing_engine})
    client = TestClient(app)
    resp = client.get("/sizing/live_status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert "capital_usdt" in data
    assert data["capital_usdt"] == 50000.0


# ---------------------------------------------------------------------------
# Tests: POST /sizing/reduce/{pair_id}
# ---------------------------------------------------------------------------

def test_reduce_endpoint_cale1_sizing_engine(app, mock_sizing_engine):
    """Cale 1: POST reduce -> SizingEngine.set_pair_factor apelat."""
    from api.sizing import set_sizing_state
    set_sizing_state({"sizing_engine": mock_sizing_engine})
    client = TestClient(app)
    resp = client.post("/sizing/reduce/BTCUSDT-ETHUSDT", json={"factor": 0.5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["pair_id"] == "BTCUSDT-ETHUSDT"
    mock_sizing_engine.set_pair_factor.assert_called_once_with("BTCUSDT-ETHUSDT", 0.5)


def test_reduce_endpoint_no_engine_fallback(client):
    """Fallback: POST reduce fara engine -> success=False in history."""
    from api.sizing import set_sizing_state, _REDUCE_REGISTRY
    set_sizing_state({"sizing_engine": None})
    initial_len = len(_REDUCE_REGISTRY)
    resp = client.post("/sizing/reduce/SOLUSDT-AVAXUSDT", json={"factor": 0.3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True  # endpoint returneaza 200 intotdeauna
    # Verifica ca s-a inregistrat in registry
    assert len(_REDUCE_REGISTRY) > initial_len


def test_reduce_endpoint_default_factor(app, mock_sizing_engine):
    """POST reduce fara body -> factor default 0.5."""
    from api.sizing import set_sizing_state
    set_sizing_state({"sizing_engine": mock_sizing_engine})
    client = TestClient(app)
    resp = client.post("/sizing/reduce/BTCUSDT-ETHUSDT")
    assert resp.status_code == 200
    data = resp.json()
    assert data["factor"] == 0.5


# ---------------------------------------------------------------------------
# Tests: GET /sizing/reduce/history
# ---------------------------------------------------------------------------

def test_reduce_history_structure(app, mock_sizing_engine):
    """GET /sizing/reduce/history returneaza structura corecta."""
    from api.sizing import set_sizing_state
    set_sizing_state({"sizing_engine": mock_sizing_engine})
    client = TestClient(app)
    # Facem mai intai un reduce
    client.post("/sizing/reduce/BTCUSDT-ETHUSDT", json={"factor": 0.5})
    resp = client.get("/sizing/reduce/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "records" in data
    assert isinstance(data["records"], list)
    if data["records"]:
        rec = data["records"][0]
        assert "timestamp" in rec
        assert "pair" in rec
        assert "factor" in rec
        assert "success" in rec
        assert "detail" in rec


# ---------------------------------------------------------------------------
# Tests: reduce_pair_size() callable programmatic
# ---------------------------------------------------------------------------

def test_reduce_pair_size_callable_cale1():
    """reduce_pair_size() callable -> SizingEngine.set_pair_factor."""
    from api.sizing import reduce_pair_size, set_sizing_state

    mock_engine = MagicMock()
    mock_engine.set_pair_factor = MagicMock()
    set_sizing_state({"sizing_engine": mock_engine})

    asyncio.get_event_loop().run_until_complete(
        reduce_pair_size("BTCUSDT-ETHUSDT", 0.4)
    )
    mock_engine.set_pair_factor.assert_called_once_with("BTCUSDT-ETHUSDT", 0.4)


def test_reduce_pair_size_clamp():
    """reduce_pair_size() clampuieste factor in [0, 1]."""
    from api.sizing import reduce_pair_size, set_sizing_state, _REDUCE_REGISTRY

    mock_engine = MagicMock()
    mock_engine.set_pair_factor = MagicMock()
    set_sizing_state({"sizing_engine": mock_engine})

    asyncio.get_event_loop().run_until_complete(
        reduce_pair_size("BTCUSDT-ETHUSDT", 1.5)  # > 1.0
    )
    call_args = mock_engine.set_pair_factor.call_args
    applied_factor = call_args[0][1]
    assert applied_factor <= 1.0


# ---------------------------------------------------------------------------
# Test: lant complet watchdog -> reduce -> factor persistat
# ---------------------------------------------------------------------------

def test_full_chain_watchdog_to_sizing_engine():
    """Lant complet: reduce_pair_size() -> SizingEngine.set_pair_factor -> factor stocat."""
    from risk.sizing_engine import SizingEngine
    from risk.bybit_position_sizer import BybitPositionSizer
    from api.sizing import reduce_pair_size, set_sizing_state

    sizer = BybitPositionSizer(
        capital_usdt=10_000.0,
        max_leverage=3.0,
        kelly_fraction="half",
        max_position_pct=0.25,
    )
    engine = SizingEngine(sizer=sizer)
    set_sizing_state({"sizing_engine": engine})

    # Simuleaza watchdog trigger
    asyncio.get_event_loop().run_until_complete(
        reduce_pair_size("BTCUSDT-ETHUSDT", 0.5)
    )

    # Verifica factor persistat in SizingEngine
    assert engine.get_pair_factor("BTCUSDT-ETHUSDT") == 0.5

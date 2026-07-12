"""
api/decision.py — QuantLuna Decision API v1.0
Sprint S46 (2026-07-12): router dedicat pentru dashboard unificat.

Endpoints:
  GET /api/decision/status — status live DecisionEngine v2.5

Wiring:
  In api/main.py:
    from api.decision import decision_router, set_decision_state
    app.include_router(decision_router, prefix="/api/decision", tags=["decision"])

  In lifespan, dupa build_context():
    set_decision_state({"decision_engine": getattr(ctx, "decision_engine", None)})

Frontend contract:
  Dashboard-ul principal (/dashboard si dashboard/) trebuie sa consume
  GET /api/decision/status ca sursa unica de adevar pentru DecisionEngine.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

decision_router = APIRouter()

# ---------------------------------------------------------------------------
# Stare injectabila — populata din api/main.py la lifespan startup
# ---------------------------------------------------------------------------
_DECISION_STATE: Dict[str, Any] = {
    "decision_engine": None,
}


def set_decision_state(state: Dict[str, Any]) -> None:
    """Injectat din api/main.py la lifespan startup."""
    _DECISION_STATE.update(state or {})


@decision_router.get("/status")
def decision_status():
    """
    GET /api/decision/status

    Status live DecisionEngine v2.5 pentru dashboard unificat.
    Returneaza toti parametrii de decizie si starea curenta a pozitiei.

    Response:
      enabled (bool)             : False daca engine-ul nu a fost injectat
      entry_zscore (float|null)  : z-score de intrare configurata
      exit_zscore (float|null)   : z-score de iesire configurata
      partial_exit_zscore        : z-score pentru iesire partiala
      scale_in_zscore            : z-score pentru scale-in
      base_qty_y / base_qty_x    : cantitatile de baza per leg
      current_streak (int)       : streak curent win/loss
      current_drawdown (float)   : drawdown curent
      in_position (bool)         : daca exista pozitie deschisa acum
    """
    engine = _DECISION_STATE.get("decision_engine")
    if engine is None:
        return {
            "enabled": False,
            "status":  "DecisionEngine indisponibil — bot-ul nu a injectat engine-ul inca",
        }

    return {
        "enabled":             True,
        "entry_zscore":        getattr(engine, "_entry_z",       None),
        "exit_zscore":         getattr(engine, "_exit_z",        None),
        "partial_exit_zscore": getattr(engine, "_partial_z",     None),
        "scale_in_zscore":     getattr(engine, "_scale_z",       None),
        "base_qty_y":          getattr(engine, "_base_qty_y",    None),
        "base_qty_x":          getattr(engine, "_base_qty_x",    None),
        "current_streak":      getattr(engine, "_current_streak", 0),
        "current_drawdown":    getattr(engine, "_current_dd",    0.0),
        "in_position":         getattr(engine, "_in_position",   False),
    }

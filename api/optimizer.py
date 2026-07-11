"""
api/optimizer.py  -  QuantLuna Optimizer API v1.0

Sprint S39-S40 (2026-07-12):
  Endpoint-uri FastAPI pentru control grid optimizer + auto-reoptimizer:

    POST /api/optimizer/run           - trigger manual grid search imediat
    GET  /api/optimizer/status        - status curent (running/idle + ultima rulare)
    GET  /api/optimizer/results       - ultimele rezultate per pereche
    GET  /api/optimizer/history       - istoricul tuturor reoptimizarilor
    POST /api/optimizer/reoptimize    - trigger re-optimizare manuala
    GET  /api/optimizer/pair/{pair}   - detalii + config curenta per pereche

  Integrat in api/main.py cu:
    app.include_router(optimizer_router, prefix="/api/optimizer")
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, HTTPException, BackgroundTasks
    from pydantic import BaseModel
except ImportError:
    raise RuntimeError("fastapi si pydantic necesare")

from loguru import logger

optimizer_router = APIRouter(tags=["optimizer"])

_HISTORY_PATH = Path(os.getenv(
    "REOPT_HISTORY", "state/reoptimizer_history.json"
))
_CONFIG_DIR = Path(os.getenv("PAIRS_CONFIG_DIR", "config/pairs"))
_REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "backtest/reports"))

# Stare globala a optimizatorului (injectata din main.py)
_optimizer_state: Dict[str, Any] = {
    "running": False,
    "last_run": None,
    "last_results": {},
    "auto_reoptimizer": None,
}


def set_optimizer_state(state: Dict[str, Any]) -> None:
    """Injecteaza starea din main.py."""
    _optimizer_state.update(state)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class RunGridRequest(BaseModel):
    pairs: Optional[List[str]] = None
    days: int = 180
    objective: str = "sharpe"    # sharpe | calmar | pnl | profit_factor
    grid_type: str = "coarse"   # coarse | fine
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@optimizer_router.get("/status")
async def get_optimizer_status() -> Dict[str, Any]:
    """Starea curenta a optimizatorului."""
    reopt = _optimizer_state.get("auto_reoptimizer")
    return {
        "running": _optimizer_state["running"],
        "last_run": _optimizer_state["last_run"],
        "pairs_count": len(
            _optimizer_state.get("pairs", [])
        ),
        "auto_reoptimizer_active": reopt is not None,
        "auto_schedule": {
            "weekday": getattr(reopt, "_weekday", 6),
            "hour_utc": getattr(reopt, "_hour", 2),
        } if reopt else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@optimizer_router.get("/results")
async def get_last_results() -> Dict[str, Any]:
    """Ultimele rezultate grid search per pereche."""
    return {
        "results": _optimizer_state.get("last_results", {}),
        "last_run": _optimizer_state.get("last_run"),
    }


@optimizer_router.get("/history")
async def get_history(limit: int = 20) -> Dict[str, Any]:
    """Istoricul tuturor re-optimizarilor."""
    if not _HISTORY_PATH.exists():
        return {"history": [], "total": 0}
    try:
        with open(_HISTORY_PATH) as f:
            history = json.load(f)
        return {
            "history": history[-limit:],
            "total": len(history),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@optimizer_router.get("/pair/{pair}")
async def get_pair_details(pair: str) -> Dict[str, Any]:
    """Config curenta + ultimele rezultate pentru o pereche."""
    config_path = _CONFIG_DIR / f"{pair}.json"
    config = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
        except Exception:
            pass

    # Cauta CSV raport
    reports = sorted(
        _REPORTS_DIR.glob(f"grid_{pair}_*.csv"),
        reverse=True,
    )
    last_report = str(reports[0]) if reports else None

    return {
        "pair": pair,
        "current_config": config,
        "last_report_csv": last_report,
        "last_results": _optimizer_state.get(
            "last_results", {}
        ).get(pair),
    }


@optimizer_router.post("/run")
async def trigger_grid_run(
    req: RunGridRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Trigger manual grid search in background.
    Raspunde imediat; rezultatele apar in /results cand e gata.
    """
    if _optimizer_state["running"]:
        raise HTTPException(
            status_code=409,
            detail="Optimizer deja ruleaza. Asteapta finalizarea.",
        )

    reopt = _optimizer_state.get("auto_reoptimizer")
    if reopt is None:
        raise HTTPException(
            status_code=503,
            detail="AutoReoptimizer nu e initializat. Verifica startupul.",
        )

    async def _bg_run():
        _optimizer_state["running"] = True
        try:
            reopt._grid_type = req.grid_type
            reopt._objective = req.objective
            reopt._dry_run = req.dry_run
            if req.pairs:
                reopt._pairs = req.pairs
            result = await reopt.run_now(force=True)
            _optimizer_state["last_results"] = result.get("results", {})
            _optimizer_state["last_run"] = datetime.now(
                timezone.utc
            ).isoformat()
        finally:
            _optimizer_state["running"] = False

    background_tasks.add_task(_bg_run)
    return {
        "status": "started",
        "pairs": req.pairs or _optimizer_state.get("pairs", []),
        "objective": req.objective,
        "grid_type": req.grid_type,
        "dry_run": req.dry_run,
        "message": "Grid search pornit in background. Verifica /api/optimizer/status",
    }


@optimizer_router.post("/reoptimize")
async def trigger_reoptimize(
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """Trigger re-optimizare manuala cu setarile curente."""
    return await trigger_grid_run(
        RunGridRequest(),
        background_tasks,
    )

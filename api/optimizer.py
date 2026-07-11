"""
api/optimizer.py  -  QuantLuna Optimizer API v1.1

Sprint S43 (2026-07-12): adauga endpoint heatmap pentru iframe dashboard
  GET /api/optimizer/heatmap/{pair}  - serveste HTML heatmap per pereche
  (toate celelalte endpoint-uri raman identice cu v1.0)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, HTTPException, BackgroundTasks
    from fastapi.responses import HTMLResponse
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

_optimizer_state: Dict[str, Any] = {
    "running": False,
    "last_run": None,
    "last_results": {},
    "auto_reoptimizer": None,
}


def set_optimizer_state(state: Dict[str, Any]) -> None:
    _optimizer_state.update(state)


class RunGridRequest(BaseModel):
    pairs: Optional[List[str]] = None
    days: int = 180
    objective: str = "sharpe"
    grid_type: str = "coarse"
    dry_run: bool = False


@optimizer_router.get("/status")
async def get_optimizer_status() -> Dict[str, Any]:
    reopt = _optimizer_state.get("auto_reoptimizer")
    return {
        "running": _optimizer_state["running"],
        "last_run": _optimizer_state["last_run"],
        "pairs_count": len(_optimizer_state.get("pairs", [])),
        "auto_reoptimizer_active": reopt is not None,
        "auto_schedule": {
            "weekday": getattr(reopt, "_weekday", 6),
            "hour_utc": getattr(reopt, "_hour", 2),
        } if reopt else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@optimizer_router.get("/results")
async def get_last_results() -> Dict[str, Any]:
    return {
        "results": _optimizer_state.get("last_results", {}),
        "last_run": _optimizer_state.get("last_run"),
    }


@optimizer_router.get("/history")
async def get_history(limit: int = 20) -> Dict[str, Any]:
    if not _HISTORY_PATH.exists():
        return {"history": [], "total": 0}
    try:
        with open(_HISTORY_PATH) as f:
            history = json.load(f)
        return {"history": history[-limit:], "total": len(history)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@optimizer_router.get("/pair/{pair}")
async def get_pair_details(pair: str) -> Dict[str, Any]:
    config_path = _CONFIG_DIR / f"{pair}.json"
    config = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
        except Exception:
            pass
    reports = sorted(_REPORTS_DIR.glob(f"grid_{pair}_*.csv"), reverse=True)
    last_report = str(reports[0]) if reports else None
    return {
        "pair": pair,
        "current_config": config,
        "last_report_csv": last_report,
        "last_results": _optimizer_state.get("last_results", {}).get(pair),
    }


@optimizer_router.get("/heatmap/{pair}", response_class=HTMLResponse)
async def get_heatmap(pair: str) -> HTMLResponse:
    """
    Serveste cel mai recent heatmap HTML per pereche.
    Folosit de iframe-ul din dashboard/pages/optimizer.tsx.
    """
    files = sorted(
        _REPORTS_DIR.glob(f"heatmap_{pair}_*.html"),
        reverse=True,
    )
    if not files:
        # Placeholder HTML cand nu exista inca heatmap
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
<html><head><meta charset='UTF-8'></head>
<body style='background:#0f0f1a;color:#555;
  font-family:Inter,sans-serif;
  display:flex;align-items:center;justify-content:center;
  height:100vh;margin:0;flex-direction:column;gap:12px'>
  <div style='font-size:32px'>🔍</div>
  <div style='font-size:15px'>Niciun heatmap disponibil pentru <b style='color:#8b5cf6'>{pair}</b></div>
  <div style='font-size:12px;color:#444'>Rulează grid search pentru a genera heatmap-ul.</div>
</body></html>""",
            status_code=200,
        )
    with open(files[0], encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)


@optimizer_router.post("/run")
async def trigger_grid_run(
    req: RunGridRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    if _optimizer_state["running"]:
        raise HTTPException(
            status_code=409,
            detail="Optimizer deja ruleaza. Asteapta finalizarea.",
        )
    reopt = _optimizer_state.get("auto_reoptimizer")
    if reopt is None:
        raise HTTPException(
            status_code=503,
            detail="AutoReoptimizer nu e initializat.",
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
            _optimizer_state["last_run"] = datetime.now(timezone.utc).isoformat()
        finally:
            _optimizer_state["running"] = False

    background_tasks.add_task(_bg_run)
    return {
        "status": "started",
        "pairs": req.pairs or _optimizer_state.get("pairs", []),
        "objective": req.objective,
        "grid_type": req.grid_type,
        "dry_run": req.dry_run,
        "message": "Grid search pornit. Verifica /api/optimizer/status",
    }


@optimizer_router.post("/reoptimize")
async def trigger_reoptimize(
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    return await trigger_grid_run(RunGridRequest(), background_tasks)

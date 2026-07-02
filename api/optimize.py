"""
QuantLuna — Optimize API
Sprint 25

Endpoints:
  POST /optimize/walk_forward   — submit walk-forward optimization job
  GET  /optimize/{job_id}       — poll job status / result
  GET  /optimize/{job_id}/best  — get best params (global + per regime)

Job lifecycle: queued → running → done | error

Jobs stored in-memory (_OPT_JOBS dict, same pattern as backtest jobs).
"""
from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

import numpy as np
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/optimize", tags=["optimize"])

_OPT_JOBS: Dict[str, Dict] = {}
_OPT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="optimizer")


class WalkForwardRequest(BaseModel):
    sym_y:       str   = Field(..., example="BTCUSDT")
    sym_x:       str   = Field(..., example="ETHUSDT")
    n_bars:      int   = Field(2000, ge=600)
    train_bars:  int   = Field(500,  ge=100)
    test_bars:   int   = Field(100,  ge=20)
    step_bars:   Optional[int] = Field(None)
    n_jobs:      int   = Field(1, ge=1, le=8)  # limited in API to avoid OOM
    param_grid:  Optional[Dict[str, Any]] = Field(None)
    description: Optional[str] = None


@router.post("/walk_forward")
async def submit_walk_forward(
    req: WalkForwardRequest,
    background_tasks: BackgroundTasks,
) -> Dict:
    job_id = str(uuid.uuid4())[:8]
    _OPT_JOBS[job_id] = {
        "job_id":   job_id,
        "status":   "queued",
        "created":  time.time(),
        "request":  req.model_dump(),
        "result":   None,
        "error":    None,
        "progress": 0,
    }
    background_tasks.add_task(_run_optimize_job, job_id, req)
    logger.info(f"Optimize job {job_id} queued: {req.sym_y}/{req.sym_x}")
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
def get_optimize_job(job_id: str) -> Dict:
    job = _OPT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Optimize job {job_id!r} not found")
    return {
        "job_id":   job["job_id"],
        "status":   job["status"],
        "progress": job["progress"],
        "created":  job["created"],
        "error":    job["error"],
        "result":   job["result"],
    }


@router.get("/{job_id}/best")
def get_best_params(job_id: str) -> Dict:
    job = _OPT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Optimize job {job_id!r} not found")
    if job["status"] != "done":
        raise HTTPException(status_code=425, detail=f"Job status: {job['status']}. Wait for 'done'.")
    r = job["result"]
    return {
        "job_id":               job_id,
        "best_params_global":   r["best_params_global"],
        "best_params_by_regime": r["best_params_by_regime"],
        "avg_test_sharpe":      r["avg_test_sharpe"],
        "avg_test_pnl":         r["avg_test_pnl"],
        "n_folds":              r["n_folds"],
    }


def _run_optimize_job(job_id: str, req: WalkForwardRequest) -> None:
    job = _OPT_JOBS[job_id]
    job["status"] = "running"
    t0 = time.time()
    try:
        from backtest.walk_forward_optimizer import WalkForwardOptimizer

        # Generate synthetic price series for the pair
        rng   = np.random.default_rng(42)
        y     = __import__("pandas").Series(100.0 + np.cumsum(rng.normal(0, 1, req.n_bars)))
        x     = __import__("pandas").Series(50.0  + np.cumsum(rng.normal(0, 0.5, req.n_bars)))

        opt = WalkForwardOptimizer(
            train_bars=req.train_bars,
            test_bars=req.test_bars,
            step_bars=req.step_bars,
            n_jobs=req.n_jobs,
        )
        result = opt.run(y=y, x=x, param_grid=req.param_grid)
        job["result"] = {
            "n_folds":               result.n_folds,
            "best_params_global":    result.best_params_global,
            "best_params_by_regime": result.best_params_by_regime,
            "avg_test_sharpe":       round(result.avg_test_sharpe, 4),
            "avg_test_pnl":          round(result.avg_test_pnl, 6),
            "fold_results": [
                {
                    "fold":           f.fold,
                    "train_sharpe":   round(f.train_sharpe, 4),
                    "test_sharpe":    round(f.test_sharpe, 4),
                    "test_pnl":       round(f.test_pnl, 6),
                    "dominant_regime": f.dominant_regime,
                    "best_params":    f.best_params,
                    "n_trades":       f.n_trades,
                }
                for f in result.fold_results
            ],
        }
        job["status"]   = "done"
        job["progress"] = 100
        logger.info(f"Optimize job {job_id} done in {time.time()-t0:.1f}s")
    except Exception as e:
        job["status"] = "error"
        job["error"]  = str(e)
        logger.error(f"Optimize job {job_id} failed: {e}")

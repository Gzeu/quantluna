"""
api/backtest.py  —  QuantLuna Sprint 16

FastAPI router pentru backtest REST API.

Endpoints:
  POST /api/backtest/run
      Acceptă BacktestRequest JSON, pornește backtest în background task.
      Returnează imediat {job_id, status: "queued"} dacă async=True (implicit),
      sau blocă și returnează metrics complete dacă async=False.

  GET  /api/backtest/jobs/{job_id}
      Returnează status + metrics pentru un job existent.

  GET  /api/backtest/jobs/{job_id}/trades.csv
      Download CSV cu toate trade-urile OOS ale job-ului.

  GET  /api/backtest/jobs
      Listează toate job-urile (maxim 100, sorted by created_at desc).

  DELETE /api/backtest/jobs/{job_id}
      Șterge un job din memorie (nu anulează un job running).

Design:
  - Job store in-memory (dict); pentru producție înlocui cu Redis/DB.
  - BacktestEngine din backtest.engine_adapter folosește StrategyConfig.
  - Date sintetice generate dacă data_dir lipsă (CI/dev friendly).
  - CSV generat on-demand via pandas to_csv(StringIO).
  - Background tasks prin FastAPI BackgroundTasks (nu ThreadPoolExecutor) —
    simplu şi fără dependențe extra.
"""
from __future__ import annotations

import io
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.schemas import (
    BacktestMetrics,
    BacktestRequest,
    BacktestResponse,
    JobListItem,
    JobStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

_JOBS: Dict[str, Dict[str, Any]] = {}  # job_id → internal job dict
_MAX_JOBS = 100


def _job_to_response(job: Dict) -> BacktestResponse:
    req = job["request"]
    metrics_raw = job.get("metrics")
    metrics = BacktestMetrics(**metrics_raw) if metrics_raw else None
    trades_csv_url = f"/api/backtest/jobs/{job['job_id']}/trades.csv" if job.get("trades_df") is not None else None

    # Include max 1000 trades in response body if requested
    trades_list = None
    if req.include_trades and job.get("trades_df") is not None:
        df = job["trades_df"]
        if not df.empty:
            trades_list = df.head(1000).to_dict(orient="records")

    return BacktestResponse(
        job_id=job["job_id"],
        status=job["status"],
        request=req,
        metrics=metrics,
        trades=trades_list,
        trades_csv_url=trades_csv_url,
        error=job.get("error"),
        duration_s=job.get("duration_s"),
        created_at=job["created_at"],
        completed_at=job.get("completed_at"),
    )


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _generate_synthetic_prices(
    n: int,
    seed: int = 42,
    freq: str = "1h",
) -> pd.DataFrame:
    """
    Generează pereche de prețuri cointegrate sintetic, pentru dev/CI.
    Returnează DataFrame cu coloane [timestamp, close_y, close_x].
    """
    rng = np.random.default_rng(seed)
    freq_map = {
        "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
        "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h",
        "6h": "6h", "8h": "8h", "12h": "12h", "1d": "1D",
    }
    pd_freq = freq_map.get(freq, "1h")
    idx = pd.date_range("2023-01-01", periods=n, freq=pd_freq, tz="UTC")
    x = 100 + np.cumsum(rng.normal(0, 0.3, n))
    y = 1.5 * x + 10 + rng.normal(0, 0.5, n)
    return pd.DataFrame({"timestamp": idx, "close_y": y, "close_x": x})


def _build_strategy_config(req: BacktestRequest):
    """Construiește StrategyConfig din BacktestRequest."""
    try:
        from config.strategy_config import StrategyConfig
    except ImportError:
        raise HTTPException(status_code=500, detail="config.strategy_config not available")

    kwargs = dict(
        sym_y=req.sym_y,
        sym_x=req.sym_x,
        bar_freq=req.bar_freq.value,
        capital_usdt=req.capital_usdt,
        vol_target=req.vol_target,
        kelly_fraction=req.kelly_fraction,
        max_leverage=req.max_leverage,
        zscore_entry=req.zscore_entry,
        zscore_exit=req.zscore_exit,
        zscore_window=req.zscore_window,
        warm_up_bars=req.warm_up_bars,
        delta=req.delta,
        observation_noise=req.observation_noise,
        fee_rate=req.fee_rate,
        slippage_pct=req.slippage_pct,
    )

    # Override din params_file (Optuna best params)
    if req.params_file:
        try:
            cfg = StrategyConfig.from_optimizer_json(req.params_file)
            # Apply only fields not explicitly in request (params_file is a baseline)
            return cfg
        except Exception as e:
            logger.warning(f"params_file load failed: {e} — using request params")

    return StrategyConfig(**{k: v for k, v in kwargs.items()
                             if v is not None})


# ---------------------------------------------------------------------------
# Core backtest runner (runs in background task)
# ---------------------------------------------------------------------------

def _run_backtest_job(job_id: str, req: BacktestRequest) -> None:
    """
    Execută backtest-ul complet şi actualizează job store.
    Rulată în FastAPI BackgroundTasks thread.
    """
    job = _JOBS.get(job_id)
    if not job:
        return

    job["status"] = JobStatus.RUNNING
    t0 = time.monotonic()

    try:
        cfg = _build_strategy_config(req)

        # Data
        if req.data_dir:
            from pathlib import Path
            from backtest.engine_adapter import BacktestEngine
            engine = BacktestEngine(
                cfg,
                n_splits=req.n_splits,
                purge_bars=req.purge_bars,
                embargo_bars=req.embargo_bars,
            )
            result = engine.run(data_dir=Path(req.data_dir))
        else:
            n = req.n_bars or 2000
            df = _generate_synthetic_prices(n=n, freq=req.bar_freq.value)
            from backtest.engine_adapter import BacktestEngine
            engine = BacktestEngine(
                cfg,
                n_splits=req.n_splits,
                purge_bars=req.purge_bars,
                embargo_bars=req.embargo_bars,
            )
            result = engine.run(df=df)

        # Sanitize float fields (NaN/Inf → 0.0)
        def _safe(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return 0.0
            return v

        metrics = {
            "sharpe":           _safe(result.get("sharpe", 0.0)),
            "sortino":          _safe(result.get("sortino", 0.0)),
            "calmar":           _safe(result.get("calmar", 0.0)),
            "max_drawdown":     _safe(result.get("max_drawdown", 0.0)),
            "max_drawdown_pct": _safe(result.get("max_drawdown_pct", 0.0)),
            "win_rate":         _safe(result.get("win_rate", 0.0)),
            "profit_factor":    _safe(result.get("profit_factor", 0.0)),
            "n_trades":         int(result.get("n_trades", 0)),
            "total_net_pnl":    _safe(result.get("total_net_pnl", 0.0)),
            "ann_return":       _safe(result.get("ann_return", 0.0)),
            "ann_volatility":   _safe(result.get("ann_volatility", 0.0)),
            "n_folds":          int(result.get("n_folds", req.n_splits)),
            "overfit_flag":     bool(result.get("overfit_flag", False)),
        }

        trades_df = result.get("trades_df")
        if trades_df is None:
            trades_df = pd.DataFrame()

        job["status"]       = JobStatus.DONE
        job["metrics"]      = metrics
        job["trades_df"]    = trades_df
        job["duration_s"]   = round(time.monotonic() - t0, 3)
        job["completed_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[{job_id}] DONE in {job['duration_s']}s | "
            f"Sharpe={metrics['sharpe']:.2f} Trades={metrics['n_trades']}"
        )

    except Exception as exc:
        job["status"]       = JobStatus.ERROR
        job["error"]        = str(exc)
        job["duration_s"]   = round(time.monotonic() - t0, 3)
        job["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.error(f"[{job_id}] ERROR: {exc}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=BacktestResponse, status_code=202)
async def run_backtest(
    req: BacktestRequest,
    background_tasks: BackgroundTasks,
    sync: bool = Query(
        default=False,
        description="Dacă True, blochează până la finalizare (max 120s). "
                    "Dacă False (default), returnează imediat job_id."
    ),
) -> BacktestResponse:
    """
    POST /api/backtest/run

    Pornește un backtest walk-forward cu parametrii din body.

    - **sync=false** (implicit): răspuns imediat cu `{job_id, status: queued}`;
      poll GET /api/backtest/jobs/{job_id} până `status == done`.
    - **sync=true**: blochează până la finalizare, returnează metrics complete.

    Curl example:
    ```bash
    curl -X POST http://localhost:8000/api/backtest/run \\
      -H 'Content-Type: application/json' \\
      -d '{"sym_y": "BTCUSDT", "sym_x": "ETHUSDT", "n_splits": 3, "n_bars": 1000}'
    ```
    """
    # Evict oldest jobs dacă limita e atinsă
    if len(_JOBS) >= _MAX_JOBS:
        oldest = sorted(_JOBS.values(), key=lambda j: j["created_at"])[:10]
        for j in oldest:
            del _JOBS[j["job_id"]]

    job_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    _JOBS[job_id] = {
        "job_id":      job_id,
        "status":      JobStatus.QUEUED,
        "request":     req,
        "metrics":     None,
        "trades_df":   None,
        "error":       None,
        "duration_s":  None,
        "created_at":  now,
        "completed_at": None,
    }

    if sync:
        # Blocking mode: run directly
        _run_backtest_job(job_id, req)
    else:
        background_tasks.add_task(_run_backtest_job, job_id, req)

    return _job_to_response(_JOBS[job_id])


@router.get("/jobs", response_model=List[JobListItem])
async def list_jobs(
    status: Optional[str] = Query(default=None, description="Filtrează după status"),
    limit: int = Query(default=20, ge=1, le=100),
) -> List[JobListItem]:
    """
    GET /api/backtest/jobs

    Listează job-urile recente (sorted by created_at desc).
    """
    jobs = sorted(_JOBS.values(), key=lambda j: j["created_at"], reverse=True)
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    jobs = jobs[:limit]

    result = []
    for j in jobs:
        req: BacktestRequest = j["request"]
        sharpe = None
        if j.get("metrics"):
            sharpe = j["metrics"].get("sharpe")
        result.append(JobListItem(
            job_id=j["job_id"],
            status=j["status"],
            sym_y=req.sym_y,
            sym_x=req.sym_x,
            bar_freq=req.bar_freq.value,
            n_splits=req.n_splits,
            created_at=j["created_at"],
            duration_s=j.get("duration_s"),
            sharpe=sharpe,
        ))
    return result


@router.get("/jobs/{job_id}", response_model=BacktestResponse)
async def get_job(job_id: str) -> BacktestResponse:
    """
    GET /api/backtest/jobs/{job_id}

    Returnează status + metrics pentru un job.
    Poll aceasta după POST /run cu sync=false.
    """
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return _job_to_response(job)


@router.get("/jobs/{job_id}/trades.csv")
async def download_trades_csv(job_id: str) -> StreamingResponse:
    """
    GET /api/backtest/jobs/{job_id}/trades.csv

    Download CSV cu toate trade-urile (IS + OOS) ale job-ului.

    Coloane: fold, split, entry_ts, exit_ts, direction, entry_zscore,
             exit_zscore, hedge_ratio, qty_y, qty_x, entry_price_y,
             entry_price_x, exit_price_y, exit_price_x, gross_pnl,
             fees, slippage, funding_cost, net_pnl, bars_held, exit_reason
    """
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    if job["status"] != JobStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id!r} is {job['status']} — wait for status=done"
        )

    df: pd.DataFrame = job.get("trades_df", pd.DataFrame())

    if df is None or df.empty:
        # Return empty CSV with headers
        headers = [
            "fold", "split", "entry_ts", "exit_ts", "direction",
            "entry_zscore", "exit_zscore", "hedge_ratio", "qty_y", "qty_x",
            "entry_price_y", "entry_price_x", "exit_price_y", "exit_price_x",
            "gross_pnl", "fees", "slippage", "funding_cost", "net_pnl",
            "bars_held", "exit_reason",
        ]
        csv_content = ",".join(headers) + "\n"
    else:
        buf = io.StringIO()
        df.to_csv(buf, index=False, float_format="%.6f")
        csv_content = buf.getvalue()

    filename = f"quantluna_trades_{job_id}_{job['request'].sym_y}_{job['request'].sym_x}.csv"

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    """
    DELETE /api/backtest/jobs/{job_id}

    Șterge job-ul din memorie. Nu anulează un job running.
    """
    if job_id not in _JOBS:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    del _JOBS[job_id]

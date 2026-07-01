"""
api/backtest.py  —  QuantLuna Sprint 16 + Sprint 18 + Review Fixes

Fix-uri aplicate (code review):
  [FIX-1] _JOBS persistence: SQLite WAL via sqlite3 + JSON (zero extra deps).
          Toate job-urile sunt scrise/citite din quantluna_jobs.db la fiecare
          operație. La start, store-ul e rehidratat din DB.
  [FIX-2] sync=True nu mai blochează Uvicorn worker: rulează în
          ThreadPoolExecutor(max_workers=4) via asyncio.run_in_executor.
  [FIX-3] Evicție FIFO sigură: șterge NUMAI jobs cu status done/error,
          nu queued/running — previne ştergerea unui job în polling.
  [FIX-4] /compare OOM cap: max 50_000 rânduri per job în diff matrix +
          trades CSV; 422 dacă job depăşeşte limita.
  [FIX-5] __all__ exportat explicit pentru tree-shaking clar.
  [REVIEW-2] _DB_PATH citit din env var QUANTLUNA_DB_PATH pentru persistență
             Docker. DB supraviețuiește restart container când volumul e montat.

Endpoints:
  POST /api/backtest/run
  GET  /api/backtest/jobs/{job_id}
  GET  /api/backtest/jobs/{job_id}/trades.csv
  GET  /api/backtest/jobs
  DELETE /api/backtest/jobs/{job_id}
  GET  /api/backtest/compare
  GET  /api/backtest/compare/trades.csv
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.schemas import (
    BacktestMetrics,
    BacktestRequest,
    BacktestResponse,
    JobListItem,
    JobStatus,
)

__all__ = [
    "router",
    "CompareResponse",
    "JobSummary",
    "RadarData",
    "RadarSeries",
    "DiffMatrix",
    "ParamField",
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# ---------------------------------------------------------------------------
# [FIX-2] Thread pool for sync backtest runs (avoids blocking Uvicorn worker)
# ---------------------------------------------------------------------------

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bt-sync")

# ---------------------------------------------------------------------------
# [FIX-4] Safety caps
# ---------------------------------------------------------------------------

_COMPARE_MAX_ROWS_PER_JOB = 50_000  # max trade rows per job in compare ops
_MAX_JOBS = 100

# ---------------------------------------------------------------------------
# [FIX-1] SQLite persistence layer (zero extra deps — stdlib sqlite3)
# [REVIEW-2] _DB_PATH citit din env var QUANTLUNA_DB_PATH — DB supravietuieste
#            restart container cand volumul e montat la /app/data.
#            docker-compose.yml:
#              volumes: ["./data:/app/data"]
#              environment: ["QUANTLUNA_DB_PATH=/app/data/quantluna_jobs.db"]
# ---------------------------------------------------------------------------

_DB_PATH = Path(os.getenv("QUANTLUNA_DB_PATH", "quantluna_jobs.db"))


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id      TEXT PRIMARY KEY,
            status      TEXT NOT NULL,
            payload     TEXT NOT NULL,   -- JSON: everything except trades_df
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


_DB: sqlite3.Connection = _db_connect()


def _persist_job(job: Dict[str, Any]) -> None:
    """
    Persistă job în SQLite. trades_df nu e serializat (prea mare);
    se pierde la restart dar trades.csv poate fi re-generat dacă
    vrei să adaugi un mecanism de re-run viitor.
    """
    safe = {k: v for k, v in job.items() if k != "trades_df"}
    # BacktestRequest → dict pentru JSON
    if hasattr(safe.get("request"), "model_dump"):
        safe["request"] = safe["request"].model_dump(mode="json")
    payload = json.dumps(safe, default=str)
    _DB.execute(
        "INSERT OR REPLACE INTO jobs (job_id, status, payload, created_at) "
        "VALUES (?, ?, ?, ?)",
        (job["job_id"], str(job["status"]), payload, job["created_at"]),
    )
    _DB.commit()


def _load_jobs_from_db() -> Dict[str, Dict]:
    """
    Rehidrateză _JOBS la startup din SQLite.
    trades_df e setat la None (nu e persistat).
    """
    rows = _DB.execute(
        "SELECT payload FROM jobs ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    result: Dict[str, Dict] = {}
    for (payload_str,) in rows:
        try:
            data = json.loads(payload_str)
            data["trades_df"] = None  # not persisted
            # Re-hydrate BacktestRequest
            if isinstance(data.get("request"), dict):
                data["request"] = BacktestRequest(**data["request"])
            result[data["job_id"]] = data
        except Exception as exc:
            logger.warning(f"DB load skip: {exc}")
    return result


# In-memory job store — rehidratat din DB la import
_JOBS: Dict[str, Dict[str, Any]] = _load_jobs_from_db()

# ---------------------------------------------------------------------------
# Sprint 18 — Compare schemas
# ---------------------------------------------------------------------------

_COMPARE_METRICS_ALL = [
    "sharpe", "sortino", "calmar",
    "max_drawdown_pct", "win_rate", "profit_factor",
    "ann_return", "n_trades", "total_net_pnl",
]
_LOWER_IS_BETTER = {"max_drawdown_pct"}


class JobSummary(BaseModel):
    job_id: str
    sym_y: str
    sym_x: str
    bar_freq: str
    n_splits: int
    capital_usdt: float
    zscore_entry: float
    zscore_exit: float
    delta: float
    vol_target: float
    kelly_fraction: float
    n_bars: Optional[int] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    duration_s: Optional[float] = None
    created_at: str


class RadarSeries(BaseModel):
    job_id: str
    label: str
    values: List[float]


class RadarData(BaseModel):
    metrics: List[str]
    series: List[RadarSeries]
    raw_min: Dict[str, float]
    raw_max: Dict[str, float]


class DiffMatrix(BaseModel):
    job_ids: List[str]
    metrics: List[str]
    matrix: List[List[Dict[str, float]]]


class ParamField(BaseModel):
    param: str
    values: Dict[str, Any]
    all_equal: bool


class CompareResponse(BaseModel):
    job_ids: List[str]
    requested_metrics: List[str]
    rank_by: str
    best_job_id: str
    ranking: List[str]
    summary: List[JobSummary]
    radar: RadarData
    diff_matrix: DiffMatrix
    param_diff: List[ParamField]
    comparison_ts: str


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _job_to_response(job: Dict) -> BacktestResponse:
    req = job["request"]
    metrics_raw = job.get("metrics")
    metrics = BacktestMetrics(**metrics_raw) if metrics_raw else None
    trades_csv_url = (
        f"/api/backtest/jobs/{job['job_id']}/trades.csv"
        if job.get("trades_df") is not None
        else None
    )
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


def _generate_synthetic_prices(n: int, seed: int = 42, freq: str = "1h") -> pd.DataFrame:
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
    try:
        from config.strategy_config import StrategyConfig
    except ImportError:
        raise HTTPException(status_code=500, detail="config.strategy_config not available")
    kwargs = dict(
        sym_y=req.sym_y, sym_x=req.sym_x, bar_freq=req.bar_freq.value,
        capital_usdt=req.capital_usdt, vol_target=req.vol_target,
        kelly_fraction=req.kelly_fraction, max_leverage=req.max_leverage,
        zscore_entry=req.zscore_entry, zscore_exit=req.zscore_exit,
        zscore_window=req.zscore_window, warm_up_bars=req.warm_up_bars,
        delta=req.delta, observation_noise=req.observation_noise,
        fee_rate=req.fee_rate, slippage_pct=req.slippage_pct,
    )
    if req.params_file:
        try:
            from config.strategy_config import StrategyConfig
            return StrategyConfig.from_optimizer_json(req.params_file)
        except Exception as e:
            logger.warning(f"params_file load failed: {e} — using request params")
    from config.strategy_config import StrategyConfig
    return StrategyConfig(**{k: v for k, v in kwargs.items() if v is not None})


# ---------------------------------------------------------------------------
# Core backtest runner
# ---------------------------------------------------------------------------

def _run_backtest_job(job_id: str, req: BacktestRequest) -> None:
    """
    Rulează backtest complet şi actualizează job store + SQLite.
    Sigur de apelat din orice thread (ThreadPoolExecutor sau BackgroundTasks).
    """
    job = _JOBS.get(job_id)
    if not job:
        return

    job["status"] = JobStatus.RUNNING
    _persist_job(job)  # [FIX-1] persist running state
    t0 = time.monotonic()

    try:
        cfg = _build_strategy_config(req)

        if req.data_dir:
            from backtest.engine_adapter import BacktestEngine
            engine = BacktestEngine(cfg, n_splits=req.n_splits,
                                    purge_bars=req.purge_bars,
                                    embargo_bars=req.embargo_bars)
            result = engine.run(data_dir=Path(req.data_dir))
        else:
            n = req.n_bars or 2000
            df = _generate_synthetic_prices(n=n, freq=req.bar_freq.value)
            from backtest.engine_adapter import BacktestEngine
            engine = BacktestEngine(cfg, n_splits=req.n_splits,
                                    purge_bars=req.purge_bars,
                                    embargo_bars=req.embargo_bars)
            result = engine.run(df=df)

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

        trades_df = result.get("trades_df") or pd.DataFrame()

        job["status"]       = JobStatus.DONE
        job["metrics"]      = metrics
        job["trades_df"]    = trades_df
        job["duration_s"]   = round(time.monotonic() - t0, 3)
        job["completed_at"] = datetime.now(timezone.utc).isoformat()

        _persist_job(job)  # [FIX-1] persist completed state
        logger.info(
            f"[{job_id}] DONE in {job['duration_s']}s | "
            f"Sharpe={metrics['sharpe']:.2f} Trades={metrics['n_trades']}"
        )

    except Exception as exc:
        job["status"]       = JobStatus.ERROR
        job["error"]        = str(exc)
        job["duration_s"]   = round(time.monotonic() - t0, 3)
        job["completed_at"] = datetime.now(timezone.utc).isoformat()
        _persist_job(job)  # [FIX-1] persist error state
        logger.error(f"[{job_id}] ERROR: {exc}")


# ---------------------------------------------------------------------------
# Compare helpers
# ---------------------------------------------------------------------------

def _parse_metrics_param(raw: str) -> List[str]:
    requested = [m.strip().lower() for m in raw.split(",") if m.strip()]
    invalid = [m for m in requested if m not in _COMPARE_METRICS_ALL]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown metrics: {invalid}. Valid: {_COMPARE_METRICS_ALL}",
        )
    seen, result = set(), []
    for m in requested:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result or _COMPARE_METRICS_ALL


def _build_radar(summaries: List[JobSummary], metrics: List[str]) -> RadarData:
    raw_min: Dict[str, float] = {}
    raw_max: Dict[str, float] = {}
    for m in metrics:
        vals = [s.metrics.get(m, 0.0) for s in summaries]
        raw_min[m] = float(min(vals))
        raw_max[m] = float(max(vals))

    series: List[RadarSeries] = []
    for s in summaries:
        normalized = []
        for m in metrics:
            v, lo, hi = float(s.metrics.get(m, 0.0)), raw_min[m], raw_max[m]
            span = hi - lo
            norm = 0.5 if span == 0 else (v - lo) / span
            if m in _LOWER_IS_BETTER:
                norm = 1.0 - norm
            normalized.append(round(norm, 6))
        label = f"{s.sym_y}/{s.sym_x} {s.n_splits}f"
        series.append(RadarSeries(job_id=s.job_id, label=label, values=normalized))

    return RadarData(metrics=metrics, series=series, raw_min=raw_min, raw_max=raw_max)


def _build_diff_matrix(summaries: List[JobSummary], metrics: List[str]) -> DiffMatrix:
    n = len(summaries)
    job_ids = [s.job_id for s in summaries]
    matrix: List[List[Dict[str, float]]] = []
    for i in range(n):
        row: List[Dict[str, float]] = []
        for j in range(n):
            cell = {
                m: round(float(summaries[i].metrics.get(m, 0.0))
                         - float(summaries[j].metrics.get(m, 0.0)), 6)
                for m in metrics
            }
            row.append(cell)
        matrix.append(row)
    return DiffMatrix(job_ids=job_ids, metrics=metrics, matrix=matrix)


def _build_param_diff(summaries: List[JobSummary]) -> List[ParamField]:
    PARAM_FIELDS = [
        "sym_y", "sym_x", "bar_freq", "n_splits", "capital_usdt",
        "zscore_entry", "zscore_exit", "delta", "vol_target", "kelly_fraction",
    ]
    result: List[ParamField] = []
    for pf in PARAM_FIELDS:
        vals = {s.job_id: getattr(s, pf, None) for s in summaries}
        unique_vals = {
            v if not isinstance(v, float) else round(v, 8)
            for v in vals.values()
        }
        result.append(ParamField(param=pf, values=vals, all_equal=(len(unique_vals) == 1)))
    result.sort(key=lambda p: (p.all_equal, p.param))
    return result


# ---------------------------------------------------------------------------
# Endpoints — Sprint 16
# ---------------------------------------------------------------------------

@router.post("/run", response_model=BacktestResponse, status_code=202)
async def run_backtest(
    req: BacktestRequest,
    background_tasks: BackgroundTasks,
    sync: bool = Query(
        default=False,
        description="True → blochează până la finalizare (rulat în ThreadPool). "
                    "False (default) → răspuns imediat cu job_id.",
    ),
) -> BacktestResponse:
    """
    POST /api/backtest/run

    [FIX-2] sync=True rulează în ThreadPoolExecutor, nu în Uvicorn worker thread,
    deci nu blochează event loop-ul altor request-uri concurente.
    """
    # [FIX-3] Evict ONLY done/error jobs, never queued/running
    if len(_JOBS) >= _MAX_JOBS:
        evictable = [
            j for j in _JOBS.values()
            if j["status"] in (JobStatus.DONE, JobStatus.ERROR)
        ]
        evictable_sorted = sorted(evictable, key=lambda j: j["created_at"])[:10]
        for j in evictable_sorted:
            del _JOBS[j["job_id"]]
            try:
                _DB.execute("DELETE FROM jobs WHERE job_id = ?", (j["job_id"],))
            except Exception:
                pass
        _DB.commit()

    job_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    _JOBS[job_id] = {
        "job_id":       job_id,
        "status":       JobStatus.QUEUED,
        "request":      req,
        "metrics":      None,
        "trades_df":    None,
        "error":        None,
        "duration_s":   None,
        "created_at":   now,
        "completed_at": None,
    }
    _persist_job(_JOBS[job_id])  # [FIX-1]

    if sync:
        # [FIX-2] Run in ThreadPoolExecutor — non-blocking for event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_EXECUTOR, _run_backtest_job, job_id, req)
    else:
        background_tasks.add_task(_run_backtest_job, job_id, req)

    return _job_to_response(_JOBS[job_id])


@router.get("/jobs", response_model=List[JobListItem])
async def list_jobs(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> List[JobListItem]:
    jobs = sorted(_JOBS.values(), key=lambda j: j["created_at"], reverse=True)
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    jobs = jobs[:limit]
    result = []
    for j in jobs:
        req: BacktestRequest = j["request"]
        sharpe = j["metrics"].get("sharpe") if j.get("metrics") else None
        result.append(JobListItem(
            job_id=j["job_id"], status=j["status"],
            sym_y=req.sym_y, sym_x=req.sym_x,
            bar_freq=req.bar_freq.value, n_splits=req.n_splits,
            created_at=j["created_at"], duration_s=j.get("duration_s"),
            sharpe=sharpe,
        ))
    return result


@router.get("/jobs/{job_id}", response_model=BacktestResponse)
async def get_job(job_id: str) -> BacktestResponse:
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return _job_to_response(job)


@router.get("/jobs/{job_id}/trades.csv")
async def download_trades_csv(job_id: str) -> StreamingResponse:
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    if job["status"] != JobStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id!r} is {job['status']} — wait for status=done"
        )
    df: pd.DataFrame = job.get("trades_df") or pd.DataFrame()
    if df.empty:
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

    filename = (
        f"quantluna_trades_{job_id}"
        f"_{job['request'].sym_y}_{job['request'].sym_x}.csv"
    )
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    if job_id not in _JOBS:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    del _JOBS[job_id]
    try:
        _DB.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        _DB.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Sprint 18 — Compare endpoint
# ---------------------------------------------------------------------------

@router.get("/compare", response_model=CompareResponse)
async def compare_jobs(
    job_ids: str = Query(..., description="Comma-separated 2–10 job IDs (all status=done)."),
    metrics: str = Query(
        default="sharpe,sortino,calmar,max_drawdown_pct,win_rate,profit_factor",
    ),
    rank_by: str = Query(default="sharpe"),
    include_trades_diff: bool = Query(default=False),
) -> CompareResponse:
    """
    GET /api/backtest/compare?job_ids=id1,id2,id3

    [FIX-4] Dacă orice job depăşeşte _COMPARE_MAX_ROWS_PER_JOB trade-uri,
    returneaz 422 cu mesaj explicit — previne OOM la 10 × DF mari.
    """
    ids = [jid.strip() for jid in job_ids.split(",") if jid.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=422, detail=f"compare requires at least 2 job_ids (got {len(ids)})")
    if len(ids) > 10:
        raise HTTPException(status_code=422, detail=f"compare supports max 10 jobs (got {len(ids)})")

    jobs_resolved: List[Dict] = []
    for jid in ids:
        job = _JOBS.get(jid)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {jid!r} not found")
        if job["status"] != JobStatus.DONE:
            raise HTTPException(
                status_code=409,
                detail=f"Job {jid!r} has status={job['status']} — must be done",
            )
        # [FIX-4] OOM cap
        df = job.get("trades_df")
        if df is not None and not df.empty and len(df) > _COMPARE_MAX_ROWS_PER_JOB:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Job {jid!r} has {len(df):,} trade rows — exceeds "
                    f"compare cap of {_COMPARE_MAX_ROWS_PER_JOB:,}. "
                    "Use /jobs/{job_id}/trades.csv for large exports."
                ),
            )
        jobs_resolved.append(job)

    requested_metrics = _parse_metrics_param(metrics)

    if rank_by not in _COMPARE_METRICS_ALL:
        raise HTTPException(
            status_code=422,
            detail=f"rank_by={rank_by!r} not in valid metrics: {_COMPARE_METRICS_ALL}",
        )
    if rank_by not in requested_metrics:
        requested_metrics = [rank_by] + requested_metrics

    summaries: List[JobSummary] = []
    for job in jobs_resolved:
        req: BacktestRequest = job["request"]
        raw_metrics = job.get("metrics") or {}
        extra: Dict[str, Any] = {}
        if include_trades_diff and job.get("trades_df") is not None:
            df = job["trades_df"]
            if not df.empty and "fold" in df.columns:
                extra["trades_per_fold"] = df.groupby("fold").size().to_dict()

        metrics_payload = {m: raw_metrics.get(m, 0.0) for m in requested_metrics}
        metrics_payload.update(extra)

        summaries.append(JobSummary(
            job_id=job["job_id"], sym_y=req.sym_y, sym_x=req.sym_x,
            bar_freq=req.bar_freq.value, n_splits=req.n_splits,
            capital_usdt=req.capital_usdt, zscore_entry=req.zscore_entry,
            zscore_exit=req.zscore_exit, delta=req.delta,
            vol_target=req.vol_target, kelly_fraction=req.kelly_fraction,
            n_bars=req.n_bars, metrics=metrics_payload,
            duration_s=job.get("duration_s"), created_at=job["created_at"],
        ))

    def _rank_key(s: JobSummary) -> float:
        v = float(s.metrics.get(rank_by, 0.0))
        return -v if rank_by in _LOWER_IS_BETTER else v

    summaries_sorted = sorted(summaries, key=_rank_key, reverse=True)
    ranking = [s.job_id for s in summaries_sorted]
    best_job_id = ranking[0]

    return CompareResponse(
        job_ids=ids,
        requested_metrics=requested_metrics,
        rank_by=rank_by,
        best_job_id=best_job_id,
        ranking=ranking,
        summary=summaries,
        radar=_build_radar(summaries, requested_metrics),
        diff_matrix=_build_diff_matrix(summaries, requested_metrics),
        param_diff=_build_param_diff(summaries),
        comparison_ts=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/compare/trades.csv")
async def compare_download_csv(
    job_ids: str = Query(...),
    split: Optional[str] = Query(default=None),
) -> StreamingResponse:
    """
    GET /api/backtest/compare/trades.csv?job_ids=a,b,c

    [FIX-4] Acelaşi OOM cap aplicat per job înainte de concat.
    """
    ids = [jid.strip() for jid in job_ids.split(",") if jid.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=422, detail="Requires at least 2 job_ids")
    if len(ids) > 10:
        raise HTTPException(status_code=422, detail="Max 10 job_ids")

    frames: List[pd.DataFrame] = []
    for jid in ids:
        job = _JOBS.get(jid)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {jid!r} not found")
        if job["status"] != JobStatus.DONE:
            raise HTTPException(
                status_code=409,
                detail=f"Job {jid!r} status={job['status']} — must be done",
            )
        df = job.get("trades_df")
        if df is not None and not df.empty:
            if len(df) > _COMPARE_MAX_ROWS_PER_JOB:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Job {jid!r} has {len(df):,} rows — exceeds cap "
                        f"{_COMPARE_MAX_ROWS_PER_JOB:,}. Use single-job CSV."
                    ),
                )
            df = df.copy()
            df.insert(0, "job_id", jid)
            df.insert(1, "sym_y", job["request"].sym_y)
            df.insert(2, "sym_x", job["request"].sym_x)
            frames.append(df)

    if not frames:
        combined_csv = "job_id,sym_y,sym_x\n"
    else:
        combined = pd.concat(frames, ignore_index=True)
        if split:
            split_upper = split.upper()
            if "split" in combined.columns:
                combined = combined[combined["split"] == split_upper]
        buf = io.StringIO()
        combined.to_csv(buf, index=False, float_format="%.6f")
        combined_csv = buf.getvalue()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = f"quantluna_compare_{ts}.csv"

    def _stream():
        chunk_size = 65536
        for i in range(0, len(combined_csv), chunk_size):
            yield combined_csv[i: i + chunk_size]

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

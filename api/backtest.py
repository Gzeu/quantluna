"""
api/backtest.py  —  QuantLuna Sprint 16 + Sprint 18

FastAPI router pentru backtest REST API.

Endpoints (Sprint 16):
  POST /api/backtest/run
  GET  /api/backtest/jobs/{job_id}
  GET  /api/backtest/jobs/{job_id}/trades.csv
  GET  /api/backtest/jobs
  DELETE /api/backtest/jobs/{job_id}

Endpoints (Sprint 18 — multi-run comparison):
  GET  /api/backtest/compare
      ?job_ids=id1,id2,id3  (2–10 job_ids, toate trebuie să fie status=done)
      ?metrics=sharpe,sortino,calmar,max_drawdown_pct,win_rate,profit_factor,ann_return
      ?rank_by=sharpe  (metrica principală pentru ranking, default sharpe)
      ?include_trades_diff=false

      Returnează CompareResponse:
        - summary: List[JobSummary]  (fiecare job cu toate metricile cerute)
        - ranking: List[str]         (job_ids sorted by rank_by desc)
        - radar: RadarData           (normalizat 0-1 per metrica, gata pentru Plotly radar)
        - diff_matrix: DiffMatrix    (pairwise diff a[i] - a[j] pe fiecare metrica)
        - param_diff: ParamDiff      (ce parametri differ între joburi)
        - best_job_id: str
        - comparison_ts: str

Design:
  - Job store in-memory (dict); pentru producție înlocuiți cu Redis/DB.
  - BacktestEngine din backtest.engine_adapter.
  - Date sintetice dacă data_dir lipsă (CI/dev friendly).
  - CSV generat on-demand via pandas to_csv(StringIO).
  - Background tasks prin FastAPI BackgroundTasks.
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
from pydantic import BaseModel, Field

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

# ---------------------------------------------------------------------------
# Sprint 18 — Compare schemas (inline, no separate file needed)
# ---------------------------------------------------------------------------

_COMPARE_METRICS_ALL = [
    "sharpe", "sortino", "calmar",
    "max_drawdown_pct", "win_rate", "profit_factor",
    "ann_return", "n_trades", "total_net_pnl",
]

# Metrics where LOWER is better (used for normalization inversion)
_LOWER_IS_BETTER = {"max_drawdown_pct"}


class JobSummary(BaseModel):
    """Metrics + key params for one job in a comparison."""
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
    """One job's normalized scores for all requested metrics (0–1)."""
    job_id: str
    label: str  # e.g. "BTCUSDT/ETHUSDT 5f"
    values: List[float]  # same order as `metrics` field in RadarData


class RadarData(BaseModel):
    """Plotly-ready radar chart data."""
    metrics: List[str]          # axis labels
    series: List[RadarSeries]   # one per job
    raw_min: Dict[str, float]   # raw min per metric (for tooltip denormalization)
    raw_max: Dict[str, float]   # raw max per metric


class DiffMatrix(BaseModel):
    """
    Pairwise difference matrix.
    cell[i][j][metric] = jobs[i].metric - jobs[j].metric
    """
    job_ids: List[str]
    metrics: List[str]
    matrix: List[List[Dict[str, float]]]  # [i][j] → {metric: diff}


class ParamField(BaseModel):
    param: str
    values: Dict[str, Any]  # job_id → value
    all_equal: bool


class CompareResponse(BaseModel):
    """Full multi-run comparison payload."""
    job_ids: List[str]
    requested_metrics: List[str]
    rank_by: str
    best_job_id: str
    ranking: List[str]              # job_ids sorted best → worst by rank_by
    summary: List[JobSummary]
    radar: RadarData
    diff_matrix: DiffMatrix
    param_diff: List[ParamField]    # only params that differ across jobs
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
    if req.params_file:
        try:
            cfg = StrategyConfig.from_optimizer_json(req.params_file)
            return cfg
        except Exception as e:
            logger.warning(f"params_file load failed: {e} — using request params")
    return StrategyConfig(**{k: v for k, v in kwargs.items() if v is not None})


# ---------------------------------------------------------------------------
# Core backtest runner
# ---------------------------------------------------------------------------

def _run_backtest_job(job_id: str, req: BacktestRequest) -> None:
    job = _JOBS.get(job_id)
    if not job:
        return

    job["status"] = JobStatus.RUNNING
    t0 = time.monotonic()

    try:
        cfg = _build_strategy_config(req)

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
# Sprint 18 — Compare helpers
# ---------------------------------------------------------------------------

def _parse_metrics_param(raw: str) -> List[str]:
    """
    Parsează query param metrics='sharpe,sortino,calmar'.
    Validează că fiecare metric e în _COMPARE_METRICS_ALL.
    Returnează lista deduplicată, ordinea păstrată.
    """
    requested = [m.strip().lower() for m in raw.split(",") if m.strip()]
    invalid = [m for m in requested if m not in _COMPARE_METRICS_ALL]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown metrics: {invalid}. Valid: {_COMPARE_METRICS_ALL}",
        )
    # dedup preserving order
    seen, result = set(), []
    for m in requested:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result or _COMPARE_METRICS_ALL


def _build_radar(summaries: List[JobSummary], metrics: List[str]) -> RadarData:
    """
    Normalizează metricile 0–1 (min-max per metrica).
    Pentru metrici lower-is-better (max_drawdown_pct) inversează scala.
    """
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
            v    = float(s.metrics.get(m, 0.0))
            lo   = raw_min[m]
            hi   = raw_max[m]
            span = hi - lo
            if span == 0:
                norm = 0.5  # all equal → mid
            else:
                norm = (v - lo) / span
                if m in _LOWER_IS_BETTER:
                    norm = 1.0 - norm  # invert: lower raw → higher score
            normalized.append(round(norm, 6))

        label = f"{s.sym_y}/{s.sym_x} {s.n_splits}f"
        series.append(RadarSeries(job_id=s.job_id, label=label, values=normalized))

    return RadarData(metrics=metrics, series=series, raw_min=raw_min, raw_max=raw_max)


def _build_diff_matrix(
    summaries: List[JobSummary],
    metrics: List[str],
) -> DiffMatrix:
    """
    Pairwise diff matrix: matrix[i][j][metric] = summaries[i].metric - summaries[j].metric
    Diagonal is always 0.0.
    """
    n = len(summaries)
    job_ids = [s.job_id for s in summaries]
    matrix: List[List[Dict[str, float]]] = []

    for i in range(n):
        row: List[Dict[str, float]] = []
        for j in range(n):
            cell = {}
            for m in metrics:
                vi = float(summaries[i].metrics.get(m, 0.0))
                vj = float(summaries[j].metrics.get(m, 0.0))
                cell[m] = round(vi - vj, 6)
            row.append(cell)
        matrix.append(row)

    return DiffMatrix(job_ids=job_ids, metrics=metrics, matrix=matrix)


def _build_param_diff(summaries: List[JobSummary]) -> List[ParamField]:
    """
    Confrontă parametrii între joburi, returnează doar cei care diferă.
    Câmpuri comparate: zscore_entry, zscore_exit, delta, vol_target,
    kelly_fraction, capital_usdt, n_splits, bar_freq, sym_y, sym_x.
    """
    PARAM_FIELDS = [
        "sym_y", "sym_x", "bar_freq", "n_splits", "capital_usdt",
        "zscore_entry", "zscore_exit", "delta", "vol_target",
        "kelly_fraction",
    ]
    result: List[ParamField] = []
    for pf in PARAM_FIELDS:
        vals = {s.job_id: getattr(s, pf, None) for s in summaries}
        unique_vals = set(
            v if not isinstance(v, float) else round(v, 8)
            for v in vals.values()
        )
        result.append(ParamField(
            param=pf,
            values=vals,
            all_equal=(len(unique_vals) == 1),
        ))
    # Sort: differing params first
    result.sort(key=lambda p: (p.all_equal, p.param))
    return result


# ---------------------------------------------------------------------------
# Endpoints — Sprint 16 (unchanged)
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

    Curl example::

        curl -X POST http://localhost:8000/api/backtest/run \\
          -H 'Content-Type: application/json' \\
          -d '{"sym_y": "BTCUSDT", "sym_x": "ETHUSDT", "n_splits": 3, "n_bars": 1000}'
    """
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
        _run_backtest_job(job_id, req)
    else:
        background_tasks.add_task(_run_backtest_job, job_id, req)

    return _job_to_response(_JOBS[job_id])


@router.get("/jobs", response_model=List[JobListItem])
async def list_jobs(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> List[JobListItem]:
    """GET /api/backtest/jobs — listează job-urile recente."""
    jobs = sorted(_JOBS.values(), key=lambda j: j["created_at"], reverse=True)
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    jobs = jobs[:limit]

    result = []
    for j in jobs:
        req: BacktestRequest = j["request"]
        sharpe = j["metrics"].get("sharpe") if j.get("metrics") else None
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
    """GET /api/backtest/jobs/{job_id} — status + metrics."""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return _job_to_response(job)


@router.get("/jobs/{job_id}/trades.csv")
async def download_trades_csv(job_id: str) -> StreamingResponse:
    """GET /api/backtest/jobs/{job_id}/trades.csv — download CSV."""
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
    """DELETE /api/backtest/jobs/{job_id} — șterge job din memorie."""
    if job_id not in _JOBS:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    del _JOBS[job_id]


# ---------------------------------------------------------------------------
# Sprint 18 — Compare endpoint
# ---------------------------------------------------------------------------

@router.get("/compare", response_model=CompareResponse)
async def compare_jobs(
    job_ids: str = Query(
        ...,
        description="Comma-separated list of 2–10 job IDs to compare. "
                    "All must have status=done.",
        example="a1b2c3d4,e5f6g7h8,i9j0k1l2",
    ),
    metrics: str = Query(
        default="sharpe,sortino,calmar,max_drawdown_pct,win_rate,profit_factor",
        description="Comma-separated metrics to include in comparison. "
                    f"Valid: {_COMPARE_METRICS_ALL}",
    ),
    rank_by: str = Query(
        default="sharpe",
        description="Primary metric for ranking (higher = better, "
                    "except max_drawdown_pct where lower = better).",
    ),
    include_trades_diff: bool = Query(
        default=False,
        description="Se True, agrega trade-count per fold pentru fiecare job "
                    "(nu include raw trades).",
    ),
) -> CompareResponse:
    """
    GET /api/backtest/compare?job_ids=id1,id2,id3

    Compară N backtests side-by-side.

    Returnează:
    - **summary**: metrici complete pentru fiecare job
    - **ranking**: job_ids sorted by `rank_by`
    - **radar**: date normalizate 0-1 per metrica pentru Plotly radar chart
    - **diff_matrix**: diferențe pairwise `jobs[i].metric - jobs[j].metric`
    - **param_diff**: parametrii care diferă între joburi (differing first)
    - **best_job_id**: job-ul cu cel mai bun `rank_by`

    Curl example::

        curl "http://localhost:8000/api/backtest/compare?job_ids=aabb1122,ccdd3344&rank_by=sharpe"
    """
    # ── Parse job_ids ──
    ids = [jid.strip() for jid in job_ids.split(",") if jid.strip()]
    if len(ids) < 2:
        raise HTTPException(
            status_code=422,
            detail="compare requires at least 2 job_ids (got {len(ids)})",
        )
    if len(ids) > 10:
        raise HTTPException(
            status_code=422,
            detail=f"compare supports max 10 jobs (got {len(ids)})",
        )

    # ── Resolve jobs ──
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
        jobs_resolved.append(job)

    # ── Parse + validate metrics ──
    requested_metrics = _parse_metrics_param(metrics)

    # ── Validate rank_by ──
    if rank_by not in _COMPARE_METRICS_ALL:
        raise HTTPException(
            status_code=422,
            detail=f"rank_by={rank_by!r} not in valid metrics: {_COMPARE_METRICS_ALL}",
        )
    if rank_by not in requested_metrics:
        # auto-add so it's always in the comparison
        requested_metrics = [rank_by] + requested_metrics

    # ── Build summaries ──
    summaries: List[JobSummary] = []
    for job in jobs_resolved:
        req: BacktestRequest = job["request"]
        raw_metrics = job.get("metrics") or {}

        # Fold-level trade counts if requested
        extra: Dict[str, Any] = {}
        if include_trades_diff and job.get("trades_df") is not None:
            df: pd.DataFrame = job["trades_df"]
            if not df.empty and "fold" in df.columns:
                fold_counts = df.groupby("fold").size().to_dict()
                extra["trades_per_fold"] = fold_counts

        metrics_payload = {m: raw_metrics.get(m, 0.0) for m in requested_metrics}
        metrics_payload.update(extra)

        summaries.append(JobSummary(
            job_id=job["job_id"],
            sym_y=req.sym_y,
            sym_x=req.sym_x,
            bar_freq=req.bar_freq.value,
            n_splits=req.n_splits,
            capital_usdt=req.capital_usdt,
            zscore_entry=req.zscore_entry,
            zscore_exit=req.zscore_exit,
            delta=req.delta,
            vol_target=req.vol_target,
            kelly_fraction=req.kelly_fraction,
            n_bars=req.n_bars,
            metrics=metrics_payload,
            duration_s=job.get("duration_s"),
            created_at=job["created_at"],
        ))

    # ── Ranking ──
    def _rank_key(s: JobSummary) -> float:
        v = float(s.metrics.get(rank_by, 0.0))
        # lower-is-better: invert sign for consistent sort (desc)
        return -v if rank_by in _LOWER_IS_BETTER else v

    summaries_sorted = sorted(summaries, key=_rank_key, reverse=True)
    ranking = [s.job_id for s in summaries_sorted]
    best_job_id = ranking[0]

    # ── Radar ──
    radar = _build_radar(summaries, requested_metrics)

    # ── Diff matrix ──
    diff_matrix = _build_diff_matrix(summaries, requested_metrics)

    # ── Param diff ──
    param_diff = _build_param_diff(summaries)

    return CompareResponse(
        job_ids=ids,
        requested_metrics=requested_metrics,
        rank_by=rank_by,
        best_job_id=best_job_id,
        ranking=ranking,
        summary=summaries,
        radar=radar,
        diff_matrix=diff_matrix,
        param_diff=param_diff,
        comparison_ts=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Sprint 18 — Multi-job CSV export
# ---------------------------------------------------------------------------

@router.get("/compare/trades.csv")
async def compare_download_csv(
    job_ids: str = Query(
        ...,
        description="Comma-separated job IDs (2–10, all must be status=done).",
    ),
    split: Optional[str] = Query(
        default=None,
        description="Filtrează: 'OOS', 'IS', sau None pentru toate.",
    ),
) -> StreamingResponse:
    """
    GET /api/backtest/compare/trades.csv?job_ids=a,b,c

    Descarcă un CSV combinat cu trade-urile tuturor job-urilor comparate.
    Adaugă coloana `job_id` pentru identificare, plus `sym_y`, `sym_x`.
    Streamed via generator pentru memorie minimă.

    Curl example::

        curl -o compare.csv \\
          "http://localhost:8000/api/backtest/compare/trades.csv?job_ids=aabb1122,ccdd3344"
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
            df = df.copy()
            df.insert(0, "job_id", jid)
            df.insert(1, "sym_y", job["request"].sym_y)
            df.insert(2, "sym_x", job["request"].sym_x)
            frames.append(df)

    if not frames:
        # All jobs have empty trade DataFrames
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
        chunk_size = 65536  # 64 KB
        for i in range(0, len(combined_csv), chunk_size):
            yield combined_csv[i: i + chunk_size]

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

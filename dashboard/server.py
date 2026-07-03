"""
dashboard/server.py  —  QuantLuna FastAPI Dashboard Server

Sprint 13 additions:
  - GET /api/health        — system health summary
  - GET /api/optimize/results  — Optuna trial history (vizualizare)
  - WebSocket /ws/live     — real-time state push la fiecare 1s
  - CORS configurat pentru dev frontend

Sprint 16 additions:
  - Backtest REST API montat via api.backtest router:
    POST   /api/backtest/run
    GET    /api/backtest/jobs/{job_id}
    GET    /api/backtest/jobs/{job_id}/trades.csv
    GET    /api/backtest/jobs
    DELETE /api/backtest/jobs/{job_id}

Sprint 20 additions:
  - GET /metrics           — Prometheus exposition format
  - GET /api/metrics       — JSON metrics summary
  - Lightweight rate limiter for public API endpoints

All original endpoints preserved.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

try:
    from core.state_bus import bus
except ImportError:
    from state_bus import bus  # legacy fallback

from core.metrics import (
    active_positions,
    drawdown_pct,
    pnl_usdt,
    registry,
    websocket_clients,
)

logger = logging.getLogger(__name__)

_REQUEST_WINDOW_SEC = 60.0
_REQUEST_LIMIT = 120
_request_times: Deque[float] = deque()


def _rate_limit_check() -> None:
    now = time.time()
    while _request_times and now - _request_times[0] > _REQUEST_WINDOW_SEC:
        _request_times.popleft()
    if len(_request_times) >= _REQUEST_LIMIT:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    _request_times.append(now)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantLuna Dashboard starting")
    yield
    logger.info("QuantLuna Dashboard shutting down")


app = FastAPI(
    title="QuantLuna Dashboard",
    description="Adaptive Kalman Filter Pairs Trading — Monitoring & Backtest API",
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = os.path.join(os.path.dirname(__file__))
try:
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
except Exception:
    pass

try:
    from api.backtest import router as backtest_router
    app.include_router(backtest_router)
    logger.info("Backtest API router mounted at /api/backtest")
except ImportError as _e:
    logger.warning(f"Backtest router not mounted: {_e}")


@app.middleware("http")
async def add_rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/") or request.url.path == "/metrics":
        _rate_limit_check()
    return await call_next(request)


@app.get("/")
async def root() -> HTMLResponse:
    html_path = os.path.join(_static_dir, "index.html")
    try:
        with open(html_path) as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>QuantLuna Dashboard</h1><p>index.html not found.</p>")


@app.get("/api/status")
async def api_status() -> Dict[str, Any]:
    state = bus.snapshot_dict()
    active_positions.set(len(bus.get_positions()))
    pnl_usdt.set(float(state.get("pnl_usdt", 0.0)))
    drawdown_pct.set(float(state.get("drawdown", 0.0)))
    return state


@app.get("/api/positions")
async def api_positions() -> Dict[str, Any]:
    positions = bus.get_positions()
    active_positions.set(len(positions))
    return {
        "count": len(positions),
        "positions": [
            {
                "pair": p.pair,
                "direction": p.direction,
                "qty_y": p.qty_y,
                "qty_x": p.qty_x,
                "notional_usdt": p.notional_usdt,
                "hedge_ratio": p.hedge_ratio,
                "entry_ts": p.entry_ts,
            }
            for p in positions
        ],
    }


@app.get("/api/performance")
async def api_performance() -> Dict[str, Any]:
    return {
        "equity_curve": bus.get_equity_curve(),
        "recent_trades": bus.get_recent_trades()[-50:],
    }


@app.get("/api/health")
async def api_health() -> Dict[str, Any]:
    state = bus.snapshot_dict()
    status = state.get("status", "UNKNOWN")
    if status in ("RUNNING", "IDLE"):
        health_status = "ok"
    elif status in ("HALT", "HARD_STOP"):
        health_status = "error"
    else:
        health_status = "degraded"
    return {
        "status": health_status,
        "trading_status": status,
        "pnl_usdt": state.get("pnl_usdt", 0.0),
        "drawdown": state.get("drawdown", 0.0),
        "n_trades": state.get("n_trades", 0),
        "last_update": state.get("last_update"),
    }


@app.get("/api/metrics")
async def api_metrics() -> Dict[str, Any]:
    state = bus.snapshot_dict()
    positions = bus.get_positions()
    active_positions.set(len(positions))
    pnl_usdt.set(float(state.get("pnl_usdt", 0.0)))
    drawdown_pct.set(float(state.get("drawdown", 0.0)))
    return {
        "active_positions": len(positions),
        "pnl_usdt": float(state.get("pnl_usdt", 0.0)),
        "drawdown_pct": float(state.get("drawdown", 0.0)),
        "websocket_clients": len(_ws_manager.active),
    }


@app.get("/metrics")
async def prometheus_metrics() -> PlainTextResponse:
    return PlainTextResponse(registry.render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/api/optimize/results")
async def api_optimize_results(
    storage: Optional[str] = Query(default=None, description="Optuna storage URL (e.g. sqlite:///optuna.db)"),
    study_name: str = Query(default="quantluna_opt", description="Optuna study name"),
    top_n: int = Query(default=50, ge=1, le=500, description="Number of top trials to return"),
) -> Dict[str, Any]:
    if not storage:
        default_paths = ["sqlite:///optuna.db", "sqlite:///data/optuna.db"]
        for default in default_paths:
            db_path = default.replace("sqlite:///", "")
            import os as _os
            if _os.path.exists(db_path):
                storage = default
                break

    if not storage:
        return {
            "study_name": study_name,
            "storage": None,
            "n_trials": 0,
            "best_value": None,
            "best_params": {},
            "trials": [],
            "message": "No Optuna storage found. Run optimizer first: python main.py optimize --pair BTCUSDT ETHUSDT",
        }

    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        try:
            study = optuna.load_study(study_name=study_name, storage=storage)
        except Exception as exc:
            return {
                "study_name": study_name,
                "storage": storage,
                "n_trials": 0,
                "best_value": None,
                "best_params": {},
                "trials": [],
                "error": str(exc),
            }

        completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        completed.sort(key=lambda t: t.value or 0, reverse=True)
        trials_data = []
        for t in completed[:top_n]:
            duration = (
                (t.datetime_complete - t.datetime_start).total_seconds()
                if t.datetime_complete and t.datetime_start else None
            )
            trials_data.append({
                "number": t.number,
                "value": round(t.value, 4) if t.value is not None else None,
                "state": t.state.name,
                "params": {k: round(v, 6) if isinstance(v, float) else v for k, v in t.params.items()},
                "duration_s": round(duration, 2) if duration else None,
                "datetime_start": t.datetime_start.isoformat() if t.datetime_start else None,
            })

        best_value = None
        best_params = {}
        try:
            best_value = round(study.best_value, 4)
            best_params = study.best_params
        except Exception:
            pass

        param_importances = {}
        if len(completed) >= 10:
            try:
                importances = optuna.importance.get_param_importances(study)
                param_importances = {k: round(v, 4) for k, v in importances.items()}
            except Exception:
                pass

        return {
            "study_name": study_name,
            "storage": storage,
            "n_trials": len(study.trials),
            "n_complete": len(completed),
            "n_pruned": sum(1 for t in study.trials if t.state.name == "PRUNED"),
            "best_value": best_value,
            "best_params": best_params,
            "objective_direction": "maximize",
            "trials": trials_data,
            "param_importances": param_importances,
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="Optuna not installed.")
    except Exception as exc:
        logger.error(f"optimize/results error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class _WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        websocket_clients.set(len(self.active))
        logger.info(f"WS client connected (total: {len(self.active)})")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        websocket_clients.set(len(self.active))
        logger.info(f"WS client disconnected (total: {len(self.active)})")

    async def broadcast(self, data: Dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_ws_manager = _WSManager()


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await _ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(1.0)
            snapshot = bus.snapshot_dict()
            await websocket.send_json(snapshot)
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.warning(f"WS error: {exc}")
        _ws_manager.disconnect(websocket)

"""
dashboard/server.py  —  QuantLuna FastAPI Dashboard Server

Sprint 13 additions:
  - GET /api/health        — system health summary
  - GET /api/optimize/results  — Optuna trial history (vizualizare)
  - WebSocket /ws/live     — real-time state push la fiecare 1s
  - CORS configurat pentru dev frontend

All original endpoints preserved.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

try:
    from core.state_bus import bus
except ImportError:
    from state_bus import bus  # legacy fallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantLuna Dashboard starting")
    yield
    logger.info("QuantLuna Dashboard shutting down")


app = FastAPI(
    title="QuantLuna Dashboard",
    description="Adaptive Kalman Filter Pairs Trading — Monitoring API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (HTML dashboard)
_static_dir = os.path.join(os.path.dirname(__file__))
try:
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
except Exception:
    pass


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------

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
    """Current trading state snapshot."""
    return bus.snapshot_dict()


@app.get("/api/positions")
async def api_positions() -> Dict[str, Any]:
    """Active positions."""
    positions = bus.get_positions()
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
    """Equity curve + recent trades."""
    return {
        "equity_curve": bus.get_equity_curve(),
        "recent_trades": bus.get_recent_trades()[-50:],
    }


# ---------------------------------------------------------------------------
# Sprint 13 — New endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def api_health() -> Dict[str, Any]:
    """
    Quick system health endpoint.
    Returns a summary based on current state bus + basic sanity checks.
    """
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


@app.get("/api/optimize/results")
async def api_optimize_results(
    storage: Optional[str] = Query(default=None, description="Optuna storage URL (e.g. sqlite:///optuna.db)"),
    study_name: str = Query(default="quantluna_opt", description="Optuna study name"),
    top_n: int = Query(default=50, ge=1, le=500, description="Number of top trials to return"),
) -> Dict[str, Any]:
    """
    Returns Optuna trial history pentru vizualizare în dashboard.

    Response:
    {
        "study_name": "quantluna_opt",
        "storage": "sqlite:///optuna.db",
        "n_trials": 150,
        "best_value": 1.82,
        "best_params": {...},
        "objective_direction": "maximize",
        "trials": [
            {
                "number": 0,
                "value": 1.45,
                "state": "COMPLETE",
                "params": {...},
                "duration_s": 2.3
            },
            ...
        ],
        "param_importances": {"zscore_entry": 0.42, "delta": 0.31, ...}  // if available
    }
    """
    if not storage:
        # Try default locations
        default_paths = [
            "sqlite:///optuna.db",
            "sqlite:///data/optuna.db",
        ]
        for default in default_paths:
            db_path = default.replace("sqlite:///", "")
            import os
            if os.path.exists(db_path):
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

        completed = [
            t for t in study.trials
            if t.state == optuna.trial.TrialState.COMPLETE
        ]
        completed.sort(key=lambda t: t.value or 0, reverse=True)

        trials_data = []
        for t in completed[:top_n]:
            duration = (
                (t.datetime_complete - t.datetime_start).total_seconds()
                if t.datetime_complete and t.datetime_start
                else None
            )
            trials_data.append({
                "number": t.number,
                "value": round(t.value, 4) if t.value is not None else None,
                "state": t.state.name,
                "params": {k: round(v, 6) if isinstance(v, float) else v
                           for k, v in t.params.items()},
                "duration_s": round(duration, 2) if duration else None,
                "datetime_start": t.datetime_start.isoformat() if t.datetime_start else None,
            })

        # Best trial
        best_value = None
        best_params = {}
        try:
            best_value = round(study.best_value, 4)
            best_params = study.best_params
        except Exception:
            pass

        # Parameter importances (Optuna fanova — optional)
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
        raise HTTPException(
            status_code=501,
            detail="Optuna not installed. Run: pip install optuna"
        )
    except Exception as exc:
        logger.error(f"optimize/results error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# WebSocket — real-time state push
# ---------------------------------------------------------------------------

class _WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WS client connected (total: {len(self.active)})")

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        logger.info(f"WS client disconnected (total: {len(self.active)})")

    async def broadcast(self, data: Dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


_ws_manager = _WSManager()


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """
    WebSocket endpoint — pushes state snapshot every 1 second.
    Connect: ws://localhost:8000/ws/live
    """
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

"""
dashboard/server.py  —  QuantLuna FastAPI Dashboard Server v2.0
Sprint S20 — 2026-07-11

Changelog S20:
  - /ws/live: push real-time din state_bus queue (nu mai e polling 1s)
  - /api/stream: SSE fallback pentru browsere fără WebSocket
  - /api/warmup: warm-up progress endpoint
  - /metrics: Prometheus gauges extinse (zscore_pair, circuit_breaker_open,
    warmup_bars_done, active_strategy, vol_regime)
  - _WSManager: broadcast asyncio.Queue-based (zero drift față de runner)
  - Backward compatible: /api/status, /api/positions, /api/performance
    funcționează în continuare cu polling dacă WS nu e conectat

Endpoints originale păstrate:
  Sprint 13: /api/health, /api/optimize/results, /ws/live, CORS
  Sprint 16: /api/backtest/* via router
  Sprint 20: /metrics, /api/metrics, rate limiter
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from core.state_bus import bus
except ImportError:
    from core.state_bus import bus  # canonical

from core.metrics import (
    active_positions,
    drawdown_pct,
    pnl_usdt,
    registry,
    websocket_clients,
    spread_zscore,
)

try:
    _zscore_pair = registry.gauge("quantluna_zscore_pair", "Z-score abs per pair")
    _circuit_open = registry.gauge("quantluna_circuit_breaker_open", "Circuit breaker open 0/1")
    _warmup_bars_done = registry.gauge("quantluna_warmup_bars_done", "Warm-up bars completed")
    _active_strategy_gauge = registry.gauge("quantluna_active_strategy_score", "Active strategy score")
    _vol_regime_gauge = registry.gauge("quantluna_vol_regime_high", "Vol regime is HIGH (1) or not (0)")
except Exception:
    pass

logger = logging.getLogger(__name__)

_REQUEST_WINDOW_SEC = 60.0
_REQUEST_LIMIT = 120
_request_times: Deque[float] = deque()

# Queue shared între state_bus listener și WS/SSE broadcaster
_bar_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
# Ultimele 500 bare pentru clienți noi care se conectează
_bar_history: Deque[Dict] = deque(maxlen=500)


def _rate_limit_check() -> None:
    now = time.time()
    while _request_times and now - _request_times[0] > _REQUEST_WINDOW_SEC:
        _request_times.popleft()
    if len(_request_times) >= _REQUEST_LIMIT:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    _request_times.append(now)


async def _state_bus_listener() -> None:
    """
    Task background: ascultă state_bus pentru topic 'bar' și
    distribuie payload în _bar_queue + _bar_history + gauge Prometheus.
    """
    logger.info("Dashboard: state_bus listener started")
    while True:
        try:
            # Încearcă să citească din bus dacă are subscribe/listen
            payload = None
            try:
                payload = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, bus.get_latest, "bar"),
                    timeout=1.0,
                )
            except (asyncio.TimeoutError, Exception):
                pass

            if payload and isinstance(payload, dict):
                # Actualizează Prometheus
                try:
                    _zscore_pair.set(float(payload.get("zscore_abs", 0.0)))
                    spread_zscore.set(float(payload.get("zscore", 0.0)))
                    pnl_usdt.set(float(payload.get("pnl", 0.0)))
                    _circuit_open.set(1.0 if payload.get("circuit_open") else 0.0)
                    _warmup_bars_done.set(float(payload.get("bar_count", 0)))
                    vol = payload.get("vol_regime", "")
                    _vol_regime_gauge.set(1.0 if "HIGH" in str(vol).upper() else 0.0)
                except Exception:
                    pass

                # Broadcast
                enriched = {**payload, "_server_ts": int(time.time() * 1000)}
                _bar_history.append(enriched)
                try:
                    _bar_queue.put_nowait(enriched)
                except asyncio.QueueFull:
                    # Scoate cel mai vechi dacă coada e plină
                    try:
                        _bar_queue.get_nowait()
                        _bar_queue.put_nowait(enriched)
                    except Exception:
                        pass

            await asyncio.sleep(0.2)  # 200ms polling pe state_bus
        except asyncio.CancelledError:
            logger.info("Dashboard: state_bus listener stopped")
            break
        except Exception as exc:
            logger.warning(f"Dashboard: state_bus listener error: {exc}")
            await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    listener_task = asyncio.create_task(_state_bus_listener())
    logger.info("QuantLuna Dashboard v2.0 starting — state_bus listener active")
    yield
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass
    logger.info("QuantLuna Dashboard shutting down")


app = FastAPI(
    title="QuantLuna Dashboard",
    description="Adaptive Kalman Filter Pairs Trading — Monitoring & Backtest API",
    version="2.0.0",
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
    health_status = "ok" if status in ("RUNNING", "IDLE") else (
        "error" if status in ("HALT", "HARD_STOP") else "degraded"
    )
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
    return {
        "active_positions": len(positions),
        "pnl_usdt": float(state.get("pnl_usdt", 0.0)),
        "drawdown_pct": float(state.get("drawdown", 0.0)),
        "websocket_clients": len(_ws_manager.active),
        "bar_history_size": len(_bar_history),
    }


@app.get("/api/warmup")
async def api_warmup() -> Dict[str, Any]:
    """
    Warm-up progress endpoint.
    Publishes: bars_done, bars_required, pct, coint_pvalue, half_life_h, regime, ready.
    """
    warmup = None
    try:
        warmup = bus.get_latest("warmup_status")
    except Exception:
        pass

    if warmup and isinstance(warmup, dict):
        return warmup

    # Fallback: reconstruiește din state snapshot
    state = bus.snapshot_dict()
    bars_done = int(state.get("warmup_bars_done", 0))
    bars_required = int(state.get("warmup_bars_required", 100))
    pct = round(min(1.0, bars_done / max(bars_required, 1)), 4)
    return {
        "bars_done": bars_done,
        "bars_required": bars_required,
        "pct": pct,
        "coint_pvalue": float(state.get("coint_pvalue", 1.0)),
        "half_life_h": float(state.get("half_life_h", 24.0)),
        "regime": str(state.get("vol_regime", "UNKNOWN")),
        "ready": pct >= 1.0,
        "ts": int(time.time() * 1000),
    }


@app.get("/api/bars/history")
async def api_bars_history(limit: int = Query(default=500, ge=1, le=500)) -> Dict[str, Any]:
    """Ultimele N bare din buffer — pentru clienți noi care se conectează."""
    bars = list(_bar_history)[-limit:]
    return {"count": len(bars), "bars": bars}


@app.get("/api/stream")
async def api_stream(request: Request) -> StreamingResponse:
    """
    SSE endpoint — alternativă la WebSocket pentru browsere care nu suportă WS.
    Folosește: EventSource('/api/stream') în JS.
    """
    async def event_generator():
        # Trimite istoricul recent la connect
        recent = list(_bar_history)[-50:]
        for bar in recent:
            yield f"data: {json.dumps(bar)}\n\n"

        # Stream live
        last_bar_count = -1
        while True:
            try:
                if await request.is_disconnected():
                    break
                # Așteaptă bar nou cu timeout pentru heartbeat
                try:
                    bar = await asyncio.wait_for(_bar_queue.get(), timeout=5.0)
                    last_bar_count = bar.get("bar_count", -1)
                    yield f"data: {json.dumps(bar)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat
                    yield f"data: {json.dumps({'type': 'heartbeat', 'ts': int(time.time() * 1000)})}\n\n"
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"SSE stream error: {exc}")
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/metrics")
async def prometheus_metrics() -> PlainTextResponse:
    """Prometheus exposition format — scrape target pentru Grafana."""
    return PlainTextResponse(registry.render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/api/optimize/results")
async def api_optimize_results(
    storage: Optional[str] = Query(default=None),
    study_name: str = Query(default="quantluna_opt"),
    top_n: int = Query(default=50, ge=1, le=500),
) -> Dict[str, Any]:
    """Optuna trial history."""
    if not storage:
        for default in ["sqlite:///optuna.db", "sqlite:///data/optuna.db"]:
            db_path = default.replace("sqlite:///", "")
            if os.path.exists(db_path):
                storage = default
                break

    if not storage:
        return {
            "study_name": study_name, "storage": None, "n_trials": 0,
            "best_value": None, "best_params": {}, "trials": [],
            "message": "No Optuna storage found.",
        }

    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        try:
            study = optuna.load_study(study_name=study_name, storage=storage)
        except Exception as exc:
            return {"study_name": study_name, "error": str(exc), "n_trials": 0,
                    "best_value": None, "best_params": {}, "trials": []}

        completed = sorted(
            [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE],
            key=lambda t: t.value or 0, reverse=True,
        )
        trials_data = []
        for t in completed[:top_n]:
            duration = (
                (t.datetime_complete - t.datetime_start).total_seconds()
                if t.datetime_complete and t.datetime_start else None
            )
            trials_data.append({
                "number": t.number,
                "value": round(t.value, 4) if t.value is not None else None,
                "params": {k: round(v, 6) if isinstance(v, float) else v for k, v in t.params.items()},
                "duration_s": round(duration, 2) if duration else None,
                "datetime_start": t.datetime_start.isoformat() if t.datetime_start else None,
            })

        best_value, best_params, param_importances = None, {}, {}
        try:
            best_value = round(study.best_value, 4)
            best_params = study.best_params
        except Exception:
            pass
        if len(completed) >= 10:
            try:
                importances = optuna.importance.get_param_importances(study)
                param_importances = {k: round(v, 4) for k, v in importances.items()}
            except Exception:
                pass

        return {
            "study_name": study_name, "storage": storage,
            "n_trials": len(study.trials), "n_complete": len(completed),
            "n_pruned": sum(1 for t in study.trials if t.state.name == "PRUNED"),
            "best_value": best_value, "best_params": best_params,
            "trials": trials_data, "param_importances": param_importances,
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="Optuna not installed.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# WebSocket Manager — push real-time
# =============================================================================

class _WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)
        websocket_clients.set(len(self.active))
        logger.info(f"WS client connected (total: {len(self.active)})")
        # Trimite istoricul recent la conectare
        try:
            recent = list(_bar_history)[-100:]
            if recent:
                await ws.send_json({"type": "history", "bars": recent})
        except Exception:
            pass

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)
        websocket_clients.set(len(self.active))
        logger.info(f"WS client disconnected (total: {len(self.active)})")

    async def broadcast(self, data: Dict) -> None:
        dead = []
        async with self._lock:
            targets = list(self.active)
        for ws in targets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


_ws_manager = _WSManager()


async def _ws_broadcast_loop() -> None:
    """Consuma _bar_queue și trimite fiecărui WS client conectat."""
    while True:
        try:
            bar = await _bar_queue.get()
            if _ws_manager.active:
                await _ws_manager.broadcast(bar)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"WS broadcast error: {exc}")


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    """
    WebSocket real-time feed.
    - La conectare: primește ultimele 100 bare (type='history')
    - Live: fiecare bar din runner în ~200ms latență
    - Fallback: dacă runner nu publică, snapshot din bus la 1s
    """
    await _ws_manager.connect(websocket)
    # Pornește broadcast loop dacă nu rulează deja
    broadcast_task = asyncio.create_task(_ws_broadcast_loop())
    try:
        while True:
            # Fallback polling dacă queue e goală (runner offline)
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                # Trimite snapshot fallback dacă queue e goală
                if _bar_queue.empty():
                    snapshot = bus.snapshot_dict()
                    snapshot["_type"] = "snapshot_fallback"
                    snapshot["_server_ts"] = int(time.time() * 1000)
                    try:
                        await websocket.send_json(snapshot)
                    except Exception:
                        break
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        broadcast_task.cancel()
        await _ws_manager.disconnect(websocket)

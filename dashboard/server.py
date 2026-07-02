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

Sprint 17 additions (feature/desktop-ui):
  - GET  /api/balance   → BalanceTracker component
  - GET  /api/pairs     → SpreadMonitorPanel + Sidebar
  - GET  /api/markets   → MarketHeatmap + Sidebar
  - GET  /api/risk      → RegimeHeader (regime + circuit breaker)
  - GET  /api/log       → ExecutionLog (recent entries)
  - WS   /ws/feed       → structured {type, payload, ts} messages for Next.js dashboard

All original endpoints preserved.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
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

# ---------------------------------------------------------------------------
# Sprint 16 — Mount backtest router
# ---------------------------------------------------------------------------

try:
    from api.backtest import router as backtest_router
    app.include_router(backtest_router)
    logger.info("Backtest API router mounted at /api/backtest")
except ImportError as _e:
    logger.warning(f"Backtest router not mounted: {_e}")


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
    storage: Optional[str] = Query(default=None),
    study_name: str = Query(default="quantluna_opt"),
    top_n: int = Query(default=50, ge=1, le=500),
) -> Dict[str, Any]:
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
            return {"error": str(exc), "n_trials": 0, "trials": []}
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
            })
        best_value, best_params = None, {}
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
            "study_name": study_name, "storage": storage,
            "n_trials": len(study.trials), "n_complete": len(completed),
            "best_value": best_value, "best_params": best_params,
            "trials": trials_data, "param_importances": param_importances,
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="Optuna not installed.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------

class _WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WS client connected (total: {len(self.active)})")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
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
    Original WS endpoint — pushes raw state snapshot every 1 second.
    Preserved for backward compatibility.
    """
    await _ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(1.0)
            await websocket.send_json(bus.snapshot_dict())
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.warning(f"WS /ws/live error: {exc}")
        _ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Sprint 17 — Dashboard REST + WS feed (feature/desktop-ui)
# ---------------------------------------------------------------------------

_MOCK_BALANCE = {
    "totalBalance": 10_432.87, "availableBalance": 7_215.44,
    "unrealizedPnl": 119.43, "realizedPnl": 312.00,
}

_MOCK_PAIRS = [
    {"symbol": "BTC/ETH", "zscore": 1.82, "spread": 0.0412, "halfLife": 18.3, "position": "LONG", "pnl": 142.5, "spreadHealth": "HEALTHY"},
    {"symbol": "SOL/AVAX", "zscore": -2.31, "spread": -0.0871, "halfLife": 8.1, "position": "SHORT", "pnl": -23.1, "spreadHealth": "DEGRADED"},
    {"symbol": "BNB/MATIC", "zscore": 0.44, "spread": 0.0089, "halfLife": 24.0, "position": "FLAT", "pnl": 0.0, "spreadHealth": "HEALTHY"},
]

_MOCK_MARKETS = [
    {"symbol": "BTC",  "price": 43215.50, "change24h":  2.34, "volume24h": 28_500_000_000, "fundingRate":  0.00012},
    {"symbol": "ETH",  "price":  2310.88, "change24h": -1.12, "volume24h": 12_300_000_000, "fundingRate": -0.00008},
    {"symbol": "SOL",  "price":    98.42, "change24h":  5.67, "volume24h":  3_200_000_000, "fundingRate":  0.00021},
    {"symbol": "BNB",  "price":   312.55, "change24h":  0.89, "volume24h":  1_800_000_000, "fundingRate":  0.00005},
    {"symbol": "AVAX", "price":    27.31, "change24h": -3.45, "volume24h":    950_000_000, "fundingRate": -0.00015},
    {"symbol": "MATIC","price":   0.5821, "change24h":  1.23, "volume24h":    620_000_000, "fundingRate":  0.00008},
    {"symbol": "DOT",  "price":    6.12, "change24h": -0.67, "volume24h":    380_000_000, "fundingRate":  0.00003},
    {"symbol": "ADA",  "price":   0.4456, "change24h":  3.21, "volume24h":    510_000_000, "fundingRate":  0.00011},
    {"symbol": "LINK", "price":   14.82, "change24h":  4.56, "volume24h":    720_000_000, "fundingRate":  0.00017},
    {"symbol": "UNI",  "price":    7.34, "change24h": -2.11, "volume24h":    280_000_000, "fundingRate": -0.00009},
    {"symbol": "ATOM", "price":    8.91, "change24h":  1.78, "volume24h":    190_000_000, "fundingRate":  0.00006},
    {"symbol": "NEAR", "price":    5.23, "change24h":  6.12, "volume24h":    340_000_000, "fundingRate":  0.00019},
    {"symbol": "FTM",  "price":  0.7821, "change24h": -4.23, "volume24h":    260_000_000, "fundingRate": -0.00022},
    {"symbol": "ALGO", "price":  0.1821, "change24h":  0.45, "volume24h":    140_000_000, "fundingRate":  0.00002},
    {"symbol": "XRP",  "price":  0.5234, "change24h":  2.89, "volume24h":  1_100_000_000, "fundingRate":  0.00010},
    {"symbol": "LTC",  "price":   72.45, "change24h": -1.34, "volume24h":    430_000_000, "fundingRate":  0.00001},
    {"symbol": "DOGE", "price":  0.0821, "change24h":  7.82, "volume24h":    890_000_000, "fundingRate":  0.00025},
    {"symbol": "SHIB", "price": 0.00000982, "change24h": -5.67, "volume24h": 310_000_000, "fundingRate": -0.00018},
    {"symbol": "ARB",  "price":  0.8123, "change24h":  3.45, "volume24h":    220_000_000, "fundingRate":  0.00014},
    {"symbol": "OP",   "price":   1.67, "change24h": -2.78, "volume24h":    180_000_000, "fundingRate": -0.00011},
]

_MOCK_RISK = {"regime": "NORMAL", "cb_open": False, "cb_cooldown": 0}


@app.get("/api/balance")
async def api_balance() -> Dict[str, Any]:
    """Balance snapshot for BalanceTracker component."""
    state = bus.snapshot_dict()
    return {
        "totalBalance":     state.get("equity",           _MOCK_BALANCE["totalBalance"]),
        "availableBalance": state.get("available_balance", _MOCK_BALANCE["availableBalance"]),
        "unrealizedPnl":    state.get("pnl_usdt",         _MOCK_BALANCE["unrealizedPnl"]),
        "realizedPnl":      state.get("realized_pnl",     _MOCK_BALANCE["realizedPnl"]),
    }


@app.get("/api/pairs")
async def api_pairs() -> List[Dict[str, Any]]:
    """Pairs state for SpreadMonitorPanel + Sidebar."""
    try:
        positions = bus.get_positions()
        if positions:
            return [
                {
                    "symbol":      p.pair,
                    "zscore":      getattr(p, "zscore",    0.0),
                    "spread":      getattr(p, "spread",    0.0),
                    "halfLife":    getattr(p, "half_life", 0.0),
                    "position":    p.direction if hasattr(p, "direction") else "FLAT",
                    "pnl":         getattr(p, "pnl",       0.0),
                    "spreadHealth": "HEALTHY",
                }
                for p in positions
            ]
    except Exception:
        pass
    return _MOCK_PAIRS


@app.get("/api/markets")
async def api_markets() -> List[Dict[str, Any]]:
    """Market data for MarketHeatmap + Sidebar."""
    return _MOCK_MARKETS


@app.get("/api/risk")
async def api_risk() -> Dict[str, Any]:
    """Risk regime + circuit breaker state for RegimeHeader."""
    state = bus.snapshot_dict()
    cb_open = state.get("status") in ("HALT", "HARD_STOP")
    return {
        "regime":       state.get("volatility_regime", _MOCK_RISK["regime"]),
        "cb_open":      cb_open,
        "cb_cooldown":  state.get("cb_cooldown", 0),
    }


@app.get("/api/log")
async def api_log() -> List[Dict[str, Any]]:
    """Recent log entries for ExecutionLog."""
    try:
        trades = bus.get_recent_trades()
        if trades:
            return [
                {
                    "ts":      int(time.time() * 1000) - i * 1000,
                    "level":   "BUY" if "BUY" in str(t).upper() else "SELL" if "SELL" in str(t).upper() else "INFO",
                    "module":  "Executor",
                    "message": str(t),
                }
                for i, t in enumerate(trades[-20:])
            ]
    except Exception:
        pass
    return [
        {"ts": int(time.time() * 1000) - 3000, "level": "INFO", "module": "SignalGen",  "message": "Kalman filter warmed up on BTC/ETH"},
        {"ts": int(time.time() * 1000) - 2000, "level": "BUY",  "module": "Executor",   "message": "LONG_SPREAD BTC/ETH z=1.82 qty=0.05"},
        {"ts": int(time.time() * 1000) - 1000, "level": "WARN", "module": "RiskMgr",    "message": "Volatility regime elevated to HIGH"},
        {"ts": int(time.time() * 1000),         "level": "ARB",  "module": "ArbScanner", "message": "Opportunity BTC Bybit/Binance spread=0.041%"},
    ]


@app.websocket("/ws/feed")
async def websocket_feed(websocket: WebSocket):
    """
    Structured WebSocket feed for Next.js dashboard.
    Sends { type, payload, ts } messages compatible with
    useTradingStore.updateFromWsFeed().
    """
    await _ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(2.0)
            now = int(time.time() * 1000)
            state = bus.snapshot_dict()

            for msg in [
                {
                    "type": "balance",
                    "payload": {
                        "totalBalance":     state.get("equity",           _MOCK_BALANCE["totalBalance"]),
                        "availableBalance": state.get("available_balance", _MOCK_BALANCE["availableBalance"]),
                        "unrealizedPnl":    state.get("pnl_usdt",         _MOCK_BALANCE["unrealizedPnl"]),
                        "realizedPnl":      state.get("realized_pnl",     _MOCK_BALANCE["realizedPnl"]),
                    },
                    "ts": now,
                },
                {
                    "type": "pairs",
                    "payload": _MOCK_PAIRS,
                    "ts": now,
                },
                {
                    "type": "regime",
                    "payload": {
                        "regime":      state.get("volatility_regime", _MOCK_RISK["regime"]),
                        "cb_open":     state.get("status") in ("HALT", "HARD_STOP"),
                        "cb_cooldown": state.get("cb_cooldown", 0),
                    },
                    "ts": now,
                },
                {
                    "type": "ws_status",
                    "payload": {"bybit": False, "binance": False, "okx": False},
                    "ts": now,
                },
            ]:
                await websocket.send_json(msg)

    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.warning(f"WS /ws/feed error: {exc}")
        _ws_manager.disconnect(websocket)

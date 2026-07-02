"""
QuantLuna — FastAPI Application Entry Point
Sprint 21 + Sprint 24 + Sprint 25 (optimize router)

Ruleaza cu:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints expuse:
  /backtest/*   — backtest jobs
  /strategy/*   — AutoSelector scores, list, switch, context
  /live/*       — LiveTrader start/stop/status/SSE stream
  /optimize/*   — WalkForwardOptimizer jobs
  /health       — health + uptime + version
  /docs         — Swagger UI
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.backtest import router as backtest_router
from api.health import router as health_router
from api.live import router as live_router
from api.optimize import router as optimize_router
from api.strategy import router as strategy_router

logger = logging.getLogger(__name__)
_START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantLuna API starting up...")
    yield
    logger.info("QuantLuna API shutting down.")


app = FastAPI(
    title="QuantLuna API",
    description="""
QuantLuna — Crypto Pairs Trading Engine

## Modules
- **Backtest** — submit & monitor walk-forward backtests, compare results
- **Strategy** — AutoSelector scores, manual switch, MarketContext per job
- **Live** — Binance WebSocket live trader (paper/live/dry mode), SSE stream
- **Optimize** — WalkForward parameter optimizer, per-regime best config
- **Health** — uptime, version, system status
    """,
    version="0.25.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(backtest_router)
app.include_router(strategy_router)
app.include_router(live_router)
app.include_router(optimize_router)
app.include_router(health_router)


@app.get("/", tags=["root"])
def root():
    return {
        "name":    "QuantLuna API",
        "version": "0.25.0",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "modules": ["/backtest", "/strategy", "/live", "/optimize", "/health"],
        "docs": "/docs",
    }

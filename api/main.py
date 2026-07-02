"""
QuantLuna — FastAPI Application Entry Point
Sprint 21

Ruleaza cu:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Sau cu Docker:
    docker compose up

Endpoints expuse:
  /backtest/*   — backtest jobs (submit, status, results, compare)
  /strategy/*   — AutoSelector scores, list, switch, context
  /health       — health + uptime + version
  /docs         — Swagger UI (auto-generat)
  /redoc        — ReDoc UI
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.backtest import router as backtest_router
from api.health import router as health_router
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
- **Health** — uptime, version, system status
    """,
    version="0.21.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow dashboard (localhost dev + production origin)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(backtest_router)
app.include_router(strategy_router)
app.include_router(health_router)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------
@app.get("/", tags=["root"], summary="API root")
def root():
    """
    Returns API version + uptime + available modules.

    curl http://localhost:8000/
    """
    return {
        "name":    "QuantLuna API",
        "version": "0.21.0",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "modules": ["/backtest", "/strategy", "/health"],
        "docs":    "/docs",
    }

"""
QuantLuna — FastAPI Application Entry Point
Sprint 21-29  — versiune 0.29.0

Ruleaza cu:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints expuse:
  /backtest/*       — backtest jobs
  /strategy/*       — AutoSelector, MarketContext, regime detection
  /live/*           — LiveTrader WebSocket (Bybit/Binance), paper/dry/live mode
  /optimize/*       — WalkForward optimizer, per-regime best config
  /data/*           — OHLCV fetch (Bybit/Binance), Parquet cache unificat
  /risk/*           — Dashboard live: Sharpe rolling, DD, win rate, exposure, SSE
  /pairs/*          — Multi-Pair Manager: N perechi simultan, halt cascade, corr filter
  /sizing/*         — Position Sizer: Kelly + Fixed, leverage-aware Bybit
  /notifications/*  — AlertDispatcher status + test send
  /health           — uptime, version, system status
  /docs             — Swagger UI
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.backtest      import router as backtest_router
from api.data          import router as data_router
from api.health        import router as health_router
from api.live          import router as live_router
from api.notifications import router as notifications_router
from api.notifications import set_dispatcher
from api.optimize      import router as optimize_router
from api.pairs         import router as pairs_router
from api.risk          import router as risk_router
from api.sizing        import router as sizing_router
from api.strategy      import router as strategy_router
from notifications.alert_dispatcher import AlertDispatcher

logger = logging.getLogger(__name__)
_START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantLuna API v0.29.0 starting up...")
    # Porneste AlertDispatcher
    dispatcher = AlertDispatcher()
    await dispatcher.start()
    set_dispatcher(dispatcher)
    # Emite event de start
    from notifications.event_types import AlertEvent, EventType
    await dispatcher.emit(AlertEvent(
        event_type=EventType.SYSTEM_START,
        payload={"version": "0.29.0", "exchange": __import__("os").getenv("EXCHANGE", "bybit")},
    ))
    yield
    await dispatcher.stop()
    logger.info("QuantLuna API v0.29.0 shutdown complete.")


app = FastAPI(
    title="QuantLuna API",
    description="""
QuantLuna — Crypto Pairs Trading Engine (Bybit + Binance)

## Modules
- **Backtest** — walk-forward backtests
- **Strategy** — AutoSelector, MarketContext, regime detection
- **Live** — LiveTrader WebSocket Bybit/Binance, paper/dry/live mode
- **Optimize** — WalkForward optimizer, per-regime best config
- **Data** — OHLCV Bybit/Binance REST, cache Parquet unificat
- **Risk** — Dashboard: Sharpe, DD, win rate, exposure, SSE stream
- **Pairs** — Multi-Pair Manager: N perechi simultane, halt cascade, corr filter
- **Sizing** — Position Sizer: Kelly + Fixed, leverage-aware Bybit linear
- **Notifications** — Telegram + Discord alerts: trade, DD, Sharpe, halt
- **Health** — uptime, version
    """,
    version="0.29.0",
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
app.include_router(data_router)
app.include_router(risk_router)
app.include_router(pairs_router)
app.include_router(sizing_router)
app.include_router(notifications_router)
app.include_router(health_router)


@app.get("/", tags=["root"])
def root():
    return {
        "name":    "QuantLuna API",
        "version": "0.29.0",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "exchange":       __import__("os").getenv("EXCHANGE", "bybit"),
        "mode":           __import__("os").getenv("EXCHANGE_MODE", "paper"),
        "modules": [
            "/backtest", "/strategy", "/live", "/optimize",
            "/data", "/risk", "/pairs", "/sizing",
            "/notifications", "/health",
        ],
        "docs": "/docs",
    }

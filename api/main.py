"""
QuantLuna — FastAPI Application Entry Point
Sprint S44  — versiune 0.30.0

Ruleaza cu:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints expuse:
  /backtest/*       — backtest jobs
  /strategy/*       — AutoSelector, MarketContext, regime detection
  /live/*           — LiveTrader WebSocket (Bybit/Binance), paper/dry/live mode
  /optimize/*       — WalkForward optimizer legacy
  /data/*           — OHLCV fetch (Bybit/Binance), Parquet cache unificat
  /risk/*           — Dashboard live: Sharpe rolling, DD, win rate, exposure, SSE
  /pairs/*          — Multi-Pair Manager: N perechi simultan, halt cascade, corr filter
  /sizing/*         — Position Sizer: Kelly + Fixed, leverage-aware Bybit
  /notifications/*  — AlertDispatcher status + test send
  /health           — uptime, version, system status
  /api/services/*   — Services Control Panel: start/stop/restart + WebSocket live
  /api/optimizer/*  — Grid Search WFO: run/status/results/history/heatmap
  /api/watchdog/*   — MonitoringWatchdog: thresholds, alerts, silence
  /docs             — Swagger UI
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ─ Routers existenti ─────────────────────────────────────────────────────────
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

# ─ Routers noi S41–S44 ──────────────────────────────────────────────────────
from api.services  import services_router
from api.optimizer import optimizer_router, set_optimizer_state
from api.watchdog  import watchdog_router, set_watchdog_state

# ─ Orchestrator ─────────────────────────────────────────────────────────────────
from notifications.alert_dispatcher import AlertDispatcher
from core.workflow_orchestrator import WorkflowOrchestrator

logger = logging.getLogger(__name__)
_START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantLuna API v0.30.0 starting up...")

    # 1. AlertDispatcher
    dispatcher = AlertDispatcher()
    await dispatcher.start()
    set_dispatcher(dispatcher)

    # 2. WorkflowOrchestrator — construieste toate componentele
    orchestrator = WorkflowOrchestrator.from_env(dispatcher=dispatcher)
    await orchestrator.build_context()

    # 3. Injecteaza state in routerele API (fara import circular)
    set_optimizer_state({
        "running":          False,
        "last_run":         None,
        "last_results":     {},
        "pairs":            orchestrator.pairs,
        "auto_reoptimizer": orchestrator.reoptimizer,
    })
    set_watchdog_state({
        "watchdog":  orchestrator.watchdog,
        "dispatcher": dispatcher,
    })

    # 4. Emite SYSTEM_START
    from notifications.event_types import AlertEvent, EventType
    await dispatcher.emit(AlertEvent(
        event_type=EventType.SYSTEM_START,
        payload={"version": "0.30.0", "exchange": os.getenv("EXCHANGE", "bybit")},
    ))

    # 5. Porneste runner + reoptimizer + watchdog in background
    import asyncio
    runner_task = asyncio.create_task(
        orchestrator.start_runner(),
        name="workflow_runner",
    )
    app.state.runner_task = runner_task

    yield

    # Shutdown ordonat
    runner_task.cancel()
    try:
        await runner_task
    except asyncio.CancelledError:
        pass
    await orchestrator.stop_runner()
    await dispatcher.stop()
    logger.info("QuantLuna API v0.30.0 shutdown complete.")


app = FastAPI(
    title="QuantLuna API",
    description="""
QuantLuna — Crypto Pairs Trading Engine (Bybit + Binance)

## Modules
- **Backtest** — walk-forward backtests
- **Strategy** — AutoSelector, MarketContext, regime detection
- **Live** — LiveTrader WebSocket Bybit/Binance, paper/dry/live mode
- **Optimize** — WalkForward optimizer legacy
- **Data** — OHLCV Bybit/Binance REST, cache Parquet unificat
- **Risk** — Dashboard: Sharpe, DD, win rate, exposure, SSE stream
- **Pairs** — Multi-Pair Manager: N perechi simultane, halt cascade, corr filter
- **Sizing** — Position Sizer: Kelly + Fixed, leverage-aware Bybit linear
- **Notifications** — Telegram + Discord: trade, DD, Sharpe, halt
- **Services** — Control Panel: start/stop/restart servicii + WebSocket live
- **Optimizer** — Grid Search WFO: run/status/results/history/heatmap
- **Watchdog** — MonitoringWatchdog: thresholds per pereche, alerte Telegram
- **Health** — uptime, version
    """,
    version="0.30.0",
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

# ─ Routers existenti ─────────────────────────────────────────────────────────
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

# ─ Routers noi S41–S44 (prefix /api/*) ─────────────────────────────────────
app.include_router(services_router,  prefix="/api/services",  tags=["services"])
app.include_router(optimizer_router, prefix="/api/optimizer", tags=["optimizer"])
app.include_router(watchdog_router,  prefix="/api/watchdog",  tags=["watchdog"])


@app.get("/", tags=["root"])
def root():
    return {
        "name":    "QuantLuna API",
        "version": "0.30.0",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "exchange":       os.getenv("EXCHANGE", "bybit"),
        "mode":           os.getenv("EXCHANGE_MODE", "paper"),
        "modules": [
            "/backtest", "/strategy", "/live", "/optimize",
            "/data", "/risk", "/pairs", "/sizing",
            "/notifications", "/health",
            "/api/services", "/api/optimizer", "/api/watchdog",
        ],
        "docs": "/docs",
    }

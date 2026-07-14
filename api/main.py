"""
QuantLuna — FastAPI Application Entry Point
Sprint S46 (2026-07-12)  — versiune 0.32.0

Ruleaza cu:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints expuse:
  /backtest/*          — backtest jobs
  /strategy/*          — AutoSelector, MarketContext, regime detection
  /live/*              — LiveTrader WebSocket (Bybit/Binance), paper/dry/live mode
  /optimize/*          — WalkForward optimizer legacy
  /data/*              — OHLCV fetch (Bybit/Binance), Parquet cache unificat
  /risk/*              — Dashboard live: Sharpe rolling, DD, win rate, exposure, SSE
  /pairs/*             — Multi-Pair Manager: N perechi simultan, halt cascade, corr filter
  /sizing/*            — Position Sizer: Kelly + Fixed, leverage-aware Bybit
  /sizing/live_status  — SizingEngine v2.5 live status
  /sizing/decision_status — DecisionEngine v2.5 (alias compat)
  /sizing/reduce/*     — Watchdog reduce hooks (S33)
  /notifications/*     — AlertDispatcher status + test send
  /health              — uptime, version, system status
  /api/services/*      — Services Control Panel: start/stop/restart + WebSocket live
  /api/optimizer/*     — Grid Search WFO: run/status/results/history/heatmap
  /api/watchdog/*      — MonitoringWatchdog: thresholds, alerts, silence
  /api/decision/status — DecisionEngine v2.5 (sursa unica pentru dashboard)
  /metrics             — Prometheus scrape endpoint (S35)
  /docs                — Swagger UI
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env before any other imports that read from os.environ
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ─ Routers existenti ────────────────────────────────────────────────────────
from api.backtest      import router as backtest_router
from api.data          import router as data_router
from api.health        import router as health_router
from api.live          import router as live_router
from api.notifications import router as notifications_router
from api.notifications import set_dispatcher
from api.optimize      import router as optimize_router
from api.pairs         import router as pairs_router
from api.pnl           import router as pnl_router
from api.risk          import router as risk_router
from api.sizing        import router as sizing_router, set_sizing_state
from api.strategy      import router as strategy_router

# ─ Routers noi S41–S44 ──────────────────────────────────────────────────────
from api.services  import services_router
from api.optimizer import optimizer_router, set_optimizer_state
from api.watchdog  import watchdog_router, set_watchdog_state

# ─ Router nou S46 ───────────────────────────────────────────────────────────
from api.decision  import decision_router, set_decision_state

# ─ Router nou S35 — Prometheus /metrics ─────────────────────────────────────
from api.metrics   import metrics_router, set_metrics_state

# ─ Router WebSocket live feed ────────────────────────────────────────────────
from api.ws_routes import ws_router

# ─ Router S47 — AI/ML signal layer ─────────────────────────────────────────
from api.ml import ml_router, set_ml_state

# ─ Router S48 — Diagnostics ──────────────────────────────────────────────
from api.diagnostics import diagnostics_router, set_traffic_state

# ─ Orchestrator ─────────────────────────────────────────────────────────────
from notifications.alert_dispatcher import AlertDispatcher
from core.workflow_orchestrator import WorkflowOrchestrator

logger = logging.getLogger(__name__)
_START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantLuna API v0.32.0 starting up...")

    # 0. Read real Bybit balance for dashboard
    real_equity = 10000.0
    try:
        from execution.bybit_order_router import BybitOrderRouter
        router = BybitOrderRouter()
        bal = await router.get_wallet_balance()
        if bal > 0:
            real_equity = bal
            logger.info("Real Bybit balance detected: ${:.2f}", real_equity)
    except Exception as e:
        logger.warning("Could not read Bybit balance: {}", e)

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
        "watchdog":   orchestrator.watchdog,
        "dispatcher": dispatcher,
    })

    # 4. S34: construieste SizingEngine si injecteaza via set_sizing_state()
    from risk.bybit_position_sizer import BybitPositionSizer
    from risk.sizing_engine import SizingEngine

    ctx = orchestrator.context
    raw_engine = getattr(ctx, "sizing_engine", None)

    if isinstance(raw_engine, SizingEngine):
        sizing_engine = raw_engine
        logger.info("[lifespan] SizingEngine preluat din orchestrator context")
    elif raw_engine is not None:
        try:
            sizing_engine = SizingEngine(sizer=raw_engine)
            logger.info(
                "[lifespan] SizingEngine wrapat in jurul %s din orchestrator context",
                type(raw_engine).__name__,
            )
        except Exception as exc:
            logger.warning(
                "[lifespan] Nu am putut wrapa raw_engine (%s) in SizingEngine: %s — "
                "construiesc standalone din env",
                type(raw_engine).__name__, exc,
            )
            sizing_engine = SizingEngine(sizer=BybitPositionSizer(
                capital_usdt=float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
                max_leverage=float(os.getenv("MAX_LEVERAGE", "3.0")),
                kelly_fraction=os.getenv("KELLY_FRACTION", "half"),
                max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.25")),
            ))
    else:
        sizing_engine = SizingEngine(sizer=BybitPositionSizer(
            capital_usdt=float(os.getenv("INITIAL_CAPITAL_USD", "10000")),
            max_leverage=float(os.getenv("MAX_LEVERAGE", "3.0")),
            kelly_fraction=os.getenv("KELLY_FRACTION", "half"),
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.25")),
        ))
        logger.info("[lifespan] SizingEngine construit standalone din env vars")

    decision_engine = getattr(ctx, "decision_engine", None)
    watchdog        = getattr(orchestrator, "watchdog", None)

    set_sizing_state({
        "sizing_engine":   sizing_engine,
        "decision_engine": decision_engine,
    })
    set_decision_state({
        "decision_engine": decision_engine,
    })

    # 5. S35: injecteaza state in metrics router (Prometheus /metrics)
    set_metrics_state({
        "sizing_engine":   sizing_engine,
        "watchdog":        watchdog,
        "decision_engine": decision_engine,
    })
    logger.info(
        "[lifespan] metrics_router wired — sizing_engine=%s watchdog=%s decision=%s",
        type(sizing_engine).__name__,
        type(watchdog).__name__ if watchdog else None,
        type(decision_engine).__name__ if decision_engine else None,
    )

    # 5b. Update RiskDashboardEngine with real Bybit balance
    try:
        from api.risk import get_risk_engine
        risk_eng = get_risk_engine()
        risk_eng.update_equity(real_equity)
        logger.info("[lifespan] RiskDashboardEngine equity set to ${:.2f}", real_equity)
    except Exception as exc:
        logger.warning("[lifespan] Could not update RiskDashboardEngine: {}", exc)

    # 5c. S47: injecteaza ML state in ml_router
    try:
        from strategy.ml.config import MLConfig
        from strategy.ml.features import FeatureStore
        from strategy.ml.models import ModelRegistry
        from strategy.ml.signal_fusion import SignalFusion

        ml_cfg = MLConfig.from_env()
        ml_fs = FeatureStore(maxlen=ml_cfg.feature_lookback)
        ml_reg = ModelRegistry(ml_cfg)
        # If ML is enabled, register default models
        if ml_cfg.enabled:
            from strategy.ml.models import NumpyLinearRegression, NumpyLogisticRegression
            ml_reg.register_direction(
                "lr_default", NumpyLogisticRegression(
                    n_features=30, lr=ml_cfg.lr_learning_rate,
                    l2_reg=ml_cfg.lr_l2_reg,
                ),
            )
            ml_reg.register_confidence(
                "lin_default", NumpyLinearRegression(
                    n_features=30, lr=ml_cfg.linear_learning_rate,
                    l2_reg=ml_cfg.linear_l2_reg,
                ),
            )
        ml_fusion = SignalFusion(ml_cfg)
        set_ml_state({
            "ml_engine": None,          # will be set when runner starts
            "feature_store": ml_fs,
            "registry": ml_reg,
            "fusion": ml_fusion,
        })
        logger.info(
            "[lifespan] ML router wired — enabled=%s features=%d",
            ml_cfg.enabled, ml_fs.N_FEATURES,
        )
    except Exception as exc:
        logger.warning("[lifespan] ML state injection failed: {}", exc)
        set_ml_state({
            "ml_engine": None, "feature_store": None,
            "registry": None, "fusion": None,
        })

    # 6. Emite SYSTEM_START
    from notifications.event_types import AlertEvent, EventType
    await dispatcher.emit(AlertEvent(
        event_type=EventType.SYSTEM_START,
        payload={"version": "0.32.0", "exchange": os.getenv("EXCHANGE", "bybit")},
    ))

    # 7. Porneste runner + reoptimizer + watchdog in background
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
    logger.info("QuantLuna API v0.32.0 shutdown complete.")


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
- **Decision** — DecisionEngine v2.5: status live pentru dashboard unificat
- **Metrics** — Prometheus scrape endpoint GET /metrics (S35)
- **Health** — uptime, version
    """,
    version="0.32.0",
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

# ─ Routers existenti ────────────────────────────────────────────────────────
app.include_router(backtest_router)
app.include_router(strategy_router)
app.include_router(live_router)
app.include_router(optimize_router)
app.include_router(data_router)
app.include_router(risk_router)
app.include_router(pairs_router)
app.include_router(pnl_router)
app.include_router(sizing_router)
app.include_router(notifications_router)
app.include_router(health_router)

# ─ Routers noi S41–S44 (prefix /api/*) ─────────────────────────────────────
app.include_router(services_router,  prefix="/api/services",  tags=["services"])
app.include_router(optimizer_router, prefix="/api/optimizer", tags=["optimizer"])
app.include_router(watchdog_router,  prefix="/api/watchdog",  tags=["watchdog"])

# ─ Router nou S46 (prefix /api/decision) ────────────────────────────────────
app.include_router(decision_router,  prefix="/api/decision",  tags=["decision"])

# ─ Router nou S35 — Prometheus /metrics ─────────────────────────────────────
app.include_router(metrics_router)

# ─ Router WebSocket live feed ──────────────────────────────────────────────
app.include_router(ws_router)

# ─ Router S47 — AI/ML signal layer ───────────────────────────────────────
app.include_router(ml_router, prefix="/api/ml", tags=["ml"])

# ─ Router S48 P0 — Diagnostics ───────────────────────────────────────────
app.include_router(diagnostics_router)


@app.get("/", tags=["root"])
def root():
    return {
        "name":    "QuantLuna API",
        "version": "0.32.0",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "exchange":       os.getenv("EXCHANGE", "bybit"),
        "mode":           os.getenv("EXCHANGE_MODE", "paper"),
        "modules": [
            "/backtest", "/strategy", "/live", "/optimize",
            "/data", "/risk", "/pairs", "/sizing",
            "/notifications", "/health",
            "/api/services", "/api/optimizer", "/api/watchdog",
            "/api/decision",
            "/metrics",
        ],
        "docs": "/docs",
    }

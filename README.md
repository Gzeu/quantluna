# QuantLuna

> **Stat-arb trading system** — pairs trading cu Kalman filter, cointegration analysis, multi-exchange execution, MonitoringWatchdog şi AutoReoptimizer WFO.

[![CI](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml/badge.svg)](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Gzeu/quantluna/branch/main/graph/badge.svg)](https://codecov.io/gh/Gzeu/quantluna)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.32.0-green.svg)](CHANGELOG.md)

---

## Arhitectură

```
┌──────────────────────────────────────────────────────────────────────┐
│                        QuantLuna v0.32.0                            │
│                                                                      │
│  Data Layer            Strategy Layer         Execution Layer        │
│  ───────────           ───────────────        ───────────────        │
│  BybitFetcher    →     KalmanFilter      →    OrderManager           │
│  BinanceFetcher        Cointegration           ├─ BybitRouter         │
│  OKXFetcher            SpreadSignal            ├─ BinanceRouter       │
│  LiveDataBridge        MultiTimeframe          └─ OKXRouter           │
│  MarketDataCache       AutoStrategySelector                           │
│                        VolatilityRegime    →   CircuitBreaker         │
│                        RegimeDetector          PositionScanner        │
│                        SpreadMonitor           AdoptionEngine         │
│                        FundingRate             ProfitOptimizer        │
│                        CorrelationMatrix        BybitLiveRunner       │
│                                                                      │
│  Orchestrare                                                         │
│  ────────────                                                        │
│  WorkflowOrchestrator  (startup: 5 faze HealthCheck → Runner)        │
│  MultiMarketOrchestrator  (runtime: Runner + Watchdog + Reoptimizer) │
│    └─ asyncio.gather(runner.start(),                                 │
│                      watchdog.run_loop(),                            │
│                      reoptimizer.run_loop())                         │
│                                                                      │
│  Risk / Monitoring                                                   │
│  ─────────────────                                                    │
│  MonitoringWatchdog  →  AlertDispatcher  →  Telegram HALT/REDUCE     │
│  CircuitBreaker          PairThreshold        ALERT_ONLY             │
│  HealthCheck             MetricsProvider   (Sharpe/DD/z/hl/streak)   │
│  WsWatchdog             RiskDashboardEngine                          │
│  AutoRebalancer          DrawdownController                          │
│  KellyPositionSizer      MultiPairAllocator                          │
│  SizingEngine (S34)      DecisionEngine v2.5 (S46)                   │
│                                                                      │
│  Backtest / Optimizer                                                │
│  ────────────────────                                                │
│  AutoReoptimizer     (WFO saptamanal, aplica params automat)         │
│  ParamGridOptimizer  (GridSpace coarse/fine, OOS Sharpe + WFO score) │
│  WalkForwardEngine   KalmanScoringWeights  SearchSpace               │
│  MonteCarlo          coint_pvalue_series (rolling ADF)               │
│  Optuna TPE optimizer (16 ks_* params)                               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
git clone https://github.com/Gzeu/quantluna.git
cd quantluna
make install
cp .env.example .env   # editează cu cheile tale

# Paper trading (recomandat)
make paper

# Sau manual
python main.py --dry-run --pair BTCUSDT/ETHUSDT
```

---

## Structura proiectului

```
quantluna/
├── core/
│   ├── multi_market_orchestrator.py  # MultiMarketOrchestrator v2.2 (S32)
│   ├── monitoring_watchdog.py        # MonitoringWatchdog (S29)
│   ├── kalman_filter.py
│   ├── kalman_adapter.py
│   ├── spread.py
│   ├── spread_monitor.py
│   ├── cointegration.py
│   ├── regime_detector.py
│   ├── correlation_matrix.py
│   ├── funding_rate.py
│   ├── live_data_bridge.py
│   ├── metrics.py
│   ├── performance_analytics.py
│   ├── config_validator.py
│   └── state_bus.py
│
├── strategy/
│   ├── signal.py
│   ├── kalman_pairs_trading.py
│   ├── auto_selector.py
│   ├── optimizer.py
│   ├── multi_strategy_engine.py
│   ├── multi_timeframe.py
│   ├── regime_filter.py
│   ├── pair_selector.py
│   ├── entry_filter.py
│   ├── bb_mean_reversion.py
│   ├── zscore_momentum.py
│   ├── funding_arb.py
│   └── stat_arb.py
│
├── execution/
│   ├── workflow_orchestrator.py
│   ├── bybit_live_runner.py
│   ├── order_manager.py
│   ├── bybit_order_router.py
│   ├── position_scanner.py
│   ├── adoption_engine.py
│   ├── profit_optimizer.py
│   ├── health_check.py
│   ├── resume_manager.py
│   ├── checkpoint.py
│   ├── pnl_reconciler.py
│   ├── exchange_factory.py
│   ├── bybit_ws_feed.py
│   ├── bybit_private_ws.py
│   ├── ws_watchdog.py
│   ├── emergency_stop.py
│   └── rate_limiter.py
│
├── risk/
│   ├── sizing_engine.py              # SizingEngine (S34) — set_pair_factor()
│   ├── bybit_position_sizer.py       # Kelly+Fixed leverage-aware
│   ├── multi_pair_allocator.py       # set_alloc_factor() (S33)
│   ├── dashboard_engine.py           # RiskDashboardEngine (S27)
│   ├── circuit_breaker.py
│   ├── kelly.py
│   ├── kelly_sizer.py
│   ├── dynamic_stop.py
│   ├── portfolio_risk.py
│   ├── auto_rebalancer.py
│   ├── correlation_filter.py
│   ├── correlation_matrix.py
│   ├── drawdown_controller.py
│   ├── position_sizer.py
│   └── position_sizer_factory.py
│
├── api/
│   ├── main.py                       # FastAPI app v0.32.0 — 14 routere
│   ├── metrics.py                    # GET /metrics Prometheus (S35)
│   ├── decision.py                   # GET /api/decision/status (S46)
│   ├── watchdog.py                   # GET /api/watchdog/* (S41–S44)
│   ├── optimizer.py                  # GET /api/optimizer/* (S41–S44)
│   ├── services.py                   # GET /api/services/* (S41–S44)
│   ├── sizing.py                     # /sizing/* + reduce hooks (S33/S34)
│   ├── pairs.py                      # /pairs/* + halt_pair (S33)
│   ├── risk.py                       # /risk/* + SSE stream (S27)
│   ├── backtest.py
│   ├── data.py
│   ├── health.py
│   ├── live.py
│   ├── live_ws.py
│   ├── notifications.py
│   ├── optimize.py
│   ├── paper.py
│   ├── portfolio.py
│   ├── rebalancer.py
│   ├── reports.py
│   ├── schemas.py
│   ├── strategies.py
│   └── strategy.py
│
├── backtest/
│   ├── auto_reoptimizer.py
│   ├── param_grid_optimizer.py
│   ├── backtest_engine.py
│   ├── engine_adapter.py
│   ├── analytics.py
│   ├── monte_carlo.py
│   ├── walk_forward.py
│   └── report_builder.py
│
├── notifications/
│   ├── notifier_bus.py
│   ├── alert_dispatcher.py
│   ├── telegram.py
│   ├── slack_notifier.py
│   └── discord.py
│
├── data/
│   ├── fetcher.py
│   ├── historical_fetcher.py
│   └── store.py
│
├── tests/                            # 55+ fisiere de teste
├── main.py
├── config.py
├── Makefile
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
└── CHANGELOG.md
```

---

## Instalare

```bash
git clone https://github.com/Gzeu/quantluna.git
cd quantluna
pip install -r requirements.txt
```

### Dependențe principale

```
numpy >= 1.26
pandas >= 2.1
scipy >= 1.11
statsmodels >= 0.14
optuna >= 3.6
aiohttp >= 3.9
fastapi >= 0.111
loguru >= 0.7
pytest >= 8.0
pytest-asyncio >= 0.23
plotly >= 5.0
```

---

## Configurare

```bash
cp .env.example .env
```

```env
# Exchange API Keys
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_PASSPHRASE=...

# Perechi (multi-market)
PAIRS=BTCUSDT-ETHUSDT,SOLUSDT-AVAXUSDT
SYMBOL_Y=BTCUSDT
SYMBOL_X=ETHUSDT

# Notificări
SLACK_WEBHOOK_URL=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DISCORD_WEBHOOK_URL=...

# MonitoringWatchdog
WATCHDOG_ENABLED=true
WATCHDOG_CHECK_INTERVAL=60
WATCHDOG_SHARPE_MIN=0.3
WATCHDOG_MAX_DD=0.10
WATCHDOG_Z_MAX=4.0
WATCHDOG_HL_MAX=96

# AutoReoptimizer
OPTIMIZER_ENABLED=true
REOPT_SCHEDULE_DAY=6
REOPT_SCHEDULE_HOUR=2
REOPT_GRID_TYPE=coarse
REOPT_DRY_RUN=false
REOPT_MIN_SHARPE=0.5
REOPT_WFO_MIN_SCORE=0.5

# Paper trading (implicit)
DRY_RUN=true
```

---

## API Endpoints

| Prefix | Descriere |
|--------|-----------|
| `GET /metrics` | Prometheus scrape endpoint (S35) |
| `GET /risk/*` | Risk dashboard: Sharpe, DD, win rate, equity curve, SSE stream |
| `GET /api/decision/status` | DecisionEngine v2.5 live status (S46) |
| `GET /api/watchdog/*` | MonitoringWatchdog: thresholds, alerts, silence (S41–S44) |
| `GET /api/optimizer/*` | Grid Search WFO: run/status/results/history/heatmap |
| `GET /api/services/*` | Control Panel: start/stop/restart + WebSocket live |
| `GET /sizing/live_status` | SizingEngine v2.5 live status (S34) |
| `POST /sizing/reduce/{pair}` | Reduce sizing per pereche (S33) |
| `GET /sizing/reduce/history` | Audit log REDUCE events |
| `POST /pairs/halt/{pair}` | Halt pereche (S33) |
| `GET /pairs/status` | Status toate perechile active |
| `GET /backtest/*` | Backtest jobs REST |
| `GET /data/*` | OHLCV fetch Bybit/Binance |
| `GET /health` | Uptime, versiune, system status |
| `GET /docs` | Swagger UI |

---

## Prometheus `/metrics`

Configureaza scraping în `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: quantluna
    static_configs:
      - targets: ['localhost:8000']
```

Metrici expuse:

| Metric | Tip | Descriere |
|--------|-----|-----------|
| `quantluna_equity_usd` | gauge | Equity curentă USD |
| `quantluna_rolling_sharpe` | gauge | Sharpe ratio rolling window 30 |
| `quantluna_drawdown_current` | gauge | Drawdown curent (fractie) |
| `quantluna_drawdown_max` | gauge | Drawdown maxim sesiune |
| `quantluna_win_rate` | gauge | Win rate global |
| `quantluna_total_trades` | counter | Total trade-uri închise |
| `quantluna_exposure_usd` | gauge | Expunere totală USD |
| `quantluna_net_pnl_usd` | gauge | PnL net USD sesiune |
| `quantluna_pair_factor{pair}` | gauge | Factor sizing per pereche [0,1] |
| `quantluna_n_reduced_pairs` | gauge | Perechi cu factor < 1.0 |
| `quantluna_sizing_capital_usd` | gauge | Capital configurată SizingEngine |
| `quantluna_watchdog_enabled` | gauge | 1 dacă watchdog rulează |
| `quantluna_watchdog_alerts_total` | counter | Total alerte emise |
| `quantluna_watchdog_halted_pairs` | gauge | Perechi în stare HALT |
| `quantluna_decision_in_position` | gauge | 1 dacă există poziție deschisă |
| `quantluna_decision_streak` | gauge | Streak curent win/loss |
| `quantluna_decision_drawdown` | gauge | Drawdown curent DecisionEngine |

---

## MultiMarketOrchestrator

```python
from core.multi_market_orchestrator import MultiMarketOrchestrator

orch = MultiMarketOrchestrator.from_env(
    dispatcher=alert_dispatcher,
    runner=bybit_runner,
    notifier_bus=notifier_bus,
)
ctx = await orch.build_context()
await orch.start_runner(ctx)
```

Flux intern:
```
asyncio.gather(
    runner.start(),            ← BybitLiveRunner
    watchdog.run_loop(),       ← MonitoringWatchdog (60s)
    reoptimizer.run_loop(),    ← AutoReoptimizer (duminică 02:00 UTC)
)
```

---

## MonitoringWatchdog — Acțiuni

| Metric | Threshold default | Acțiune |
|---|---|---|
| `sharpe` rolling 24h | < 0.3 | `ALERT_ONLY` / `HALT` |
| `drawdown` | > 10% | `HALT` |
| `z_score` | \|z\| > 4.0 | `ALERT_ONLY` |
| `half_life` | > 96 ore | `ALERT_ONLY` |
| `loss_streak` | ≥ 5 | `ALERT_ONLY` / `HALT` |

Lanț complet watchdog → sizing:
```
MonitoringWatchdog
  → reduce_callback(pair, factor)
  → api.sizing.reduce_pair_size()
  → [Cale 1] SizingEngine.set_pair_factor()   ✅ S34
  → [Cale 2] MultiPairManager.set_alloc_factor() ✅ S33
  → [Fallback] WARNING log
```

---

## Teste

```bash
make test          # toate testele
make coverage      # cu coverage HTML
pytest tests/ -v
```

---

## Roadmap

| Sprint | Status | Conținut |
|--------|--------|----------|
| S1–S8  | ✅ Done | Core Kalman filter, Spread calculator, SignalGenerator, Data fetchers |
| S9–S11 | ✅ Done | Cointegration (Engle-Granger + Johansen), Ornstein-Uhlenbeck half-life |
| S12–S15 | ✅ Done | Backtest engine, Walk-forward validation, Optuna optimizer, Analytics, Monte Carlo |
| S16 | ✅ Done | OKX router, Multi-timeframe, VolatilityRegime, Dashboard API (FastAPI) |
| S17 | ✅ Done | OrderManager multi-exchange, CircuitBreaker, AdoptionEngine, ProfitOptimizer, Kelly, DynamicStop |
| S18 | ✅ Done | SpreadMonitor, RegimeFilter, NotifierBus fan-out |
| S19 | ✅ Done | AutoStrategySelector, KalmanScoringWeights SearchSpace (16 params), coint_pvalue_series rolling ADF |
| S20–S28 | ✅ Done | WorkflowOrchestrator (5 faze startup), PositionScanner, ResumeManager, EmergencyStop, HealthCheck, RiskDashboardEngine, StateBus, ConfigValidator, LiveDataBridge |
| S29–S31 | ✅ Done | MonitoringWatchdog (Sharpe/DD/z/hl/streak → HALT/REDUCE/ALERT), AlertDispatcher, AutoReoptimizer WFO + ParamGridOptimizer |
| S32 | ✅ Done | MultiMarketOrchestrator v2.2 — asyncio.gather(runner+watchdog+reoptimizer), from_env(), build_context(), metrics_provider cascadat 4 nivele |
| S33 | ✅ Done | `api/pairs.py` halt_pair + `api/sizing.py` reduce_pair_size — REST hooks pentru watchdog callbacks |
| S34 | ✅ Done | `SizingEngine` — wrapper stateful cu set_pair_factor(), cale 1 reduce completă |
| **S35** | ✅ **Done** | **Prometheus `/metrics`** (Risk+Sizing+Watchdog+Decision) + **teste S33/S34/S46** + **README v0.32.0** |
| S41–S44 | ✅ Done | Services Control Panel, Grid Search WFO optimizer, MonitoringWatchdog API router |
| S46 | ✅ Done | `DecisionEngine v2.5` — `/api/decision/status` dashboard unificat |
| S36 | 🔲 Next | End-to-end integration test suite, paper run automatizat 48h CI |
| S37 | 🔲 Next | Web UI React dashboard — live PnL charts, strategy scores, watchdog status |

---

## Docker

```bash
make docker-build
make docker-paper
make docker-dashboard
make docker-live
```

---

## Contributing

```bash
make install-dev
make lint
make format
make typecheck
make test
```

---

## License

MIT © 2025–2026 George Pricop

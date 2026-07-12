# QuantLuna

> **Stat-arb trading system** — pairs trading cu Kalman filter, cointegration analysis, multi-exchange execution, MonitoringWatchdog și AutoReoptimizer WFO.

[![CI](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml/badge.svg)](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Gzeu/quantluna/branch/main/graph/badge.svg)](https://codecov.io/gh/Gzeu/quantluna)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.16.0-green.svg)](CHANGELOG.md)

---

## Arhitectură

```
┌──────────────────────────────────────────────────────────────────────┐
│                           QuantLuna v0.16                            │
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
│  ─────────────────                                                   │
│  MonitoringWatchdog  →  AlertDispatcher  →  Telegram HALT/REDUCE     │
│  CircuitBreaker          PairThreshold        ALERT_ONLY             │
│  HealthCheck             MetricsProvider   (Sharpe/DD/z/hl/streak)   │
│  WsWatchdog             RiskDashboardEngine                          │
│  AutoRebalancer          DrawdownController                          │
│  KellyPositionSizer      MultiPairAllocator                          │
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
│   ├── multi_market_orchestrator.py  # MultiMarketOrchestrator v2.2 (Sprint 32)
│   ├── monitoring_watchdog.py        # MonitoringWatchdog (Sprint 44)
│   ├── kalman_filter.py              # Kalman filter dinamic hedge ratio
│   ├── kalman_adapter.py             # Adaptor Kalman → BaseStrategy
│   ├── spread.py                     # Spread computation + z-score
│   ├── spread_monitor.py             # Real-time spread health monitor
│   ├── cointegration.py              # Engle-Granger + Johansen tests
│   ├── regime_detector.py            # Detectie trend vs mean-reversion
│   ├── correlation_matrix.py         # Matrice corelatie portofoliu
│   ├── funding_rate.py               # Funding rate monitor
│   ├── live_data_bridge.py           # Bridge date live multi-exchange
│   ├── metrics.py                    # Metrici performanta
│   ├── performance_analytics.py      # Analytics performanta
│   ├── config_validator.py           # Validare configurare startup
│   └── state_bus.py                  # StateBus pub/sub intern
│
├── strategy/
│   ├── signal.py                     # SignalGenerator v4 — logica principala
│   ├── kalman_pairs_trading.py       # KalmanPairsTrading BaseStrategy wrapper
│   ├── auto_selector.py              # AutoStrategySelector — scorer + switcher
│   ├── optimizer.py                  # Optuna optimizer + KalmanScoringWeights
│   ├── multi_strategy_engine.py      # Engine multi-strategie paralel
│   ├── multi_timeframe.py            # Confirmare MTF (LTF + HTF)
│   ├── regime_filter.py              # Gate unificat regim
│   ├── pair_selector.py              # Selectie perechi cointegrate
│   ├── entry_filter.py               # Filtru intrare semnal
│   ├── bb_mean_reversion.py          # Bollinger Bands mean reversion
│   ├── zscore_momentum.py            # Z-score momentum strategy
│   ├── funding_arb.py                # Funding rate arbitrage
│   └── stat_arb.py                   # Statistical arbitrage clasic
│
├── execution/
│   ├── workflow_orchestrator.py      # WorkflowOrchestrator (startup 5 faze)
│   ├── bybit_live_runner.py          # Runner live Bybit
│   ├── order_manager.py              # Lifecycle comenzi multi-exchange
│   ├── bybit_order_router.py         # Router Bybit futures
│   ├── position_scanner.py           # Scan pozitii → MANAGED/ORPHAN
│   ├── adoption_engine.py            # Decizie ADOPT/CLOSE_NOW/MONITOR
│   ├── profit_optimizer.py           # TP/SL, break-even, trailing stop
│   ├── health_check.py               # Health checks sistem
│   ├── resume_manager.py             # Resume dupa restart
│   ├── checkpoint.py                 # Persistenta stare
│   ├── pnl_reconciler.py             # Reconciliere PnL
│   ├── exchange_factory.py           # Factory exchange instances
│   ├── bybit_ws_feed.py              # Feed WebSocket Bybit
│   ├── bybit_private_ws.py           # WS privat Bybit (ordine/pozitii)
│   ├── ws_watchdog.py                # Watchdog WebSocket
│   ├── emergency_stop.py             # EmergencyStop (HALT complet)
│   └── rate_limiter.py               # Rate limiter API
│
├── risk/
│   ├── circuit_breaker.py            # Circuit breaker auto-reset
│   ├── kelly.py                      # Kelly criterion (full)
│   ├── dynamic_stop.py               # Stop dinamic ATR/vol-based
│   ├── portfolio_risk.py             # Risk management portofoliu
│   ├── auto_rebalancer.py            # Auto-rebalancer pozitii
│   ├── correlation_filter.py         # Filtru corelatie
│   ├── dashboard_engine.py           # Engine risk dashboard
│   ├── drawdown_controller.py        # Controller drawdown
│   └── multi_pair_allocator.py       # Alocator multi-perechi
│
├── backtest/
│   ├── auto_reoptimizer.py           # AutoReoptimizer WFO saptamanal (Sprint 40)
│   ├── param_grid_optimizer.py       # ParamGridOptimizer + GridSpace
│   ├── backtest_engine.py            # BacktestEngine principal
│   ├── engine_adapter.py             # Adaptor engine multi-strategie
│   ├── analytics.py                  # Metrici performanta backtest
│   ├── monte_carlo.py                # Simulari Monte Carlo
│   ├── walk_forward.py               # Walk-forward validation
│   └── report_builder.py             # Rapoarte HTML/JSON backtest
│
├── notifications/
│   ├── notifier_bus.py               # Fan-out bus notificari
│   ├── alert_dispatcher.py           # AlertDispatcher (Watchdog → Telegram)
│   ├── telegram.py                   # Notificari Telegram
│   ├── slack_notifier.py             # Notificari Slack
│   └── discord.py                    # Notificari Discord
│
├── api/
│   ├── dashboard_api.py              # REST API dashboard (FastAPI)
│   ├── risk.py                       # /api/risk/* (RiskDashboardEngine)
│   ├── watchdog.py                   # /api/watchdog/* (MonitoringWatchdog status)
│   ├── pairs.py                      # /api/pairs/* (halt_pair etc.)
│   └── sizing.py                     # /api/sizing/* (reduce_pair_size etc.)
│
├── data/
│   ├── fetcher.py                    # Fetcher date istorice
│   ├── historical_fetcher.py         # Fetcher OHLCV multi-exchange
│   └── store.py                      # Persistenta date OHLCV
│
├── tests/                            # 45+ fisiere de teste
├── main.py                           # Entry point principal
├── config.py                         # Configurare globala
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

### Dependente principale

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
PAIRS=BTCUSDT-ETHUSDT,SOLUSDT-AVAXUSDT    # lista perechi active
SYMBOL_Y=BTCUSDT                           # fallback single-pair
SYMBOL_X=ETHUSDT

# Notificari
SLACK_WEBHOOK_URL=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DISCORD_WEBHOOK_URL=...

# MonitoringWatchdog
WATCHDOG_ENABLED=true
WATCHDOG_CHECK_INTERVAL=60     # secunde intre verificari
WATCHDOG_SHARPE_MIN=0.3        # Sharpe rolling 24h minim
WATCHDOG_MAX_DD=0.10           # Drawdown maxim (fractie)
WATCHDOG_Z_MAX=4.0             # |z-score| maxim
WATCHDOG_HL_MAX=96             # Half-life maxim (ore)

# AutoReoptimizer
OPTIMIZER_ENABLED=true
REOPT_SCHEDULE_DAY=6           # 0=Luni, 6=Duminica
REOPT_SCHEDULE_HOUR=2          # 02:00 UTC
REOPT_GRID_TYPE=coarse         # coarse | fine
REOPT_DRY_RUN=false
REOPT_MIN_SHARPE=0.5
REOPT_WFO_MIN_SCORE=0.5

# Paper trading (implicit)
DRY_RUN=true
```

---

## Utilizare

### Paper trading

```bash
python main.py --dry-run --pair BTCUSDT/ETHUSDT
# sau
make paper
```

### Live trading

```bash
python main.py --pair BTCUSDT/ETHUSDT
# sau
make live
```

### Backtest

```bash
python main.py backtest --pair BTCUSDT ETHUSDT --days 90
# sau
make backtest
```

### Dashboard

```bash
uvicorn dashboard.server:app --reload --port 8000
# http://localhost:8000/docs
```

---

## MultiMarketOrchestrator

Din `v0.16.0`, `core/multi_market_orchestrator.py` gestionează execuția simultană a tuturor subsistemelor runtime:

```python
from core.multi_market_orchestrator import MultiMarketOrchestrator

# Din env vars (PAIRS, WATCHDOG_ENABLED, OPTIMIZER_ENABLED etc.)
orch = MultiMarketOrchestrator.from_env(
    dispatcher=alert_dispatcher,
    runner=bybit_runner,
    notifier_bus=notifier_bus,
)
ctx = await orch.build_context()
await orch.start_runner(ctx)   # blocheaza pana la stop()

# Cu config explicit + watchdog per-pereche
orch = MultiMarketOrchestrator(
    pairs=["BTCUSDT-ETHUSDT", "SOLUSDT-AVAXUSDT"],
    runner=bybit_runner,
    notifier_bus=bus,
    dispatcher=alert_dispatcher,
    per_pair_watchdog_cfg={
        "BTCUSDT-ETHUSDT": {"sharpe_min": 0.5, "action": "HALT"},
        "SOLUSDT-AVAXUSDT": {"max_drawdown": 0.08, "action": "REDUCE_SIZE"},
    },
)
```

Flux intern:
```
asyncio.gather(
    runner.start(),            ← BybitLiveRunner (trading loop)
    watchdog.run_loop(),       ← MonitoringWatchdog (60s: Sharpe/DD/z/hl/streak)
    reoptimizer.run_loop(),    ← AutoReoptimizer (duminica 02:00 UTC)
)
```

Metrics provider este rezolvat automat în cascadă:
1. `runner.get_pair_metrics(pair)` — RiskManager nativ
2. `runner.risk_manager.get_metrics(pair)` — RiskManager separat
3. `runner.pnl_tracker.get_metrics(pair)` — PnLTracker fallback
4. stub safe `{sharpe:99, drawdown:0, z_score:0, ...}` — niciodată crash

---

## MonitoringWatchdog — Acțiuni

| Metric | Threshold default | Acțiune |
|---|---|---|
| `sharpe` rolling 24h | < 0.3 | `ALERT_ONLY` / `HALT` |
| `drawdown` | > 10% | `HALT` |
| `z_score` | \|z\| > 4.0 | `ALERT_ONLY` |
| `half_life` | > 96 ore | `ALERT_ONLY` |
| `loss_streak` | ≥ 5 | `ALERT_ONLY` / `HALT` |

Acțiunile disponibile: `ALERT_ONLY` → `REDUCE_SIZE` (sizing 50%) → `HALT` (oprire completă).

---

## AutoReoptimizer — WFO Săptămânal

```python
from backtest.auto_reoptimizer import AutoReoptimizer

scheduler = AutoReoptimizer.from_env(
    engine=backtest_engine,
    pairs=["BTCUSDT-ETHUSDT", "SOLUSDT-AVAXUSDT"],
    notifier_bus=bus,
)
await scheduler.run_loop()  # pornit automat de MultiMarketOrchestrator

# Trigger manual:
await scheduler.run_now(force=True)
```

Logică: dacă `oos_sharpe >= 0.5` **și** `wfo_score >= 0.5`, parametrii noi sunt aplicați în `config/pairs/PAIR.json` și raportați pe Telegram. Altfel, parametrii actuali sunt păstrați și se emite alertă de degradare.

---

## AutoStrategySelector + KalmanScoringWeights

```python
from strategy.auto_selector import AutoStrategySelector
from strategy.kalman_pairs_trading import KalmanPairsTrading, KalmanScoringWeights

weights = KalmanScoringWeights(baseline=0.65, regime_ranging_bonus=0.18)
kalman = KalmanPairsTrading(spread_engine=engine, scoring_weights=weights)

selector = AutoStrategySelector(strategies=[kalman, bb, zscore_mom, funding_arb])
signals = selector.generate_batch(
    df=spread_df,
    coint_pvalue_series=coint_pvalue_series,
    coint_valid_series=coint_valid_series,
)
```

---

## Teste

```bash
# Toate testele
make test

# Cu coverage HTML
make coverage

# Pytest direct
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
| **S32** | ✅ **Done** | **MultiMarketOrchestrator v2.2** — asyncio.gather(runner+watchdog+reoptimizer), from_env(), build_context(), stop_runner() graceful, metrics_provider cascadat 4 nivele |
| S33 | 🔲 Next | `api/pairs.py` + `api/sizing.py` — endpoint-uri REST halt_pair / reduce_pair_size (necesare de halt_callback / reduce_callback) |
| S34 | 🔲 Next | Prometheus `/metrics` endpoint + Grafana alerting rules |
| S35 | 🔲 Next | Web UI React dashboard — live PnL charts, strategy scores, watchdog status |
| S36 | 🔲 Next | End-to-end integration test suite, paper run automatizat 48h CI |

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
make install-dev    # install + pre-commit hooks
make lint           # ruff check
make format         # ruff format
make typecheck      # mypy
make test           # pytest
```

---

## License

MIT © 2025–2026 George Pricop

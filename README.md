# QuantLuna

> Cryptocurrency statistical arbitrage system — pairs trading cu Kalman filter, walk-forward optimization și dashboard Next.js în timp real.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-15-black?logo=next.js)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

---

## Prezentare generală

QuantLuna este un sistem complet de **stat-arb pe crypto** construit în Python + FastAPI (backend) și Next.js + TypeScript (dashboard). Sistemul rulează perechi cointegrate (ex. BTC/ETH), calculează spread-ul cu un Kalman filter adaptiv, detectează semnale de intrare/ieșire pe baza Z-score și execută ordine pe Bybit, Binance sau OKX.

**Starea curentă:** paper trading funcțional, API REST complet, dashboard live operațional.

---

## Stack

| Layer | Tehnologie |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, asyncio, aiohttp |
| Date | Bybit/Binance/OKX REST + WebSocket |
| Strategie | Kalman filter, cointegration (Engle-Granger + Johansen), Optuna TPE |
| Risk | Kelly sizing, circuit breaker, drawdown controller, MonitoringWatchdog |
| Backtest | Walk-forward engine, Monte Carlo, Optuna grid search |
| Dashboard | Next.js 15, TypeScript, Tailwind CSS, Recharts, Zustand |
| Monitoring | Prometheus `/metrics`, Telegram/Slack/Discord alerts |
| DevOps | Docker, docker-compose, Makefile, pytest, Ruff, pyright |

---

## Arhitectură

```
┌─────────────────────────────────────────────────────────────────────┐
│                         QuantLuna v0.33                             │
│                                                                     │
│  Date               Strategie              Execuție                 │
│  ────               ─────────              ────────                 │
│  BybitFetcher  →    KalmanFilter     →     OrderManager             │
│  BinanceFetcher     Cointegration           ├─ BybitRouter           │
│  OKXFetcher         SpreadSignal            ├─ BinanceRouter         │
│  LiveDataBridge     ZScoreDetector          └─ OKXRouter             │
│  MarketDataCache    RegimeDetector                                   │
│                     AutoStrategySelector →  CircuitBreaker           │
│                     FundingRateFilter        PositionScanner         │
│                     VolatilityRegime         AdoptionEngine          │
│                     CorrelationMatrix        ProfitOptimizer         │
│                                                                     │
│  Orchestrare                                                        │
│  ────────────                                                       │
│  WorkflowOrchestrator   (startup 5 faze: HealthCheck → Runner)     │
│  MultiMarketOrchestrator (runtime):                                 │
│    asyncio.gather(runner.start(),                                   │
│                   watchdog.run_loop(),                              │
│                   reoptimizer.run_loop())                           │
│                                                                     │
│  Risk & Monitoring                                                  │
│  ─────────────────                                                  │
│  MonitoringWatchdog  →  AlertDispatcher  →  Telegram/Slack/Discord  │
│  CircuitBreaker          PairThreshold    (HALT / REDUCE / ALERT)   │
│  SizingEngine            DrawdownController                         │
│  KellyPositionSizer      MultiPairAllocator                         │
│  AutoRebalancer          DecisionEngine v2.5                        │
│                                                                     │
│  Backtest & Optimizer                                               │
│  ────────────────────                                               │
│  AutoReoptimizer   (WFO săptămânal, aplică params automat)          │
│  ParamGridOptimizer (GridSpace coarse/fine, OOS Sharpe + WFO score) │
│  WalkForwardEngine   MonteCarlo   Optuna TPE (16 parametri)         │
│                                                                     │
│  Dashboard (Next.js 15 + TypeScript)                                │
│  ───────────────────────────────────                                │
│  NavBar + 8 pagini: Dashboard, Portfolio, Services, Optimizer,      │
│  Watchdog, Strategy, Risk, Backtest                                 │
│  WebSocket live + polling REST + Zustand store                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Backend Python

```bash
git clone https://github.com/Gzeu/quantluna.git
cd quantluna

# Instalare dependențe
pip install -r requirements.txt

# Configurare
cp .env.example .env
# → editează .env cu cheile tale API

# Paper trading (fără ordine reale)
python main.py --dry-run

# API server (port 8000)
uvicorn api.main:app --reload --port 8000
```

### 2. Dashboard Next.js

```bash
cd dashboard
cp .env.local.example .env.local
# → setează NEXT_PUBLIC_API_URL=http://localhost:8000

npm install
npm run dev   # → http://localhost:3000
```

### 3. Docker (recomandat pentru producție)

```bash
make docker-build
make docker-paper     # paper trading + API + dashboard
make docker-live      # live trading (necesită chei reale)
```

---

## Configurare `.env`

```env
# ── Exchange API Keys ─────────────────────────────────
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_PASSPHRASE=...

# ── Trading ────────────────────────────────────────────
DRY_RUN=true                        # true = paper trading
PAIRS=BTCUSDT-ETHUSDT,SOLUSDT-AVAXUSDT
SYMBOL_Y=BTCUSDT
SYMBOL_X=ETHUSDT

# ── MonitoringWatchdog ─────────────────────────────────
WATCHDOG_ENABLED=true
WATCHDOG_CHECK_INTERVAL=60          # secunde
WATCHDOG_SHARPE_MIN=0.3
WATCHDOG_MAX_DD=0.10                # 10%
WATCHDOG_Z_MAX=4.0
WATCHDOG_HL_MAX=96                  # ore

# ── AutoReoptimizer ────────────────────────────────────
OPTIMIZER_ENABLED=true
REOPT_SCHEDULE_DAY=6                # duminică
REOPT_SCHEDULE_HOUR=2               # 02:00 UTC
REOPT_GRID_TYPE=coarse
REOPT_MIN_SHARPE=0.5
REOPT_WFO_MIN_SCORE=0.5

# ── Notificări ─────────────────────────────────────────
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SLACK_WEBHOOK_URL=...
DISCORD_WEBHOOK_URL=...
```

---

## Structura proiectului

```
quantluna/
├── core/                        # Logică core: Kalman, spread, cointegration, regime
├── strategy/                    # Semnale: ZScore, MultiTimeframe, AutoSelector, stat-arb
├── execution/                   # Ordine, workflow, health check, resume, checkpoints
├── risk/                        # Sizing (Kelly), circuit breaker, drawdown, allocator
├── backtest/                    # Engine, walk-forward, Monte Carlo, Optuna, analytics
├── api/                         # FastAPI: 14 routere REST + WebSocket
│   ├── main.py                  # App FastAPI + CORS + lifespan
│   ├── metrics.py               # GET /metrics — Prometheus scrape
│   ├── decision.py              # GET /api/decision/status
│   ├── watchdog.py              # GET /api/watchdog/*
│   ├── optimizer.py             # GET/POST /api/optimizer/*
│   ├── services.py              # GET /api/services/*
│   ├── risk.py                  # GET /risk/* + SSE stream
│   ├── backtest.py              # POST /backtest/run
│   └── ...
├── notifications/               # Telegram, Slack, Discord, NotifierBus
├── data/                        # Fetcher OHLCV, historical, store
├── tests/                       # 55+ fișiere de teste pytest
├── dashboard/                   # Next.js 15 dashboard
│   ├── pages/                   # 8 pagini: index, portfolio, services, optimizer,
│   │                            #           watchdog, strategy, risk, backtest
│   ├── components/              # NavBar, StatsBar, charts, panels, modals
│   ├── hooks/                   # useQuantLunaWS, useRiskMetrics, useServices, ...
│   ├── store/                   # Zustand store (quantlunaStore + dashboardSlice)
│   ├── types/                   # TypeScript types
│   └── app/globals.css          # Design system (CSS vars, tokens, Tailwind)
├── main.py
├── config.py
├── Makefile
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

---

## API Endpoints

| Endpoint | Metodă | Descriere |
|----------|--------|-----------|
| `/health` | GET | Uptime, versiune, system status |
| `/docs` | GET | Swagger UI interactiv |
| `/metrics` | GET | Prometheus scrape (Gauge + Counter) |
| `/risk/*` | GET | Sharpe, drawdown, win rate, equity curve, SSE stream |
| `/api/decision/status` | GET | DecisionEngine v2.5 — status unificat |
| `/api/watchdog/*` | GET/POST | Thresholds, alerte, silence, HALT |
| `/api/optimizer/*` | GET/POST | Run grid search WFO, status, rezultate |
| `/api/services/*` | GET/POST | Start/stop/restart servicii + WebSocket |
| `/sizing/live_status` | GET | SizingEngine v2.5 — factori per pereche |
| `/sizing/reduce/{pair}` | POST | Reduce sizing pentru o pereche |
| `/pairs/halt/{pair}` | POST | Halt pereche activă |
| `/pairs/status` | GET | Status toate perechile active |
| `/backtest/run` | POST | Pornire job backtest |
| `/data/*` | GET | OHLCV fetch Bybit/Binance |

---

## Prometheus `/metrics`

Adaugă în `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: quantluna
    static_configs:
      - targets: ['localhost:8000']
    scrape_interval: 15s
```

Metrici principale expuse:

| Metric | Tip | Descriere |
|--------|-----|-----------|
| `quantluna_equity_usd` | gauge | Equity curentă USD |
| `quantluna_rolling_sharpe` | gauge | Sharpe rolling 30 zile |
| `quantluna_drawdown_current` | gauge | Drawdown curent (fracție) |
| `quantluna_drawdown_max` | gauge | Drawdown maxim sesiune |
| `quantluna_win_rate` | gauge | Win rate global |
| `quantluna_total_trades` | counter | Trade-uri închise |
| `quantluna_exposure_usd` | gauge | Expunere totală USD |
| `quantluna_net_pnl_usd` | gauge | PnL net USD sesiune |
| `quantluna_pair_factor{pair}` | gauge | Factor sizing [0, 1] per pereche |
| `quantluna_watchdog_alerts_total` | counter | Total alerte emise |
| `quantluna_watchdog_halted_pairs` | gauge | Perechi în HALT |
| `quantluna_decision_in_position` | gauge | 1 dacă există poziție deschisă |

---

## MonitoringWatchdog

Verifică metricile la fiecare 60s și aplică acțiuni automate:

| Metric | Threshold default | Acțiune |
|--------|------------------|---------|
| Sharpe rolling 24h | < 0.3 | `ALERT_ONLY` → `HALT` |
| Drawdown | > 10% | `HALT` imediat |
| \|Z-score\| | > 4.0 | `ALERT_ONLY` |
| Half-life | > 96 ore | `ALERT_ONLY` |
| Loss streak | ≥ 5 | `ALERT_ONLY` → `HALT` |

Lanț de reducere sizing:

```
MonitoringWatchdog
  → reduce_callback(pair, factor)
  → SizingEngine.set_pair_factor()    # cale 1 (S34)
  → MultiPairAllocator.set_alloc_factor()  # cale 2 (S33)
  → AlertDispatcher → Telegram/Slack/Discord
```

---

## Dashboard — Pagini

| Pagină | Shortcut | Conținut |
|--------|----------|----------|
| `/` — Dashboard | `G+D` | PnL chart, MetricsBadge, Watchdog, Spread, Arb, Heatmap, Candlestick, ExecutionLog |
| `/portfolio` | `G+P` | BalanceTracker, PnlChart, TradeBreakdown |
| `/services` | `G+S` | Tabel servicii live (status, PID, uptime, CPU, MEM) |
| `/optimizer` | `G+O` | Run/stop WFO, iterații, best score, best params |
| `/watchdog` | `G+W` | Alerte, thresholds, status MonitoringWatchdog |
| `/strategy` | `G+T` | StrategyScores per pereche |
| `/risk` | `G+R` | Risk grid: equity, drawdown, Sharpe, win rate, streak |
| `/backtest` | `G+B` | Form configurare, trade log, summary cards |

Toate paginile: WebSocket live (`useQuantLunaWS`), polling REST, keyboard shortcuts globale.

---

## Teste

```bash
make test          # rulează toate testele
make coverage      # raport HTML coverage
pytest tests/ -v --tb=short
```

Structura testelor acoperă: Kalman filter, cointegration, backtest engine, walk-forward, risk sizing, watchdog, API endpoints, orchestrator.

---

## Roadmap

| Sprint | Status | Conținut |
|--------|--------|----------|
| S1–S15 | ✅ Done | Core Kalman, Cointegration, Backtest engine, Walk-forward, Optuna, Monte Carlo |
| S16–S20 | ✅ Done | Multi-exchange execution, CircuitBreaker, Kelly sizing, SpreadMonitor, AutoStrategySelector |
| S21–S28 | ✅ Done | WorkflowOrchestrator (5 faze), ResumeManager, EmergencyStop, RiskDashboardEngine, StateBus |
| S29–S31 | ✅ Done | MonitoringWatchdog (HALT/REDUCE/ALERT), AlertDispatcher, AutoReoptimizer WFO |
| S32–S34 | ✅ Done | MultiMarketOrchestrator v2.2, API hooks sizing/pairs, SizingEngine set_pair_factor() |
| S35 | ✅ Done | Prometheus `/metrics`, DecisionEngine v2.5, teste complete |
| S41–S44 | ✅ Done | Services Control Panel API, Grid Search WFO, MonitoringWatchdog router |
| **S37** | ✅ **Done** | **Dashboard Next.js 15** — 8 pagini, design system, WebSocket live, hooks, modals |
| S36 | 🔲 Next | End-to-end integration tests, paper run 48h CI automatizat |
| S38 | 🔲 Planificat | Grafice avansate: equity curve interactivă, drawdown chart, corelație heatmap |
| S39 | 🔲 Planificat | Alerting în dashboard: push notifications browser, threshold editor UI |
| S40 | 🔲 Planificat | Multi-account support, role-based access |

---

## Docker

```bash
# Build
make docker-build

# Paper trading (DRY_RUN=true)
make docker-paper

# Live trading
make docker-live

# Doar dashboard
make docker-dashboard
```

`docker-compose.yml` pornește: API FastAPI (8000), Dashboard Next.js (3000), Prometheus (9090), opțional Grafana (3001).

---

## Development

```bash
make install-dev    # dependențe dev (ruff, pyright, pytest-cov)
make lint           # ruff check
make format         # ruff format
make typecheck      # pyright
make test           # pytest
make coverage       # pytest + htmlcov
```

---

## Avertisment

Acest proiect este în dezvoltare activă și rulează în **paper trading** (DRY_RUN=true) implicit. Utilizarea în live trading implică riscuri financiare semnificative. Nu este sfat financiar.

---

## Licență

MIT © 2025–2026 George Pricop

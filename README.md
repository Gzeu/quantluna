# QuantLuna

> **Stat-arb trading system** — pairs trading cu Kalman filter, cointegration analysis, multi-exchange execution și risk management complet.

[![CI](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml/badge.svg)](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Gzeu/quantluna/branch/main/graph/badge.svg)](https://codecov.io/gh/Gzeu/quantluna)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.14.0-green.svg)](CHANGELOG.md)

---

## Arhitectură

```
┌─────────────────────────────────────────────────────────────┐
│                        QuantLuna                            │
│                                                             │
│  Data Layer          Strategy Layer       Execution Layer   │
│  ───────────         ───────────────      ───────────────   │
│  BybitFetcher   →    KalmanFilter    →    OrderManager      │
│  BinanceFetcher      Cointegration        ├─ BybitRouter    │
│  OKXFetcher          SpreadSignal         ├─ BinanceRouter  │
│  MarketDataCache     MultiTimeframe       └─ OKXRouter      │
│                      VolatilityRegime                        │
│                      RegimeFilter    →    CircuitBreaker     │
│                      SpreadMonitor        PositionScanner   │
│                                          AdoptionEngine     │
│                                          ProfitOptimizer    │
│                                                             │
│  Risk / Monitoring                                          │
│  ─────────────────                                          │
│  CircuitBreaker  →  NotifierBus  →  Slack / Telegram /      │
│  HealthCheck        PnLReconciler    Discord                 │
│  WsWatchdog         Checkpoint                              │
│  AutoRebalancer     CorrelationFilter                        │
│  DrawdownController KellyPositionSizer                      │
└─────────────────────────────────────────────────────────────┘
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
python main.py paper --pair BTCUSDT ETHUSDT --exchange bybit
```

---

## Structura proiectului

```
quantluna/
├── core/
│   ├── kalman_filter.py          # Kalman filter dinamic hedge ratio
│   ├── spread_calculator.py      # Spread computation + z-score
│   ├── spread_monitor.py         # Real-time spread health monitor
│   ├── volatility_regime.py      # Regim volatilitate LOW/NORMAL/HIGH/EXTREME
│   ├── cointegration.py          # Engle-Granger + Johansen tests
│   ├── half_life.py              # Ornstein-Uhlenbeck half-life
│   └── market_data_cache.py      # Cache date de piata
│
├── strategy/
│   ├── signal_generator.py       # Generator semnale intrare/iesire
│   ├── multi_timeframe.py        # Confirmare MTF (LTF + HTF)
│   ├── regime_filter.py          # Gate unificat regim
│   ├── auto_selector.py          # Selectie automata perechi
│   └── trend_regime_detector.py  # Detectie trend vs mean-reversion
│
├── execution/
│   ├── order_manager.py          # Lifecycle comenzi multi-exchange
│   ├── bybit_order_router.py     # Router Bybit futures
│   ├── binance_order_router.py   # Router Binance futures
│   ├── okx_order_router.py       # Router OKX futures
│   ├── position_scanner.py       # Scan pozitii → MANAGED/ORPHAN
│   ├── adoption_engine.py        # Decizie ADOPT/CLOSE_NOW/MONITOR
│   ├── profit_optimizer.py       # TP/SL, break-even, trailing stop
│   ├── live_trader.py            # Trader live principal
│   ├── paper_trader.py           # Paper trading complet
│   ├── paper_engine.py           # Engine simulare ordine paper
│   ├── multi_pair_manager.py     # Manager multi-perechi
│   ├── workflow_orchestrator.py  # Orchestrare workflow principal
│   ├── checkpoint.py             # Persistenta stare
│   ├── pnl_reconciler.py         # Reconciliere PnL
│   ├── partial_exit_handler.py   # Iesiri partiale
│   ├── funding_monitor.py        # Monitor funding rate
│   ├── health_check.py           # Health checks sistem
│   ├── resume_manager.py         # Resume dupa restart
│   ├── ws_watchdog.py            # Watchdog WebSocket
│   ├── bybit_ws_feed.py          # Feed WebSocket Bybit
│   ├── bybit_private_ws.py       # WS privat Bybit (ordine/pozitii)
│   ├── exchange_factory.py       # Factory exchange instances
│   ├── rate_limiter.py           # Rate limiter API
│   ├── backoff.py                # Retry cu exponential backoff
│   └── bybit_live_runner.py      # Runner live Bybit
│
├── risk/
│   ├── circuit_breaker.py        # Circuit breaker auto-reset
│   ├── kelly.py                  # Kelly criterion sizer
│   ├── portfolio_risk.py         # Risk management portofoliu
│   ├── auto_rebalancer.py        # Auto-rebalancer pozitii
│   ├── bybit_position_sizer.py   # Position sizer Bybit-specific
│   ├── correlation_filter.py     # Filtru corelatie
│   ├── correlation_matrix.py     # Matrice corelatie portofoliu
│   ├── dashboard_engine.py       # Engine risk dashboard
│   ├── drawdown_controller.py    # Controller drawdown
│   ├── multi_pair_allocator.py   # Alocator multi-perechi
│   └── position_sizer_factory.py # Factory position sizers
│
├── notifications/
│   ├── notifier_bus.py           # Fan-out bus notificari
│   ├── slack_notifier.py         # Notificari Slack
│   ├── telegram.py               # Notificari Telegram
│   └── discord.py                # Notificari Discord
│
├── backtest/
│   ├── engine.py                 # Engine backtest vectorizat
│   ├── walk_forward.py           # Walk-forward validation
│   ├── walk_forward_optimizer.py # Optimizare Optuna walk-forward
│   └── report_builder.py         # Rapoarte HTML/JSON backtest
│
├── data/
│   ├── fetcher.py                # Fetcher date istorice
│   ├── historical_fetcher.py     # Fetcher OHLCV multi-exchange
│   └── store.py                  # Persistenta date OHLCV
│
├── api/
│   ├── dashboard_api.py          # REST API dashboard (FastAPI)
│   └── strategy_api.py           # API strategie
│
├── analytics/
│   ├── analytics.py              # Metrici performanta
│   └── risk_dashboard.py         # Dashboard risc
│
├── tests/                        # 45+ fisiere de teste
│   ├── conftest.py
│   └── ...                       # Teste pentru fiecare modul
│
├── main.py                       # Entry point principal
├── config.py                     # Configurare globala
├── Makefile                      # Dev workflow shortcuts
├── Dockerfile                    # Multi-stage production build
├── docker-compose.yml            # Servicii: paper, live, dashboard, backtest
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
aiohttp >= 3.9
fastapi >= 0.111
loguru >= 0.7
pytest >= 8.0
pytest-asyncio >= 0.23
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

# Notificari
SLACK_WEBHOOK_URL=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DISCORD_WEBHOOK_URL=...

# Paper trading (implicit)
DRY_RUN=true
```

---

## Utilizare

### Paper trading (recomandat pentru start)

```bash
python main.py paper --pair BTCUSDT ETHUSDT --exchange bybit
# sau
make paper
```

### Live trading

```bash
python main.py live --pair BTCUSDT ETHUSDT --yes
# sau
make live
```

### Backtest

```bash
python main.py backtest --pair BTCUSDT ETHUSDT --days 90 --timeframe 1h
# sau
make backtest
```

### Walk-forward + Optuna

```bash
python main.py scan --exchange bybit --top 20
# urmat de:
python scripts/optimize_params.py --pair BTCUSDT ETHUSDT --trials 200
```

### Dashboard

```bash
uvicorn dashboard.server:app --reload --port 8000
# http://localhost:8000/docs
# sau
make docker-dashboard
```

---

## Teste

```bash
# Toate testele
make test

# Cu coverage HTML
make coverage
# → deschide htmlcov/index.html

# Doar un sprint
pytest tests/test_sprint18.py -v

# Smoke tests integrare
pytest tests/test_smoke_s18.py tests/test_smoke_s15_s17.py -v
```

---

## Componente cheie

### RegimeFilter — Gatekeeper central

```python
from strategy.regime_filter import RegimeFilter
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.spread_monitor import SpreadMonitor

cb = CircuitBreaker(CircuitBreakerConfig(max_consecutive_losses=3))
sm = SpreadMonitor()
rf = RegimeFilter(circuit_breaker=cb, spread_monitor=sm)

report = sm.update(spread, zscore, half_life, kalman_p_diag)
gate = rf.check(ltf_zscore=zscore, htf_zscore=htf_z, spread_report=report)

if gate.allowed:
    qty = base_qty * gate.size_multiplier
    await order_manager.submit(OrderRequest(...))
```

### OrderManager — Multi-exchange lifecycle

```python
from execution.order_manager import OrderManager, OrderManagerConfig, OrderRequest

manager = OrderManager(OrderManagerConfig(dry_run=True))
await manager.start()

local_id = await manager.submit(OrderRequest(
    venue="bybit", symbol="BTCUSDT",
    side="BUY", qty=0.01, order_type="MARKET"
))
```

### CircuitBreaker — Auto-reset

```python
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

cb = CircuitBreaker(CircuitBreakerConfig(
    max_consecutive_losses=3,
    max_drawdown_pct=5.0,
    cooldown_seconds=3600,
))
cb.record_trade(pnl=-200.0)
if not cb.is_open:
    print(cb.status())
```

### NotifierBus — Fan-out notificari

```python
from notifications.notifier_bus import NotifierBus
from notifications.slack_notifier import SlackNotifier, SlackConfig

bus = NotifierBus()
bus.register("slack", SlackNotifier(SlackConfig(webhook_url="https://...")))

await bus.send_entry_signal("BTCUSDT", "LONG", zscore=2.4)
await bus.send_circuit_breaker_trip("drawdown", "5% hit", cooldown_s=3600)
```

---

## Roadmap

| Sprint | Status | Continut |
|--------|--------|----------|
| S1–S8  | ✅ Done | Core Kalman, Spread, Signal, Data fetching |
| S9–S11 | ✅ Done | Cointegration (EG + Johansen), half-life |
| S12–S15| ✅ Done | Backtest engine, Walk-forward, Report builder |
| S14    | ✅ Done | Optuna optimizer |
| S16    | ✅ Done | OKX router, Multi-timeframe, Vol regime, Dashboard API |
| S17    | ✅ Done | OrderManager, CircuitBreaker, Slack, AdoptionEngine, ProfitOptimizer |
| S18    | ✅ Done | SpreadMonitor, RegimeFilter, NotifierBus, `__init__` completat |
| S19    | 🔲 Next | Live integration test end-to-end, paper run 48h |
| S20    | 🔲 Next | Prometheus metrics endpoint, alerting rules |
| S21    | 🔲 Next | Web UI React dashboard (replace FastAPI Jinja) |

---

## Docker

```bash
# Build
make docker-build

# Paper trader
make docker-paper

# Dashboard
make docker-dashboard

# Live (necesita profil explicit)
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

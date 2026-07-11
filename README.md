# QuantLuna

> **Stat-arb trading system** — pairs trading cu Kalman filter, cointegration analysis, multi-exchange execution și risk management complet.

[![CI](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml/badge.svg)](https://github.com/Gzeu/quantluna/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Gzeu/quantluna/branch/main/graph/badge.svg)](https://codecov.io/gh/Gzeu/quantluna)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.15.0-green.svg)](CHANGELOG.md)

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
│                      AutoStrategySelector                   │
│                      VolatilityRegime                       │
│                      RegimeFilter    →    CircuitBreaker    │
│                      SpreadMonitor        PositionScanner   │
│                                          AdoptionEngine     │
│                                          ProfitOptimizer    │
│                                                             │
│  Risk / Monitoring                                          │
│  ─────────────────                                          │
│  CircuitBreaker  →  NotifierBus  →  Slack / Telegram /      │
│  HealthCheck        PnLReconciler    Discord                 │
│  WsWatchdog         Checkpoint                              │
│  AutoRebalancer     CorrelationFilter                       │
│  DrawdownController KellyPositionSizer                      │
│  DynamicStop        MultiPairAllocator                      │
│                                                             │
│  Backtest / Optimizer                                       │
│  ────────────────────                                       │
│  WalkForwardEngine   KalmanScoringWeights SearchSpace       │
│  MonteCarlo          coint_pvalue_series (rolling ADF)      │
│  Analytics           Optuna TPE optimizer (16 ks_* params)  │
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
│   ├── signal.py                 # SignalGenerator v4 — logica principala
│   ├── kalman_pairs_trading.py   # KalmanPairsTrading BaseStrategy wrapper (v4.2)
│   ├── auto_selector.py          # AutoStrategySelector — scorer + switcher
│   ├── optimizer.py              # Optuna optimizer + KalmanScoringWeights SearchSpace
│   ├── multi_strategy_engine.py  # Engine multi-strategie paralel
│   ├── multi_timeframe.py        # Confirmare MTF (LTF + HTF)
│   ├── regime_filter.py          # Gate unificat regim
│   ├── regime_detector.py        # Detectie trend vs mean-reversion
│   ├── pair_selector.py          # Selectie perechi cointegrate
│   ├── live_pair_scanner.py      # Scanner live perechi
│   ├── entry_filter.py           # Filtru intrare semnal
│   ├── signal_adapter.py         # Adaptor semnale legacy → BaseStrategy
│   ├── signal_combiner.py        # Combinator semnale multi-strategie
│   ├── bb_mean_reversion.py      # Bollinger Bands mean reversion
│   ├── zscore_momentum.py        # Z-score momentum strategy
│   ├── funding_arb.py            # Funding rate arbitrage
│   ├── stat_arb.py               # Statistical arbitrage clasic
│   ├── mean_reversion.py         # Mean reversion standalone
│   ├── momentum.py               # Momentum standalone
│   └── cointegration/            # Sub-modul cointegration dedicat
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
│   ├── kelly.py                  # Kelly criterion (full)
│   ├── kelly_sizer.py            # Kelly sizer wrapper
│   ├── dynamic_stop.py           # Stop dinamic ATR/vol-based
│   ├── portfolio_risk.py         # Risk management portofoliu
│   ├── position_sizer.py         # Position sizer generic
│   ├── position_sizer_factory.py # Factory position sizers
│   ├── auto_rebalancer.py        # Auto-rebalancer pozitii
│   ├── bybit_position_sizer.py   # Position sizer Bybit-specific
│   ├── correlation_filter.py     # Filtru corelatie
│   ├── correlation_matrix.py     # Matrice corelatie portofoliu
│   ├── dashboard_engine.py       # Engine risk dashboard
│   ├── drawdown_controller.py    # Controller drawdown
│   └── multi_pair_allocator.py   # Alocator multi-perechi
│
├── notifications/
│   ├── notifier_bus.py           # Fan-out bus notificari
│   ├── slack_notifier.py         # Notificari Slack
│   ├── telegram.py               # Notificari Telegram
│   └── discord.py                # Notificari Discord
│
├── backtest/
│   ├── engine.py                 # WalkForwardEngine + coint_pvalue_series (FIX-BT-7)
│   ├── engine_adapter.py         # Adaptor engine pentru strategii multiple
│   ├── auto_selector_runner.py   # Runner AutoSelector in backtest
│   ├── analytics.py              # Metrici performanta backtest
│   ├── monte_carlo.py            # Simulari Monte Carlo
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
├── tests/                        # 45+ fisiere de teste
│   ├── conftest.py
│   └── ...
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

### Walk-forward + Optuna (KalmanScoringWeights inclus)

```bash
# Optimizare completa cu 16 parametri ks_* in SearchSpace
python scripts/optimize_params.py --pair BTCUSDT ETHUSDT --trials 200

# Cu optimize_kalman_score=False pentru spatiu mai mic
python scripts/optimize_params.py --pair BTCUSDT ETHUSDT --trials 100 --no-ks
```

### Dashboard

```bash
uvicorn dashboard.server:app --reload --port 8000
# http://localhost:8000/docs
# sau
make docker-dashboard
```

---

## Optimizer — KalmanScoringWeights SearchSpace

Din `v0.15.0`, toti parametrii `KalmanScoringWeights` sunt inclusi in `SearchSpace` si pot fi optimizati via Optuna:

```python
from strategy.optimizer import QuantLunaOptimizer, OptimizerConfig

opt = QuantLunaOptimizer(OptimizerConfig(
    n_trials=200,
    optimize_kalman_score=True,   # activeaza cei 16 ks_* params
    objective="sharpe",
    seed=42,
))
best = opt.optimize(ohlcv_y, ohlcv_x)
best.save_json("best_params.json")
print(f"Sharpe test: {best.sharpe_test:.3f}")
```

Parametrii optimizati includ: `ks_baseline`, `ks_regime_*`, `ks_coint_p*`, `ks_hl_*`, `ks_autocorr_*`, `ks_vol_rank_*`, `ks_win_rate_*`.

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

### AutoStrategySelector — Scoring inteligent

```python
from strategy.auto_selector import AutoStrategySelector
from strategy.kalman_pairs_trading import KalmanPairsTrading, KalmanScoringWeights

# Scoring weights customizabile (sau optimizate via Optuna)
weights = KalmanScoringWeights(baseline=0.65, regime_ranging_bonus=0.18)
kalman = KalmanPairsTrading(spread_engine=engine, scoring_weights=weights)

selector = AutoStrategySelector(strategies=[kalman, bb, zscore_mom, funding_arb])

# generate_batch cu coint_pvalue_series float real
signals = selector.generate_batch(
    df=spread_df,
    coint_pvalue_series=coint_pvalue_series,  # pd.Series de float (ADF p-values)
    coint_valid_series=coint_valid_series,
)
```

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

---

## Roadmap

| Sprint | Status | Conținut |
|--------|--------|----------|
| S1–S8  | ✅ Done | Core Kalman filter, Spread calculator, SignalGenerator, Data fetchers (Bybit/Binance/OKX) |
| S9–S11 | ✅ Done | Cointegration (Engle-Granger + Johansen), Ornstein-Uhlenbeck half-life, `strategy/cointegration/` |
| S12–S15| ✅ Done | Backtest engine, Walk-forward validation, Optuna optimizer, Report builder HTML/JSON, Analytics, Monte Carlo |
| S16    | ✅ Done | OKX router, Multi-timeframe confirmare, VolatilityRegime, Dashboard API (FastAPI) |
| S17    | ✅ Done | OrderManager multi-exchange, CircuitBreaker auto-reset, Slack notifier, AdoptionEngine, ProfitOptimizer, Kelly sizer, DynamicStop |
| S18    | ✅ Done | SpreadMonitor real-time, RegimeFilter gatekeeper, NotifierBus fan-out, toate `__init__.py` completate |
| S19    | ✅ Done | AutoStrategySelector + scoring, KalmanScoringWeights SearchSpace (16 params Optuna), coint_pvalue_series rolling ADF (FIX-BT-7), MultiStrategyEngine, SignalAdapter/Combiner, EntryFilter, Gap #1–#3 |
| S20    | 🔲 Next | Prometheus `/metrics` endpoint, Grafana alerting rules, integrare FastAPI middleware |
| S21    | 🔲 Next | Web UI React dashboard — live PnL charts, strategy scores vizuale, replace FastAPI Jinja |
| S22    | 🔲 Next | End-to-end integration test suite, paper run automatizat 48h CI, smoke test live order flow (dry-run) |

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

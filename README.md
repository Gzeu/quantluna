# QuantLuna 🌙

> Adaptive Kalman Filter Pairs Trading Engine for Crypto Markets

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Prod--Ready-brightgreen)]()
[![Tests](https://img.shields.io/badge/Tests-264%2B-brightgreen)]()
[![Exchanges](https://img.shields.io/badge/Exchanges-Bybit%20%7C%20Binance-orange)]()
[![Strategy](https://img.shields.io/badge/Strategy-Stat%20Arb%20%2F%20Pairs-blueviolet)]()

QuantLuna is a **production-grade statistical arbitrage engine** built around a real-time Kalman Filter for dynamic hedge ratio estimation. Designed for crypto spot + perpetual futures markets on **Bybit** and **Binance**, with full portfolio-level risk management, live pair scanning, cointegration validation, adaptive signal v4 engine, orphan position adoption, and a monitoring dashboard.

---

## Table of Contents

- [Core Strategy](#core-strategy)
- [Architecture](#architecture)
- [Signal v4](#signal-v4)
- [Signal Flow](#signal-flow)
- [Startup Workflow (S19)](#startup-workflow-s19)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Key Parameters](#key-parameters)
- [Backtesting](#backtesting)
- [Live Trading](#live-trading)
- [Dashboard](#dashboard)
- [Risk Management](#risk-management)
- [Testing](#testing)
- [Fix Log](#fix-log)
- [Prod Checklist](#prod-checklist)
- [Roadmap](#roadmap)
- [Risk Warnings](#risk-warnings)
- [License](#license)

---

## Core Strategy

- **Pairs Trading** with cointegration validation (Engle-Granger + Johansen + residual diagnostics)
- **Kalman Filter** for adaptive, real-time hedge ratio (β) estimation — full P/K/state cycle per tick with tunable process noise Q and measurement noise R
- **Market-neutral** long/short structures on BTC, ETH, SOL, BNB and correlated assets
- **Funding-rate aware** — perpetual futures cost model via `FundingMonitor`, integrated into sizing via `MultiPairAllocator`
- **Walk-forward + Monte Carlo** backtesting for robust out-of-sample validation
- **Regime detection** — `RegimeDetector` (HMM/vol-based) + z-score stability gate before any entry
- **Live pair scanning** — `LivePairScanner` + `PairSelector` for automatic universe filtering and rotation
- **Portfolio-level risk** — `MultiPairAllocator`, `CorrelationMatrix` (Ledoit-Wolf), `DrawdownController`, `PortfolioRisk`
- **WebSocket health** — `WsWatchdog` with auto-reconnect, stale-feed detection, and entry gate
- **HALT logic** — queue overflow (100 consecutive drops) triggers system halt + external alert
- **Signal v4** — volatility-adjusted thresholds, delta-z momentum filter, dynamic cooldown, partial exit at z=0, cointegration validity gate
- **Startup orchestration (S19)** — `WorkflowOrchestrator` scans, reconciles and adopts orphan positions before `LiveTrader.run()`

---

## Architecture

```
quantluna/
├── core/
│   ├── kalman_filter.py          # KF state: predict + update, Q/R tuning, P matrix
│   ├── spread.py                 # Spread engine, hedge ratio application
│   └── cointegration.py          # Engle-Granger, Johansen, half-life estimator
│
├── strategy/
│   ├── signal.py                 # SignalGenerator v4 — z-score + P0/P1 adaptive features
│   ├── signal_adapter.py         # Adapter layer between signal engine and live trader
│   ├── regime.py                 # Regime filter, stability gate (lightweight)
│   ├── regime_detector.py        # RegimeDetector — HMM/vol-based regime classification
│   ├── pair_selector.py          # PairSelector — scoring, ranking, universe filtering
│   ├── live_pair_scanner.py      # LivePairScanner — async scanning, real-time pair rotation
│   └── cointegration/            # Extended cointegration submodule (Sprint 9)
│       ├── engle_granger.py
│       ├── johansen.py
│       ├── residuals.py
│       └── validator.py          # CointegrationValidator — unified pipeline
│
├── risk/
│   ├── kelly.py                  # KellyCrossPair, KellyConfig, KellyResult — Thorp f*
│   ├── position_sizer.py         # PositionSizer — unified sizing with DD scaling
│   ├── multi_pair_allocator.py   # PortfolioAllocator — 5-gate capital allocation
│   ├── portfolio_risk.py         # PortfolioRisk — var, beta-neutral checks
│   ├── correlation_matrix.py     # SpreadCorrelationMatrix — live rolling + Ledoit-Wolf
│   └── drawdown_controller.py    # DrawdownController — NORMAL → SOFT_LIMIT → HARD_STOP
│
├── execution/
│   ├── live_trader.py            # Main live engine — WebSocket feed, order execution
│   ├── paper_trader.py           # Paper trader — realistic fill simulation + slippage
│   ├── order_manager.py          # OrderManager — order lifecycle, fills, cancels
│   ├── funding_monitor.py        # FundingMonitor — real-time funding rate polling
│   ├── pnl_reconciler.py         # PnLReconciler — realized/unrealized PnL tracking
│   ├── checkpoint.py             # PositionCheckpoint — SQLite persistence
│   ├── resume_manager.py         # ResumeManager — checkpoint reconciliation on startup
│   ├── ws_watchdog.py            # WsWatchdog — WebSocket health, auto-reconnect
│   ├── circuit_breaker.py        # CircuitBreaker — exchange error handling
│   ├── rate_limiter.py           # RateLimiter — API rate limit enforcement
│   ├── health_check.py           # HealthCheck — pre-flight exchange connectivity
│   ├── backoff.py                # Exponential backoff for retries
│   ├── position_scanner.py       # PositionScanner — detect orphan/managed positions  [S19]
│   ├── adoption_engine.py        # AdoptionEngine — ADOPT / CLOSE_NOW / MONITOR_ONLY  [S19]
│   ├── profit_optimizer.py       # ProfitOptimizer — TP/SL/trailing/ladder for adopted pos [S19]
│   ├── workflow_orchestrator.py  # WorkflowOrchestrator — startup phases 1-4             [S19]
│   └── partial_exit_handler.py  # PartialExitHandler — Signal.PARTIAL_EXIT execution    [S19]
│
├── backtest/
│   ├── engine.py                 # Vectorised backtest, bar_freq support
│   ├── walk_forward.py           # Walk-forward, purged K-fold, non-leakage splits
│   ├── monte_carlo.py            # Monte Carlo simulation — path sampling, confidence bands
│   └── analytics.py             # Sharpe, Sortino, Calmar, max DD, win rate
│
├── data/
│   ├── loaders.py                # OHLCV loaders, CCXT wrappers
│   ├── market_data_cache.py      # Local OHLCV caching (SQLite / Parquet)
│   └── funding_fetcher.py        # Historical + live funding rate data
│
├── config/
│   ├── settings.py               # QuantLunaConfig — all runtime params (Pydantic)
│   ├── live_config.py            # LiveConfig dataclass
│   └── exec_config.py            # Exchange credentials, API config
│
├── dashboard/                    # Real-time monitoring dashboard (FastAPI + WebSocket)
│   ├── server.py
│   └── index.html
│
├── api/                          # REST API (FastAPI) — backtest jobs, compare, radar
│   ├── backtest.py               # /backtest, /status, /results, /compare endpoints
│   └── schemas.py                # Pydantic models: BacktestRequest, CompareResponse
│
├── scripts/
│   ├── run_backtest.py
│   ├── run_live.py               # v2 — cu WorkflowOrchestrator startup (S19)
│   ├── run_paper.py
│   ├── optimize_params.py        # Optuna hyperparameter tuning
│   ├── preflight_check.py        # Pre-flight connectivity check
│   └── scan_pairs.py             # Pair universe scanning
│
├── tests/                        # 27 test files, 264+ test cases
│   ├── conftest.py
│   ├── test_kalman.py / test_kalman_filter.py
│   ├── test_spread.py
│   ├── test_cointegration.py
│   ├── test_signal.py / test_signal_full.py
│   ├── test_signal_v4.py         # P0+P1 features: vol_adj, dz_filter, partial_exit  [NEW]
│   ├── test_regime.py
│   ├── test_pair_selector.py
│   ├── test_risk.py
│   ├── test_sprint10_allocator.py
│   ├── test_live_trader.py
│   ├── test_backtest.py / test_sprint15_backtest.py
│   ├── test_walk_forward.py
│   ├── test_sprint16_api.py / test_sprint18.py
│   ├── test_adoption_workflow.py # PositionScanner, AdoptionEngine, ProfitOptimizer [NEW]
│   ├── test_smoke_s15_s17.py
│   ├── test_health_check.py
│   ├── test_rate_limiter.py
│   ├── test_market_data_cache.py
│   ├── test_data.py
│   └── test_telegram_notifier.py
│
├── state_bus.py                  # Internal async event bus
├── .env.example                  # Environment variable template (updated S19)
├── Dockerfile / docker-compose.yml
├── CHANGELOG.md
├── CONTRIBUTING.md
├── PRODUCTION.md
└── WORKFLOW.md
```

---

## Signal v4

SignalGenerator v4 introduce 4 feature-uri adaptive peste core-ul Kalman/z-score:

### P0-1: Volatility-Adjusted Threshold

Threshold-ul de entry creste proportional cu percentila de volatilitate curenta, prevenind intrari in perioade de vol extrema:

```
effective_threshold = zscore_entry × (1 + vol_adj_factor × vol_rank)
                    ≤ zscore_entry × vol_adj_max_multiplier
```

| Env var | Default | Descriere |
|---------|---------|----------|
| `VOL_ADJ_ENABLED` | `true` | Activeaza feature |
| `VOL_ADJ_FACTOR` | `0.40` | Amplitudine ajustare (0=off, 0.6=agresiv) |
| `VOL_ADJ_LOOKBACK` | `100` | Bare pentru percentila vol |
| `VOL_ADJ_MAX_MULTIPLIER` | `1.6` | Cap threshold (max 1.6× baza) |

### P0-2: Delta-Z Momentum Filter

Blocheaza entry cand spread-ul inca accelereaza in directia z-score-ului — evita catching a falling knife:

```
blocat dacă: same_sign(dz_avg, z) AND |dz_avg| > dz_block_ratio × |z|
```

| Env var | Default | Descriere |
|---------|---------|----------|
| `DZ_FILTER_ENABLED` | `true` | Activeaza filter |
| `DZ_LOOKBACK` | `3` | Bare pentru derivata z |
| `DZ_BLOCK_RATIO` | `0.25` | Bloc daca |dz| > 25% din |z| |

### P1-1: Dynamic Cooldown

Cooldown-ul post-trade se adapteaza la half-life-ul curent al spread-ului:

```
cooldown = clamp(ceil(half_life × cooldown_hl_factor), cooldown_min, cooldown_max)
```

### P1-2: Partial Exit la z=0

La primul crossing al z=0 in timp ce suntem in trade, `PARTIAL_EXIT` inchide `partial_exit_pct`% din pozitie pe ambele legs via `reduceOnly` orders. Se executa **o singura data per trade**.

```python
# In LiveTrader._handle_signal:
from execution.partial_exit_handler import handle_partial_exit

if signal.signal == Signal.PARTIAL_EXIT:
    result = await handle_partial_exit(
        signal=signal, position=current_position,
        exchange=self._exchange, checkpoint=self._checkpoint,
        alert_cfg=self._alert_cfg,
    )
```

### P1-3: Cointegration Validity Gate

Inainte de fiecare entry, se verifica daca perechea este inca cointegrata (`coint_valid` flag din `CointegrationValidator`). Entry blocat cu `reason='stale_pair'` daca flag-ul este `False`. Pozitiile deja deschise nu sunt fortate la exit.

---

## Startup Workflow (S19)

La pornire, `run_live.py` v2 executa **4 faze** inainte de `LiveTrader.run()`:

```
Faza 1: PositionScanner.scan()
    └─> detecteaza pozitii ORPHAN (pe exchange, fara checkpoint local)
    └─> detecteaza pozitii MANAGED (exista in checkpoint)

Faza 2: ResumeManager.reconcile()
    └─> verifica divergente intre checkpoint si exchange
    └─> actualizeaza qty daca fill-uri au venit cat timp bot-ul era oprit

Faza 3: AdoptionEngine.process(orphans)
    ├─> ADOPT   — PnL > adopt_min_pnl_pct: salveaza in checkpoint + seteaza TP/SL
    ├─> CLOSE_NOW — PnL < close_loss_pct OR distanta_liq < min_liq_distance_pct
    └─> MONITOR_ONLY — notional prea mic sau conditii incerte

Faza 4: ProfitOptimizer.register(adopted)
    └─> porneste loop asyncio (background task) care monitorizeaza:
        ├─> TP/SL trigger → FULL_CLOSE
        ├─> Break-even move (dupa +be_trigger_pct)
        ├─> Profit ladder (inchidere partiala la praguri definite)
        └─> Trailing stop (dupa activare la +trailing_activation_pct)

Faza 5: LiveTrader.run()  ← trade normal pe perechea configurata
```

**HALT** — daca orchestratorul semnaleaza `should_halt=True` (ex: exchange down, pozitii critice), procesul se opreste cu `sys.exit(1)` inainte de a incepe trading.

```bash
# Pornire normala
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live

# Skip orphan scan (debug rapid)
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live --skip-orphan-scan

# Custom checkpoint path
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live --checkpoint /data/ql.db
```

---

## Signal Flow

```
WebSocket tick
    └─> WsWatchdog.ping()  [execution/ws_watchdog.py]
            └─> LiveSignalAdapter.on_tick()  [strategy/signal_adapter.py]
                    └─> SpreadEngine → Kalman update → hedge ratio → spread  [core/]
                            └─> Z-score calculation
                                    └─> SignalGenerator v4  [strategy/signal.py]
                                            ├─> vol_adj threshold gate (P0-1)
                                            ├─> delta-z momentum filter (P0-2)
                                            ├─> cointegration validity gate (P1-3)
                                            └─> RegimeDetector gate
                                                    └─> LiveTrader._evaluate_signal()  [execution/live_trader.py]
                                                            ├─> Signal.PARTIAL_EXIT → partial_exit_handler.py (P1-2)
                                                            ├─> WatchdogGate
                                                            ├─> DrawdownController
                                                            ├─> PortfolioAllocator.request_entry()  [risk/]
                                                            │       ├─> DD gate
                                                            │       ├─> Max concurrent pairs gate
                                                            │       ├─> Correlation gate
                                                            │       ├─> Kelly cross-pair sizing
                                                            │       └─> PortfolioRisk gate
                                                            ├─> FundingMonitor cost check
                                                            └─> OrderManager → exchange (CCXT)
                                                                        └─> PnLReconciler
                                                                                └─> StateBus → Dashboard /ws

Background tasks:
    ├─> _ws_feed()          — WebSocket consumer
    ├─> _consumer()         — tick processing loop
    ├─> _heartbeat()        — periodic status log
    ├─> _run_watchdog()     — WsWatchdog health
    ├─> _funding_task()     — FundingMonitor polling
    ├─> _pnl_task()         — PnLReconciler
    └─> optimizer_loop()    — ProfitOptimizer pentru pozitii adoptate (S19)
```

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/Gzeu/quantluna.git
cd quantluna
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Completeaza API keys, exchange, pair config
# Signal v4: VOL_ADJ_ENABLED, DZ_FILTER_ENABLED, PARTIAL_EXIT_ENABLED etc.
# S19 adoption: ADOPT_MIN_PNL_PCT, TP_TARGET_PCT, SL_MAX_LOSS_PCT etc.
```

### 3. Backtest

```bash
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --exchange binance --days 180
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --mode walk_forward --folds 5
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --mode monte_carlo --simulations 1000
```

### 4. Paper Trading

```bash
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode paper
```

### 5. Live Trading

```bash
# Startup complet: scan orphans → reconcile → adopt → trade
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live

# Skip orphan scan (prima pornire sau debug)
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live --skip-orphan-scan
```

### 6. Dashboard

```bash
uvicorn dashboard.server:app --host 0.0.0.0 --port 8000
open http://localhost:8000
```

### 7. API REST (backtest jobs)

```bash
uvicorn api.backtest:app --host 0.0.0.0 --port 8001
# POST /backtest   → porneste job async
# GET  /status/{id} → polling status
# GET  /results/{id} → rezultate complete
# POST /compare   → radar chart + diff matrix intre doua job-uri
```

---

## Configuration

### `.env` Variables (selectie)

```env
# Exchange
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret
EXCHANGE=bybit
TRADING_MODE=paper

# Signal v4
VOL_ADJ_ENABLED=true
VOL_ADJ_FACTOR=0.40
DZ_FILTER_ENABLED=true
PARTIAL_EXIT_ENABLED=true
PARTIAL_EXIT_PCT=0.50

# S19 — Adoption Engine
ADOPT_MIN_PNL_PCT=-0.02
CLOSE_LOSS_PCT=-0.05
TP_TARGET_PCT=0.04
SL_MAX_LOSS_PCT=0.03
TRAILING_ACTIVATION_PCT=0.02
TRAILING_DISTANCE_PCT=0.015
```

Vezi [`.env.example`](.env.example) pentru lista completa cu toate variabilele documentate.

### LiveConfig (programmatic)

```python
from config.settings import QuantLunaConfig, SignalConfig

cfg = QuantLunaConfig()
cfg.trading_mode = "live"
cfg.signal = SignalConfig(
    zscore_entry=2.0,
    zscore_exit=0.5,
    vol_adj_enabled=True,
    vol_adj_factor=0.40,
    dz_filter_enabled=True,
    partial_exit_enabled=True,
)
```

---

## Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `delta` | `1e-4` | Kalman process noise — higher = faster adaptation |
| `R` | `1e-2` | Kalman measurement noise — higher = smoother hedge ratio |
| `zscore_entry` | `2.0` | Z-score threshold for entry |
| `zscore_exit` | `0.5` | Z-score target for exit |
| `vol_adj_factor` | `0.40` | Vol-adj amplitudine (P0-1) |
| `vol_adj_max_multiplier` | `1.6` | Cap threshold la 1.6× (P0-1) |
| `dz_block_ratio` | `0.25` | Delta-z block threshold (P0-2) |
| `partial_exit_pct` | `0.50` | % pozitie inchis la z=0 (P1-2) |
| `cooldown_hl_factor` | `0.50` | Dynamic cooldown = half_life × factor (P1-1) |
| `half_life_min` | `12h` | Minimum acceptable half-life |
| `half_life_max` | `168h` | Maximum acceptable half-life |
| `min_warmup_bars` | `30` | Minimum bars before first entry |
| `vol_target` | `0.01` | Volatility target per trade (1%) |
| `kelly_fraction` | `0.25` | Fractional Kelly multiplier |
| `max_drawdown_pct` | `0.10` | Max drawdown before position scaling |
| `queue_overflow_halt` | `100` | Consecutive drops → HALT |
| `max_pairs_live` | `5` | Max concurrent active pairs |
| `corr_threshold` | `0.85` | Cross-pair correlation threshold |
| `adopt_min_pnl_pct` | `-0.02` | Min PnL pentru adoptie pozitie orfana |
| `close_loss_pct` | `-0.05` | Inchide automat daca PnL < -5% |
| `tp_target_pct` | `0.04` | Take-Profit pozitii adoptate |
| `sl_max_loss_pct` | `0.03` | Stop-Loss pozitii adoptate |

---

## Backtesting

### Standard Vectorised Backtest

```python
from backtest.engine import BacktestEngine
from backtest.analytics import BacktestAnalytics

engine = BacktestEngine(config)
results = engine.run(ohlcv_a, ohlcv_b, bar_freq="1h")

analytics = BacktestAnalytics(results)
print(analytics.sharpe())       # Sharpe ratio
print(analytics.sortino())      # Sortino ratio
print(analytics.calmar())       # Calmar ratio
print(analytics.max_drawdown())
print(analytics.win_rate())
```

### Walk-Forward

```python
from backtest.walk_forward import WalkForwardEngine

wf = WalkForwardEngine(config, n_folds=5, bar_freq="1h")
wf_results = wf.run(ohlcv_a, ohlcv_b)
```

### Monte Carlo

```python
from backtest.monte_carlo import MonteCarloEngine

mc = MonteCarloEngine(config)
mc_results = mc.run(ohlcv_a, ohlcv_b, n_simulations=1000,
                    confidence_levels=[0.05, 0.50, 0.95])
```

---

## Live Trading

### PortfolioAllocator — 5-Gate Entry Pipeline

```python
from risk import PortfolioAllocator, AllocatorConfig
from risk.kelly import KellyConfig
from risk.drawdown_controller import DDConfig

cfg = AllocatorConfig(
    capital_usd=10_000,
    max_concurrent_pairs=4,
    kelly=KellyConfig(kelly_fraction=0.25, vol_target=0.01),
    drawdown=DDConfig(
        pair_soft_dd=0.05,
        portfolio_soft_dd=0.08,
        portfolio_hard_dd=0.15,
    ),
)
allocator = PortfolioAllocator(cfg)

decision = allocator.request_entry(
    pair_id="ETH/BTC_perp",
    candidate_spread=spread_series,
    trade_pnl_history=oos_pnl_series,
    current_zscore=-2.3,
    entry_beta=0.0534,
)
if decision.allowed:
    notional = decision.notional_usd
```

### DrawdownController States

```
NORMAL ──(DD > 8%)──> SOFT_LIMIT ──(DD > 15%)──> HARD_STOP
       <──────────── manual_resume() ─────────────────────
```

HARD_STOP **nu se reseteaza automat**. Apeleaza `allocator.manual_resume()` dupa investigatie.

### WsWatchdog States

```
LIVE ──(no tick > timeout)──> STALE ──(reconnect ok)──> LIVE
                                   └──(max retries)──> DEAD
```

---

## Dashboard

```bash
uvicorn dashboard.server:app --host 0.0.0.0 --port 8000
```

**Disponibil la:** `http://localhost:8000`

**Afiseaza:**
- Perechi active + z-scores curente
- Pozitii deschise cu PnL nerealizat
- Drawdown level si state (NORMAL / SOFT_LIMIT / HARD_STOP)
- WsWatchdog state + last tick age
- Funding rates per pereche activa
- Trade history + PnL realizat
- Correlation matrix heatmap
- **Pozitii adoptate** (S19) + status optimizer (TP/SL/trailing)

---

## Risk Management

### Kelly Cross-Pair Sizing

Continuous Kelly (Thorp): \( f^* = \frac{E[R]}{E[R^2]} \)

- Fractional Kelly multiplier (default 0.25)
- Correlation discount din `SpreadCorrelationMatrix`
- Fallback la vol-target sizing cand sample < 20 trades sau E[R] ≤ 0
- Cap: `min(kelly_adj, vol_target, max_pair_cap, remaining_capital)`

### Cointegration Validation Pipeline

```
EngleGrangerTest  ──┐
JohansenTest      ──┼──> CointegrationValidator ──> accept / reject
ResidualDiagnostics─┘
```

Teste P1-3: re-validare la fiecare `COINT_RETEST_INTERVAL_HOURS` ore. Perechi cu `p-value > COINT_BLACKLIST_PVALUE` sunt blacklisted si nu mai primesc entry-uri noi.

---

## Testing

```bash
# Suite completa (264+ teste)
pytest tests/ -x --tb=short -q

# Pe module
pytest tests/test_kalman.py tests/test_spread.py -v
pytest tests/test_signal.py tests/test_signal_full.py tests/test_signal_v4.py -v
pytest tests/test_regime.py tests/test_pair_selector.py -v
pytest tests/test_risk.py tests/test_sprint10_allocator.py -v
pytest tests/test_live_trader.py -v
pytest tests/test_backtest.py tests/test_walk_forward.py tests/test_sprint15_backtest.py -v
pytest tests/test_sprint16_api.py tests/test_sprint18.py -v
pytest tests/test_adoption_workflow.py -v    # S19: scanner + engine + optimizer
pytest tests/test_smoke_s15_s17.py -v
```

**Cerinte suite verde:**
- Non-leakage: train/test windows nu se suprapun niciodata
- `bar_freq` respectat in engine (nu hardcoded `bars_per_day=24`)
- `MultiPairAllocator`: cross-pair capital allocation + correlation-aware sizing
- `LiveTrader`: 25+ cazuri — HALT paths, watchdog gate, close_all retry
- Signal v4: 32 teste dedicate (P0-1, P0-2, P1-1, P1-2, P1-3, backward compat)
- S19: 15+ teste pentru PositionScanner, AdoptionEngine, ProfitOptimizer

---

## Fix Log

| ID | File | Description |
|----|------|-------------|
| FIX-1 | `backtest/engine.py` | `bars_per_day` removed — replaced with `bar_freq` |
| FIX-2 | `backtest/walk_forward.py` | API rewritten, non-leakage splits, `bar_freq` propagated |
| FIX-3 | `execution/live_trader.py` | Queue overflow 100 drops → HALT + alert |
| FIX-4 | `execution/live_trader.py` | `close_all()` retry: 3 retries, 1s delay |
| FIX-5 | `execution/live_trader.py` | `FundingMonitor` credentials fallback |
| FIX-6 | `execution/live_trader.py` | `signal_gen.reset_kalman()` on reconnect |
| FIX-P1 | `execution/live_trader.py` | Spread buffer threshold: `10` → `min_warmup_bars` |
| FIX-S19-1 | `scripts/run_live.py` | Integrat `WorkflowOrchestrator` faze 1-4 la startup |
| FIX-S19-2 | `execution/partial_exit_handler.py` | Handler `Signal.PARTIAL_EXIT` — `reduceOnly` orders + checkpoint update |
| FIX-S19-3 | `execution/live_trader_sprint6_patch.py` | Marcat deprecated — 0 bytes, zero import risk |

---

## Prod Checklist

| Component | Status |
|-----------|--------|
| `core/kalman_filter.py` | ✅ Prod-ready |
| `core/spread.py` | ✅ Prod-ready |
| `core/cointegration.py` | ✅ Prod-ready |
| `strategy/signal.py` (v4) | ✅ Prod-ready — P0+P1 features active |
| `strategy/signal_adapter.py` | ✅ Prod-ready |
| `strategy/regime_detector.py` | ✅ Prod-ready |
| `strategy/pair_selector.py` | ✅ Prod-ready |
| `strategy/live_pair_scanner.py` | ✅ Prod-ready |
| `strategy/cointegration/` | ✅ Prod-ready |
| `risk/kelly.py` | ✅ Prod-ready |
| `risk/position_sizer.py` | ✅ Prod-ready |
| `risk/multi_pair_allocator.py` | ✅ Prod-ready |
| `risk/correlation_matrix.py` | ✅ Prod-ready |
| `risk/drawdown_controller.py` | ✅ Prod-ready |
| `risk/portfolio_risk.py` | ✅ Prod-ready |
| `execution/live_trader.py` | ✅ Prod-ready |
| `execution/paper_trader.py` | ✅ Prod-ready |
| `execution/order_manager.py` | ✅ Prod-ready |
| `execution/pnl_reconciler.py` | ✅ Prod-ready |
| `execution/checkpoint.py` | ✅ Prod-ready |
| `execution/resume_manager.py` | ✅ Prod-ready |
| `execution/ws_watchdog.py` | ✅ Prod-ready |
| `execution/circuit_breaker.py` | ✅ Prod-ready |
| `execution/health_check.py` | ✅ Prod-ready |
| `execution/position_scanner.py` | ✅ Prod-ready [S19] |
| `execution/adoption_engine.py` | ✅ Prod-ready [S19] |
| `execution/profit_optimizer.py` | ✅ Prod-ready [S19] |
| `execution/workflow_orchestrator.py` | ✅ Prod-ready [S19] |
| `execution/partial_exit_handler.py` | ✅ Prod-ready [S19] |
| `scripts/run_live.py` (v2) | ✅ Prod-ready — startup orchestration |
| `backtest/engine.py` | ✅ Prod-ready |
| `backtest/walk_forward.py` | ✅ Prod-ready |
| `backtest/monte_carlo.py` | ✅ Prod-ready |
| `backtest/analytics.py` | ✅ Prod-ready |
| `api/backtest.py` | ✅ Prod-ready |
| `dashboard/server.py` | ✅ Operational |
| `tests/` (27 files, 264+ cases) | ✅ Suite completa |
| `.env.example` | ✅ Actualizat — toate variabilele S19 |

---

## Roadmap

| Sprint | Feature | Status |
|--------|---------|--------|
| S19 | Signal v4 (P0+P1), AdoptionEngine, ProfitOptimizer, WorkflowOrchestrator | ✅ Livrat |
| S20 | Redis persistence pentru `_JOBS` in-memory din `api/backtest.py` | 🔜 Planned |
| S20 | Rate limiting pe `/compare` endpoint (max rows cap + streaming) | 🔜 Planned |
| S21 | CI/CD pipeline activ (GitHub Actions: lint + pytest + docker build) | 🔜 Planned |
| S21 | `__all__` exports in toate modulele publice + `pip-compile` version pins | 🔜 Planned |
| S22 | Compare UI in dashboard — radar chart + diff matrix vizualizare live | 🔜 Planned |

---

## Risk Warnings

⚠️ **Research software. Not financial advice.**

- Cointegration relationships break down — monitor regime shifts continuously
- Funding rates on perpetual futures can destroy P&L — cost is integrated into sizing but risk is not eliminated
- Real liquidity and slippage differ from backtest assumptions
- Cross-pair correlation can spike sharply during stress — `CorrelationMatrix` reduces sizing but does not prevent losses
- Never deploy without validated walk-forward results and out-of-sample tested parameters
- **Prima pornire live:** seteaza `min_warmup_bars=60`, capital la 10% din target — verifica din loguri ca buffer-ul se umple corect si Kelly returneaza sizing rezonabil
- **S19 adoption engine:** verifica manual pozitiile adoptate din loguri inainte de a lasa `ProfitOptimizer` sa opereze nesupravegheat

---

## License

MIT © 2026 George Pricop

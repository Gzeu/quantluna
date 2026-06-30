# QuantLuna 🌙

> Adaptive Kalman Filter Pairs Trading Engine for Crypto Markets

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Prod--Ready-brightgreen)]()
[![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen)]()
[![Exchanges](https://img.shields.io/badge/Exchanges-Bybit%20%7C%20Binance-orange)]()
[![Strategy](https://img.shields.io/badge/Strategy-Stat%20Arb%20%2F%20Pairs-blueviolet)]()

QuantLuna is a **production-grade statistical arbitrage engine** built around a real-time Kalman Filter for dynamic hedge ratio estimation. Designed for crypto spot + perpetual futures markets on **Bybit** and **Binance**, with full portfolio-level risk management, live pair scanning, cointegration validation, and a monitoring dashboard.

---

## Table of Contents

- [Core Strategy](#core-strategy)
- [Architecture](#architecture)
- [Signal Flow](#signal-flow)
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
│   ├── signal.py                 # LiveSignalAdapter, SpreadEngine, z-score via on_tick()
│   ├── signal_adapter.py         # Adapter layer between signal engine and live trader
│   ├── regime.py                 # Regime filter, stability gate (lightweight)
│   ├── regime_detector.py        # RegimeDetector — HMM/vol-based regime classification
│   ├── pair_selector.py          # PairSelector — scoring, ranking, universe filtering
│   ├── live_pair_scanner.py      # LivePairScanner — async scanning, real-time pair rotation
│   └── cointegration/            # Extended cointegration submodule (Sprint 9)
│       ├── engle_granger.py      # EngleGrangerTest
│       ├── johansen.py           # JohansenTest
│       ├── residuals.py          # ResidualDiagnostics
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
│   ├── order_manager.py          # OrderManager — order lifecycle, fills, cancels
│   ├── funding_monitor.py        # FundingMonitor — real-time funding rate polling
│   ├── pnl_reconciler.py         # PnLReconciler — realized/unrealized PnL tracking
│   ├── ws_watchdog.py            # WsWatchdog — WebSocket health, auto-reconnect
│   └── live_trader_sprint6_patch.py  # Deprecated — emptied, zero import risk
│
├── backtest/
│   ├── engine.py                 # Vectorised backtest, bar_freq support
│   ├── walk_forward.py           # Walk-forward, purged K-fold, non-leakage splits
│   ├── monte_carlo.py            # Monte Carlo simulation — path sampling, confidence bands
│   └── analytics.py              # Sharpe, Sortino, Calmar, max DD, win rate
│
├── data/
│   ├── loaders.py                # OHLCV loaders, CCXT wrappers
│   └── funding_fetcher.py        # Historical + live funding rate data
│
├── config/
│   ├── live_config.py            # LiveConfig dataclass — all runtime params
│   └── exec_config.py            # Exchange credentials, API config
│
├── dashboard/                    # Real-time monitoring dashboard (FastAPI + WebSocket)
│   ├── server.py                 # Dashboard server — /ws endpoint, snapshot broadcast
│   └── index.html                # Frontend — live metrics, positions, PnL
│
├── scripts/
│   ├── run_backtest.py           # CLI backtest runner
│   └── run_live.py               # CLI live/paper runner
│
├── tests/                        # 14 test files, 100+ test cases
│   ├── conftest.py
│   ├── test_kalman.py
│   ├── test_spread.py
│   ├── test_cointegration.py
│   ├── test_signal.py
│   ├── test_signal_full.py
│   ├── test_regime.py
│   ├── test_pair_selector.py
│   ├── test_risk.py
│   ├── test_sprint10_allocator.py
│   ├── test_live_trader.py
│   ├── test_backtest.py
│   ├── test_walk_forward.py
│   └── test_data.py
│
├── state_bus.py                  # Internal async event bus
├── .env.example                  # Environment variable template
├── pyproject.toml
└── requirements.txt
```

---

## Signal Flow

```
WebSocket tick
    └─> WsWatchdog.ping()  [execution/ws_watchdog.py]
            └─> LiveSignalAdapter.on_tick()  [strategy/signal_adapter.py]
                    └─> SpreadEngine → Kalman update → hedge ratio → spread  [core/]
                            └─> Z-score calculation
                                    └─> RegimeDetector gate  [strategy/regime_detector.py]
                                            └─> LiveTrader._evaluate_signal()  [execution/live_trader.py]
                                                    ├─> WatchdogGate (watchdog.state == LIVE)
                                                    ├─> DrawdownController level check
                                                    ├─> PortfolioAllocator.request_entry()  [risk/multi_pair_allocator.py]
                                                    │       ├─> DD level gate
                                                    │       ├─> Max concurrent pairs gate
                                                    │       ├─> Correlation gate (SpreadCorrelationMatrix)
                                                    │       ├─> Kelly cross-pair sizing  [risk/kelly.py]
                                                    │       └─> PortfolioRisk exposure gate
                                                    ├─> FundingMonitor cost check  [execution/funding_monitor.py]
                                                    └─> OrderManager → exchange (CCXT)  [execution/order_manager.py]
                                                                └─> PnLReconciler  [execution/pnl_reconciler.py]
                                                                        └─> StateBus broadcast  [state_bus.py]
                                                                                └─> Dashboard /ws  [dashboard/server.py]

Background tasks (asyncio.gather):
    ├─> _ws_feed()          — WebSocket consumer
    ├─> _consumer()         — tick processing loop
    ├─> _heartbeat()        — periodic status log
    ├─> _run_watchdog()     — WsWatchdog health monitor
    ├─> _funding_task()     — FundingMonitor polling
    └─> _pnl_task()         — PnLReconciler reconciliation

Parallel: LivePairScanner  [strategy/live_pair_scanner.py]
    └─> CointegrationValidator  [strategy/cointegration/validator.py]
            └─> PortfolioAllocator.request_entry() — candidate pair evaluation
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
# Fill in API keys, exchange, pair config
```

### 3. Backtest

```bash
# Standard backtest — BTC/ETH pair, 180 days
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --exchange binance --days 180

# Walk-forward with 5 folds
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --mode walk_forward --folds 5

# Monte Carlo simulation
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --mode monte_carlo --simulations 1000
```

### 4. Paper Trading (recommended before going live)

```bash
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode paper
```

### 5. Live Trading

```bash
# First run: use warmup mode — entry only after min_warmup_bars ticks
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live
```

### 6. Dashboard

```bash
# Start the monitoring dashboard (FastAPI + WebSocket)
uvicorn dashboard.server:app --host 0.0.0.0 --port 8000

# Open in browser
open http://localhost:8000
```

---

## Configuration

### `.env` Variables

```env
# Exchange credentials
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret

# Exchange selection: bybit | binance
EXCHANGE=bybit

# Trading mode: live | paper
MODE=paper

# Pair universe (comma-separated)
PAIRS=BTCUSDT,ETHUSDT

# Capital allocation
CAPITAL_USD=10000

# Risk parameters
MAX_DRAWDOWN_PCT=0.10
KELLY_FRACTION=0.25
VOL_TARGET=0.01
MAX_PAIRS_LIVE=5
```

### LiveConfig (programmatic)

```python
from config.live_config import LiveConfig
from config.exec_config import ExecConfig

config = LiveConfig(
    exchange="bybit",
    pairs=[("BTCUSDT", "ETHUSDT")],
    capital_usd=10_000,
    zscore_entry=2.0,
    zscore_exit=0.5,
    min_warmup_bars=30,
    kelly_fraction=0.25,
    vol_target=0.01,
    max_drawdown_pct=0.10,
    max_pairs_live=5,
    corr_threshold=0.85,
    delta=1e-4,   # Kalman process noise
    R=1e-2,       # Kalman measurement noise
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
| `half_life_min` | `12h` | Minimum acceptable half-life for mean reversion |
| `half_life_max` | `168h` | Maximum acceptable half-life |
| `min_warmup_bars` | `30` | Minimum bars in spread buffer before first entry |
| `vol_target` | `0.01` | Volatility target per trade (1%) |
| `kelly_fraction` | `0.25` | Fractional Kelly multiplier |
| `max_drawdown_pct` | `0.10` | Max drawdown before position scaling |
| `queue_overflow_halt` | `100` | Consecutive drops → HALT + external alert |
| `max_pairs_live` | `5` | Max concurrent active pairs (MultiPairAllocator) |
| `corr_threshold` | `0.85` | Cross-pair correlation threshold — pairs above reduce sizing |
| `pair_soft_dd` | `0.05` | Pair-level soft DD limit (5%) → force close |
| `portfolio_soft_dd` | `0.08` | Portfolio soft DD (8%) → SOFT_LIMIT state |
| `portfolio_hard_dd` | `0.15` | Portfolio hard DD (15%) → HARD_STOP state |
| `watchdog_timeout_s` | `30` | Seconds without tick before WsWatchdog marks feed STALE |

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
print(analytics.max_drawdown()) # Max drawdown
print(analytics.win_rate())     # Win rate
```

### Walk-Forward

```python
from backtest.walk_forward import WalkForwardEngine

wf = WalkForwardEngine(config, n_folds=5, bar_freq="1h")
wf_results = wf.run(ohlcv_a, ohlcv_b)
# Returns per-fold metrics + aggregate stats
# Non-leakage guaranteed: train/test windows never overlap
```

### Monte Carlo

```python
from backtest.monte_carlo import MonteCarloEngine

mc = MonteCarloEngine(config)
mc_results = mc.run(
    ohlcv_a, ohlcv_b,
    n_simulations=1000,
    confidence_levels=[0.05, 0.50, 0.95]
)
# Returns path distribution, confidence bands, ruin probability
```

---

## Live Trading

### PortfolioAllocator — 5-Gate Entry Pipeline

Every entry request passes through 5 sequential gates:

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

# On entry signal:
decision = allocator.request_entry(
    pair_id="ETH/BTC_perp",
    candidate_spread=spread_series,
    trade_pnl_history=oos_pnl_series,
    current_zscore=-2.3,
    entry_beta=0.0534,
)
if decision.allowed:
    notional = decision.notional_usd  # send order

# Per tick update:
snap = allocator.update_state(
    open_pnl_per_pair={"ETH/BTC_perp": 45.2},
    spread_updates={"ETH/BTC_perp": 0.0118},
)
if snap.level.value == "HARD_STOP":
    await live_trader.close_all("HARD_STOP")

# On exit:
allocator.record_exit("ETH/BTC_perp")
```

### DrawdownController States

```
NORMAL ──(portfolio DD > 8%)──> SOFT_LIMIT ──(portfolio DD > 15%)──> HARD_STOP
         <──────────────────── manual_resume() ────────────────────────────────
```

HARD_STOP **does not auto-reset**. Call `allocator.manual_resume()` explicitly after investigation.

### WsWatchdog States

```
LIVE ──(no tick > watchdog_timeout_s)──> STALE ──(reconnect success)──> LIVE
                                              └──(max retries exceeded)──> DEAD
```

Entry is blocked when watchdog state is not `LIVE`.

---

## Dashboard

The dashboard provides real-time monitoring via WebSocket broadcast from `StateBus`.

```bash
uvicorn dashboard.server:app --host 0.0.0.0 --port 8000
```

**Available at:** `http://localhost:8000`

**Displays:**
- Active pairs and their current z-scores
- Open positions with unrealized PnL
- Portfolio drawdown level and state (NORMAL / SOFT_LIMIT / HARD_STOP)
- WsWatchdog state and last tick age
- Funding rates per active pair
- Trade history and realized PnL
- Correlation matrix heatmap (cross-pair)

**WebSocket endpoint:** `ws://localhost:8000/ws` — subscribes to all `StateBus` events.

---

## Risk Management

### Kelly Cross-Pair Sizing

Continuous Kelly (Thorp): \( f^* = \frac{E[R]}{E[R^2]} \)

Applied with:
- Fractional Kelly multiplier (default 0.25)
- Correlation discount from `SpreadCorrelationMatrix`
- Fallback to vol-target sizing when sample < 20 trades or E[R] ≤ 0
- Portfolio cap: `min(kelly_adj, vol_target, max_pair_cap, remaining_capital)`

### Correlation Matrix

- Rolling buffer per pair (default 120 bars)
- Ledoit-Wolf shrinkage via scikit-learn (auto-fallback to numpy `corrcoef` if not installed)
- `check_new_pair()` — blocks candidate if |corr| > threshold with any active pair
- `diversification_discount()` — [0, 1] factor applied to Kelly sizing

### Cointegration Validation Pipeline (Sprint 9)

```
EngleGrangerTest  ──┐
JohansenTest      ──┼──> CointegrationValidator ──> accept / reject pair
ResidualDiagnostics─┘
```

All three tests must pass for a pair to be accepted into the live universe.

---

## Testing

```bash
# Run full test suite
pytest tests/ -x --tb=short -q

# Module groups
pytest tests/test_kalman.py tests/test_spread.py -v
pytest tests/test_signal.py tests/test_signal_full.py -v
pytest tests/test_regime.py tests/test_pair_selector.py -v
pytest tests/test_risk.py tests/test_sprint10_allocator.py -v
pytest tests/test_live_trader.py -v
pytest tests/test_backtest.py tests/test_walk_forward.py -v
pytest tests/test_cointegration.py tests/test_data.py -v
```

**Green suite requirements:**
- All non-leakage tests must pass — train/test windows must not overlap
- `bar_freq` must be respected in engine (not hardcoded `bars_per_day = 24`)
- Walk-forward fold count and split ratio validated against actual API
- `MultiPairAllocator` tests (Sprint 10) validate cross-pair capital allocation and correlation-aware sizing
- `LiveTrader` tests (25+ cases) cover HALT paths, watchdog gate, close_all retry logic

---

## Fix Log

| ID | File | Description |
|----|------|-------------|
| FIX-1 | `backtest/engine.py` | `bars_per_day` removed — replaced with configurable `bar_freq` |
| FIX-2 | `backtest/walk_forward.py` | API rewritten, non-leakage splits, `bar_freq` propagated |
| FIX-3 | `execution/live_trader.py` | Queue overflow 100 drops → HALT + external alert |
| FIX-4 | `execution/live_trader.py` | `close_all()` retry logic — 3 retries, 1s delay, alert on failure |
| FIX-5 | `execution/live_trader.py` | `FundingMonitor` credentials fallback to `exec_config` |
| FIX-6 | `execution/live_trader.py` | `signal_gen.reset_kalman()` on reconnect with fallback warning |
| FIX-P1 | `execution/live_trader.py` | Spread buffer threshold: `10` → `min_warmup_bars` — prevents Kelly oversizing at warmup |
| PATCH | `execution/live_trader_sprint6_patch.py` | Emptied — zero accidental import risk |
| TEST | `tests/test_backtest.py` | Full rewrite — smoke, metrics, non-leakage, bar_freq |
| TEST | `tests/test_walk_forward.py` | Full rewrite — new API, non-leakage, bar_freq |

---

## Prod Checklist

| Component | Status |
|-----------|--------|
| `core/kalman_filter.py` | ✅ Prod-ready |
| `core/spread.py` | ✅ Prod-ready |
| `core/cointegration.py` | ✅ Prod-ready |
| `strategy/signal.py` | ✅ Prod-ready |
| `strategy/signal_adapter.py` | ✅ Prod-ready |
| `strategy/regime_detector.py` | ✅ Prod-ready |
| `strategy/pair_selector.py` | ✅ Prod-ready |
| `strategy/live_pair_scanner.py` | ✅ Prod-ready |
| `strategy/cointegration/` (Sprint 9) | ✅ Prod-ready |
| `risk/kelly.py` | ✅ Prod-ready |
| `risk/position_sizer.py` | ✅ Prod-ready |
| `risk/multi_pair_allocator.py` | ✅ Prod-ready |
| `risk/correlation_matrix.py` | ✅ Prod-ready |
| `risk/drawdown_controller.py` | ✅ Prod-ready |
| `risk/portfolio_risk.py` | ✅ Prod-ready |
| `execution/live_trader.py` | ✅ Prod-ready (FIX-P1 applied) |
| `execution/order_manager.py` | ✅ Prod-ready |
| `execution/pnl_reconciler.py` | ✅ Prod-ready |
| `execution/ws_watchdog.py` | ✅ Prod-ready |
| `execution/live_trader_sprint6_patch.py` | ✅ Emptied |
| `backtest/engine.py` | ✅ Prod-ready |
| `backtest/walk_forward.py` | ✅ Prod-ready |
| `backtest/monte_carlo.py` | ✅ Prod-ready |
| `backtest/analytics.py` | ✅ Prod-ready |
| `dashboard/server.py` | ✅ Operational |
| `tests/` (14 files, 100+ cases) | ✅ Full suite |

---

## Roadmap

| Sprint | Feature | Status |
|--------|---------|--------|
| S11 | Telegram notifications — HALT alerts, trade entries/exits, daily PnL summary | 🔜 Planned |
| S11 | `execution/paper_trader.py` — dedicated paper trading with realistic fill simulation + slippage model | 🔜 Planned |
| S12 | `strategy/optimizer.py` — Optuna hyperparameter tuning for delta/R/zscore params | 🔜 Planned |
| S12 | `data/market_data_cache.py` — local OHLCV caching (SQLite / Parquet) | 🔜 Planned |
| S13 | `Dockerfile` + `docker-compose.yml` — containerized deployment | 🔜 Planned |
| S13 | `docs/` — extended docs: deployment guide, strategy math, ADRs | 🔜 Planned |

---

## Risk Warnings

⚠️ **Research software. Not financial advice.**

- Cointegration relationships break down — monitor regime shifts continuously
- Funding rates on perpetual futures can destroy P&L — cost is integrated into sizing but risk is not eliminated
- Real liquidity and slippage differ from backtest assumptions — validate on your specific exchange
- Cross-pair correlation can spike sharply during stress periods — `CorrelationMatrix` reduces sizing automatically but does not prevent losses
- Never deploy without validated walk-forward results and out-of-sample tested parameters
- **First live run:** set `min_warmup_bars=60`, capital at 10% of target — verify the buffer fills correctly and Kelly returns reasonable sizing from logs

---

## License

MIT © 2026 George Pricop

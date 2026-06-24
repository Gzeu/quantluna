# QuantLuna 🌙

> Adaptive Kalman Filter Pairs Trading Engine for Crypto Markets

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Prod--Ready-brightgreen)]()
[![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen)]()

QuantLuna is a **production-grade statistical arbitrage engine** built around a real-time Kalman Filter for dynamic hedge ratio estimation. Designed for crypto spot + perpetual futures markets on Bybit and Binance.

---

## Core Strategy

- **Pairs Trading** with cointegration validation (Engle-Granger + Johansen)
- **Kalman Filter** for adaptive, real-time hedge ratio (β) estimation — process noise Q, measurement noise R, full P/K/state cycle per tick
- **Market-neutral** long/short structures on BTC, ETH, SOL, BNB and correlated assets
- **Funding-rate aware** — perpetual futures cost model via `FundingMonitor`, delegated to `MultiPairAllocator`
- **Walk-forward + Monte Carlo** backtesting for robust out-of-sample validation
- **Regime detection** — `RegimeDetector` + spread buffer + z-score stability checks before entry
- **Live pair scanning** — `LivePairScanner` + `PairSelector` pentru universe filtering automat
- **Portfolio-level risk** — `MultiPairAllocator`, `CorrelationMatrix`, `DrawdownController`, `PortfolioRisk`
- **HALT logic** — queue overflow (100 consecutive drops) triggers system halt + external alert

---

## Architecture

```
quantluna/
├── core/
│   ├── kalman_filter.py        # KF state: predict + update, Q/R tuning, P matrix
│   ├── spread.py               # Spread engine, hedge ratio application
│   └── cointegration.py        # Engle-Granger, Johansen, half-life estimator
├── strategy/
│   ├── signal.py               # LiveSignalAdapter, SpreadEngine, z-score via on_tick()
│   ├── signal_adapter.py       # Adapter layer între signal engine și live trader
│   ├── regime.py               # Regime filter, stability gate (lightweight)
│   ├── regime_detector.py      # RegimeDetector complet — HMM/vol-based regime classification
│   ├── pair_selector.py        # PairSelector — scoring, ranking, universe filtering
│   ├── live_pair_scanner.py    # LivePairScanner — async scanning, pair rotation în timp real
│   └── cointegration/          # Submodul cointegration extins
├── risk/
│   ├── kelly.py                # Fractional Kelly, vol-target sizing
│   ├── position_sizer.py       # PositionSizer — sizing unificat cu DD scaling
│   ├── multi_pair_allocator.py # MultiPairAllocator — capital allocation cross-pair
│   ├── portfolio_risk.py       # PortfolioRisk — var, beta-neutral checks
│   ├── correlation_matrix.py   # CorrelationMatrix — live rolling correlation tracking
│   └── drawdown_controller.py  # DrawdownController — max DD enforcement, scaling logic
├── execution/
│   ├── live_trader.py          # Main live engine — WebSocket feed, order execution
│   ├── order_manager.py        # OrderManager — order lifecycle, fills, cancels
│   ├── funding_monitor.py      # FundingMonitor — real-time funding rate polling
│   ├── pnl_reconciler.py       # PnLReconciler — realized/unrealized PnL tracking
│   ├── ws_watchdog.py          # WsWatchdog — WebSocket health, auto-reconnect
│   └── live_trader_sprint6_patch.py  # GOLIT — zero risc de import accidental
├── backtest/
│   ├── engine.py               # Vectorised backtest, bar_freq support
│   ├── walk_forward.py         # Walk-forward, purged K-fold, non-leakage splits
│   └── analytics.py            # Sharpe, Sortino, Calmar, max DD, win rate
├── data/
│   ├── loaders.py              # OHLCV loaders, CCXT wrappers
│   └── funding_fetcher.py      # Historical + live funding rate data
├── config/
│   ├── live_config.py          # LiveConfig dataclass — all runtime params
│   └── exec_config.py          # Exchange credentials, API config
├── dashboard/                  # Optional monitoring dashboard
├── scripts/
│   ├── run_backtest.py         # CLI backtest runner
│   └── run_live.py             # CLI live/paper runner
├── tests/
│   ├── conftest.py             # Shared fixtures
│   ├── test_kalman.py          # Kalman Filter unit tests
│   ├── test_spread.py          # Spread engine tests
│   ├── test_cointegration.py   # Cointegration pipeline tests
│   ├── test_signal.py          # Signal adapter tests (smoke)
│   ├── test_signal_full.py     # Signal end-to-end tests
│   ├── test_regime.py          # Regime detector tests
│   ├── test_pair_selector.py   # PairSelector scoring tests
│   ├── test_risk.py            # Kelly, PositionSizer, DrawdownController tests
│   ├── test_sprint10_allocator.py  # MultiPairAllocator integration tests
│   ├── test_live_trader.py     # LiveTrader unit + integration tests
│   ├── test_backtest.py        # Backtest: smoke, metrics, non-leakage, bar_freq
│   ├── test_walk_forward.py    # Walk-forward: API, non-leakage, bar_freq
│   └── test_data.py            # Data loaders + funding fetcher tests
├── state_bus.py                # Internal event bus
├── .env.example                # Environment variable template
├── pyproject.toml
└── requirements.txt
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
# Completează API keys, exchange, pair config
```

### 3. Backtest

```bash
# Backtest pe perechea BTC/ETH, 180 zile
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --exchange binance --days 180

# Walk-forward cu 5 folds
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --mode walk_forward --folds 5
```

### 4. Paper Trading (recomandat înainte de live)

```bash
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode paper
```

### 5. Live Trading

```bash
# Pornire cu warmup obligatoriu — primul entry după min_warmup_bars ticks
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode live
```

---

## Local Testing

```bash
# Rulează toate testele
pytest tests/ -x --tb=short -q

# Module specifice
pytest tests/test_kalman.py tests/test_spread.py -v
pytest tests/test_signal.py tests/test_signal_full.py -v
pytest tests/test_regime.py tests/test_pair_selector.py -v
pytest tests/test_risk.py tests/test_sprint10_allocator.py -v
pytest tests/test_live_trader.py -v
pytest tests/test_backtest.py tests/test_walk_forward.py -v
```

**Cerințe pentru green suite:**
- Toate testele non-leakage trebuie să treacă — train/test windows nu se suprapun
- `bar_freq` trebuie respectat în engine (nu hardcodat `bars_per_day = 24`)
- Walk-forward fold count și split ratio validate cu API-ul actual
- `MultiPairAllocator` tests (sprint10) validează capital allocation cross-pair și correlation-aware sizing

---

## Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `delta` | `1e-4` | Kalman process noise — mai mare = adaptare mai rapidă |
| `R` | `1e-2` | Measurement noise — mai mare = hedge ratio mai smooth |
| `zscore_entry` | `2.0` | Prag z-score pentru entry |
| `zscore_exit` | `0.5` | Target z-score pentru exit |
| `half_life_min` | `12h` | Half-life minim acceptabil pentru mean reversion |
| `half_life_max` | `168h` | Half-life maxim acceptabil |
| `min_warmup_bars` | `30` | Bars minime în spread buffer înainte de primul entry |
| `vol_target` | `0.01` | Volatility target per trade (1% implicit) |
| `kelly_fraction` | `0.25` | Fractional Kelly multiplier |
| `max_drawdown_pct` | `0.10` | DD maxim înainte de position scaling |
| `queue_overflow_halt` | `100` | Drops consecutive → HALT + alert extern |
| `max_pairs_live` | `5` | Nr. maxim de perechi active simultan (MultiPairAllocator) |
| `corr_threshold` | `0.85` | Prag corelație cross-pair — perechi > threshold reduc sizing |

---

## Signal Flow

```
WebSocket tick
    └─> LiveSignalAdapter.on_tick()  [strategy/signal_adapter.py]
            └─> SpreadEngine (Kalman update → hedge ratio → spread)  [core/]
                    └─> Z-score calculation
                            └─> RegimeDetector gate  [strategy/regime_detector.py]
                                    └─> LiveTrader._evaluate_signal()  [execution/live_trader.py]
                                            ├─> Kelly sizing (spread_buffer ≥ min_warmup_bars)  [risk/kelly.py]
                                            ├─> PositionSizer + DrawdownController  [risk/]
                                            ├─> MultiPairAllocator — cross-pair capital check  [risk/multi_pair_allocator.py]
                                            ├─> FundingMonitor cost check  [execution/funding_monitor.py]
                                            └─> OrderManager → exchange (CCXT)  [execution/order_manager.py]
                                                        └─> PnLReconciler  [execution/pnl_reconciler.py]
```

---

## Fix Log

Toate fix-urile aplicate înainte de prod:

| ID | Fișier | Descriere |
|----|--------|-----------|
| FIX-1 | `backtest/engine.py` | `bars_per_day` eliminat — înlocuit cu `bar_freq` configurabil |
| FIX-2 | `backtest/walk_forward.py` | API rescris, non-leakage splits, `bar_freq` propagat |
| FIX-3 | `execution/live_trader.py` | Queue overflow 100 drops → HALT + alert extern |
| FIX-4 | `execution/live_trader.py` | `close_all()` retry logic — 3 retries, 1s delay, alert la eșec |
| FIX-5 | `execution/live_trader.py` | `FundingMonitor` credentials fallback pe `exec_config` |
| FIX-6 | `execution/live_trader.py` | `signal_gen.reset_kalman()` la reconnect cu fallback warning |
| FIX-P1 | `execution/live_trader.py` | Spread buffer threshold: `10` → `min_warmup_bars` — previne Kelly oversizing la warmup |
| PATCH | `execution/live_trader_sprint6_patch.py` | Golit complet — zero risc de import accidental |
| TEST | `tests/test_backtest.py` | Rescris complet — smoke, metrics, non-leakage, bar_freq |
| TEST | `tests/test_walk_forward.py` | Rescris complet — API nou, non-leakage, bar_freq |

---

## Prod Checklist

| Componentă | Status |
|------------|--------|
| `core/kalman_filter.py` | ✅ Prod-ready |
| `core/spread.py` | ✅ Prod-ready |
| `core/cointegration.py` | ✅ Prod-ready |
| `strategy/signal.py` | ✅ Prod-ready |
| `strategy/signal_adapter.py` | ✅ Prod-ready |
| `strategy/regime_detector.py` | ✅ Prod-ready |
| `strategy/pair_selector.py` | ✅ Prod-ready |
| `strategy/live_pair_scanner.py` | ✅ Prod-ready |
| `risk/kelly.py` | ✅ Prod-ready |
| `risk/position_sizer.py` | ✅ Prod-ready |
| `risk/multi_pair_allocator.py` | ✅ Prod-ready |
| `risk/correlation_matrix.py` | ✅ Prod-ready |
| `risk/drawdown_controller.py` | ✅ Prod-ready |
| `risk/portfolio_risk.py` | ✅ Prod-ready |
| `execution/live_trader.py` | ✅ Prod-ready (FIX-P1 aplicat) |
| `execution/order_manager.py` | ✅ Prod-ready |
| `execution/pnl_reconciler.py` | ✅ Prod-ready |
| `execution/ws_watchdog.py` | ✅ Prod-ready |
| `execution/live_trader_sprint6_patch.py` | ✅ Golit |
| `backtest/engine.py` | ✅ Prod-ready |
| `backtest/walk_forward.py` | ✅ Prod-ready |
| `tests/` (14 fișiere) | ✅ Suite completă |

---

## Risk Warnings

⚠️ **Research software. Not financial advice.**

- Cointegration relationships break down — monitorizează permanent regime shifts
- Funding rates pe perpetual futures pot distruge P&L — costul este integrat în sizing dar nu elimină riscul
- Lichiditate și slippage reale diferă de presupunerile din backtest — validează pe exchange-ul tău specific
- Corelația cross-pair poate crește brusc în perioadele de stress — `CorrelationMatrix` reduce sizing automat dar nu previne loss-urile
- Nu deployi niciodată fără walk-forward validat și parametri testați out-of-sample
- Primul run live: `min_warmup_bars=60`, capital la 10% din target — validează că buffer-ul se umple corect și Kelly returnează sizing rezonabil din log

---

## License

MIT © 2026 George Pricop

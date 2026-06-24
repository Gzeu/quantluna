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
- **Funding-rate aware** — perpetual futures cost model via `FundingMonitor`, delegated to `PortfolioAllocator`
- **Walk-forward + Monte Carlo** backtesting for robust out-of-sample validation
- **Regime detection** — spread buffer + z-score stability checks before entry
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
│   └── regime.py               # Regime filter, stability gate
├── risk/
│   ├── kelly.py                # Fractional Kelly, vol-target sizing
│   ├── portfolio.py            # PortfolioAllocator, funding-adjusted sizing
│   └── drawdown.py             # Max DD control, position scaling
├── execution/
│   ├── live_trader.py          # Main live engine — WebSocket feed, order execution
│   └── funding_monitor.py      # FundingMonitor — real-time funding rate polling
├── backtest/
│   ├── engine.py               # Vectorised backtest, bar_freq support
│   ├── walk_forward.py         # Walk-forward, purged K-fold, non-leakage splits
│   └── analytics.py           # Sharpe, Sortino, Calmar, max DD, win rate
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
│   ├── test_backtest.py        # Smoke, metrics, non-leakage, bar_freq tests
│   └── test_walk_forward.py    # Walk-forward: API, non-leakage, bar_freq
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

# Doar backtest suite
pytest tests/test_backtest.py -v

# Doar walk-forward suite
pytest tests/test_walk_forward.py -v
```

**Cerințe pentru green suite:**
- Toate testele non-leakage trebuie să treacă — train/test windows nu se suprapun
- `bar_freq` trebuie respectat în engine (nu hardcodat `bars_per_day = 24`)
- Walk-forward fold count și split ratio validate cu API-ul actual

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

---

## Signal Flow

```
WebSocket tick
    └─> LiveSignalAdapter.on_tick()
            └─> SpreadEngine (Kalman update → hedge ratio → spread)
                    └─> Z-score calculation
                            └─> Regime gate
                                    └─> LiveTrader._evaluate_signal()
                                            ├─> Kelly sizing (spread_buffer ≥ min_warmup_bars)
                                            ├─> FundingMonitor cost check
                                            └─> Order execution (CCXT)
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
| `strategy/signal.py` | ✅ Prod-ready |
| `backtest/engine.py` | ✅ Prod-ready |
| `backtest/walk_forward.py` | ✅ Prod-ready |
| `execution/live_trader.py` | ✅ Prod-ready (FIX-P1 aplicat) |
| `execution/live_trader_sprint6_patch.py` | ✅ Golit |
| `tests/test_backtest.py` | ✅ Rescris |
| `tests/test_walk_forward.py` | ✅ Rescris |

---

## Risk Warnings

⚠️ **Research software. Not financial advice.**

- Cointegration relationships break down — monitorizează permanent regime shifts
- Funding rates pe perpetual futures pot distruge P&L — costul este integrat în sizing dar nu elimină riscul
- Lichiditate și slippage reale diferă de presupunerile din backtest — validează pe exchange-ul tău specific
- Nu deployi niciodată fără walk-forward validat și parametri testați out-of-sample
- Primul run live: `min_warmup_bars=60`, capital la 10% din target — validează că buffer-ul se umple corect și Kelly returnează sizing rezonabil din log

---

## License

MIT © 2026 George Pricop

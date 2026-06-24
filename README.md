# QuantLuna 🌙

> Adaptive Kalman Filter Pairs Trading Engine for Crypto Markets

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen)]()

QuantLuna is a **production-grade statistical arbitrage engine** built around a real-time Kalman Filter for dynamic hedge ratio estimation. Designed for crypto spot + perpetual futures markets.

## Core Strategy

- **Pairs Trading** with cointegration validation (Engle-Granger + Johansen)
- **Kalman Filter** for adaptive, real-time hedge ratio (beta) estimation
- **Market-neutral** long/short structures on BTC, ETH, SOL, BNB and correlated assets
- **Funding-rate aware** — perpetual futures cost model built in
- **Walk-forward + Monte Carlo** backtesting for robust validation

## Architecture

```
quantluna/
├── core/                   # Kalman Filter, cointegration, spread engine
├── strategy/               # Signal generation, z-score, regime detection
├── risk/                   # Position sizing, Kelly, drawdown control
├── execution/              # CCXT live trading, WebSocket feed
├── backtest/               # Walk-forward, Monte Carlo, analytics
├── data/                   # Data loaders, funding rate fetcher
├── config/                 # Strategy parameters, exchange config
├── scripts/                # CLI runners
└── tests/                  # Unit + integration tests
```

## Quick Start

```bash
git clone https://github.com/Gzeu/quantluna.git
cd quantluna
pip install -r requirements.txt

# Run backtest on BTC/ETH pair
python scripts/run_backtest.py --pair BTCUSDT ETHUSDT --exchange binance --days 180

# Live paper trading
python scripts/run_live.py --pair BTCUSDT ETHUSDT --mode paper
```

## Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `delta` | `1e-4` | Kalman process noise — higher = faster adaptation |
| `R` | `1e-2` | Measurement noise — higher = smoother hedge ratio |
| `zscore_entry` | `2.0` | Z-score threshold for entry |
| `zscore_exit` | `0.5` | Z-score target for exit |
| `half_life_min` | `12h` | Minimum acceptable mean reversion half-life |
| `half_life_max` | `168h` | Maximum acceptable mean reversion half-life |

## Risk Warnings

⚠️ **This is experimental research software. Not financial advice.**
- Cointegration relationships break down. Always monitor regime shifts.
- Crypto funding rates can destroy P&L on perpetual futures.
- Liquidity and slippage assumptions are critical — validate for your exchange.
- Never deploy without walk-forward validated, out-of-sample tested parameters.

## License

MIT © 2026 George Pricop

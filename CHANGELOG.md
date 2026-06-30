# QuantLuna — Changelog

## Sprint 12 — 2026-07-01

### Added

**`strategy/optimizer.py` — Optuna Hyperparameter Optimizer**

- `QuantLunaOptimizer` cu TPE sampler (Tree-structured Parzen Estimator)
- `SearchSpace` dataclass — bounds configurabile pentru toți parametrii
- `OptimizerConfig` — n_trials, n_jobs, objective, train_ratio, seed, storage, pruning
- `OptimizationResult` — best params + metrici train + out-of-sample (Sharpe, Sortino, Calmar, win rate, max DD, profit factor)
- Walk-forward aware: split strict 70/30 fără leakage
- MedianPruner — taie trial-uri slabe devreme
- Obiective suportate: `sharpe`, `sortino`, `calmar`, `profit_factor`
- Parametrii optimizați: `delta`, `R`, `zscore_entry`, `zscore_exit`, `kelly_fraction`, `vol_target`, `half_life_min_h`, `half_life_max_h`, `min_warmup_bars`
- Constraints automate: `zscore_exit < zscore_entry`, `half_life_min < half_life_max`
- Optuna SQLite storage — resume study după întrerupere
- `result.save_json()` + `result.print_summary()`

**`data/market_data_cache.py` — Local Parquet Cache**

- `MarketDataCache` — download OHLCV via CCXT + cache local Parquet (Apache Arrow, snappy)
- `load()` — load din cache sau download dacă lipsă/stale
- `refresh()` — incremental merge (descarcă doar barele noi de la ultima bară cached)
- `exists()`, `info()`, `list_cached()`, `clear()`
- Cache dir: `~/.quantluna/cache/<exchange>/<symbol>/<timeframe>.parquet`
- Stale detection configurabilă (default: 4h)
- Deduplicare automată și sortare cronologică
- Pagination automată la CCXT (descarcă tot istoricul cu limit=1000 per request)

**`execution/rate_limiter.py` — Async Token Bucket Rate Limiter**

- `RateLimiter` cu bucket-uri separate per endpoint type: `order`, `query`, `market`
- Limite default per exchange: Bybit (10/20/50 rps), Binance (10/20/20 rps)
- `burst_factor` configurabil — permite burst scurt
- Warning log la wait > `warn_wait_s`
- `acquire()`, `acquire_order()`, `acquire_market()`, `acquire_query()` shortcuts

**`execution/health_check.py` — Pre-flight Health Check**

- `HealthCheck` cu 7 checks: ccxt import, API credentials, exchange connectivity, symbols tradeable, account balance, config constraints, cache freshness
- `HealthReport` cu `all_passed`, `critical_failures`, `print_report()` Rich table
- `critical=False` pentru checks non-blocante (balance, cache)
- `CheckResult` per check cu pass/fail/message/critical

**`scripts/optimize_params.py` — CLI Optimizer Runner**

- Full CLI cu argparse: pair, exchange, timeframe, days, trials, jobs, objective, train-ratio, seed, storage, output
- Auto-load din MarketDataCache
- Index alignment automat pe barele comune
- Print LiveConfig patch după optimizare

**`scripts/run_paper.py` — CLI Paper Trading Dedicated Runner**

- CLI cu argparse: pair, exchange, capital, slippage, latency, warmup, zscore params
- `--params best_params.json` — încarcă automat parametrii din optimizer
- `--health-check` flag — rulează HealthCheck înainte de start
- Telegram integration din CLI flags
- Summary la oprire (Ctrl+C)

**`.github/workflows/ci.yml` — GitHub Actions CI**

- Test suite pe Python 3.10, 3.11, 3.12 (matrix)
- Coverage upload la Codecov (Python 3.11)
- Ruff lint check
- TruffleHog secret scan pe fiecare push/PR

**`Dockerfile` + `docker-compose.yml` — Containerizare**

- Multi-stage build (builder + runtime)
- Non-root user pentru securitate
- 4 services: `paper`, `live` (profile), `optimize` (profile), `dashboard`
- Volume persistent pentru data și cache
- Live service cu `restart: "no"` — restart manual intentionat pentru siguranță

---

## Sprint 11 — 2026-07-01

### Added

**`notifications/telegram_notifier.py`**

- `TelegramNotifier` — async, non-blocking, fail-safe
- `send_trade_entry()`, `send_trade_exit()`, `send_halt()`, `send_daily_summary()`
- `send_watchdog_alert()`, `send_queue_overflow()`, `send_custom()`
- Rate limiting (1 msg/s), retry exponential backoff (3x)
- `NotifierConfig` cu filtre granulare per tip de alert
- `AlertLevel` enum cu emoji per severitate

**`execution/paper_trader.py`**

- `PaperTrader` — drop-in replacement pentru LiveTrader
- WebSocket feed real (Bybit + Binance), fills simulate
- Slippage model: `ask + slippage_pct/2 * mid` pentru buy, invers pentru sell
- Fee model per exchange: Bybit taker 0.055%, Binance taker 0.04%
- Latency simulation (asyncio.sleep)
- Același PortfolioAllocator pipeline ca LiveTrader
- SQLite persistence (`paper_trades.db`)
- `summary()` dict, `send_daily_summary()` via Telegram

**`CONTRIBUTING.md`**

- Getting started, project structure table, development workflow
- Code standards: PEP 8, type hints, async, logging levels, error handling
- Testing requirements: mock APIs, no leakage, pytest-asyncio
- Commit message format + PR checklist

---

## fix(integration) — 2026-06-24

### Fixed — Integration Commit (Sprint 4-10 Consolidation)

**`execution/live_trader.py` — rescris complet**

- `PortfolioRisk.record_trade()` lipsea — adăugat în `risk/portfolio_risk.py`
- `PortfolioAllocator` Sprint 10 neintegrat — integrat complet
- `close_all(reason)` — metodă async nouă pentru HARD_STOP / PAIR_DD / urgenta externă
- Sprint 6 patch (`live_trader_sprint6_patch.py`) aplicat direct în cod
- `live_trader_sprint6_patch.py` — marcat deprecat cu `ImportError` explicit

**`execution/live_trader.py` — WsWatchdog integrat (Sprint 7)**

- `WsWatchdog` importat și creat în `__init__`
- `watchdog.ping()` apelat în `_consumer()` la fiecare tick
- Gate entry: `watchdog_gate_entries=True` blochează `_open_position()` când `watchdog.state != LIVE`

---

## Sprint 10 — 2026-06-24

### Added

- `risk/correlation_matrix.py` — `SpreadCorrelationMatrix` cu Ledoit-Wolf shrinkage
- `risk/kelly.py` — `KellyCrossPair` + `KellyConfig` + `KellyResult`
- `risk/drawdown_controller.py` — `DrawdownController` NORMAL → SOFT_LIMIT → HARD_STOP
- `risk/multi_pair_allocator.py` — `PortfolioAllocator` cu 5 gates secvențiale

---

## Sprint 9 — 2026-06-24

### Added
- `strategy/cointegration/` — pachet complet: EngleGrangerTest, JohansenTest, ResidualDiagnostics, CointegrationValidator

---

## Sprint 8 — 2026-06-24

### Fixed
- `dashboard/server.py` — `bus.snapshot_dict()` fix

### Added
- `backtest/monte_carlo.py`, `strategy/live_pair_scanner.py`

---

## Sprint 7 — 2026-06-24

### Added
- `execution/ws_watchdog.py`, `backtest/engine.py`

### Changed
- `state_bus.py` patch Sprint 6 + 7

---

## Sprint 6 — 2026-06-24

### Added
- `execution/funding_monitor.py`, `pnl_reconciler.py`
- `strategy/signal_adapter.py`

---

## Sprint 5 — 2026-06-24

### Added
- `state_bus.py`, `dashboard/server.py`, `dashboard/index.html`

---

## Sprint 4 — anterioare

- Kalman Filter adaptive hedge ratio (SpreadEngine)
- SignalGenerator v3, PortfolioRisk, PositionSizer
- Backtesting walk-forward engine

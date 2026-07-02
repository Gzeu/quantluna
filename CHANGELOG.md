# QuantLuna — CHANGELOG

All notable changes to QuantLuna are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Sprint 16] — 2026-07-02

### Added
- **`strategy/multi_timeframe.py`** — Multi-Timeframe Signal Confirmation engine
  - `MTFConfig`: configurabil cu `htf_resample`, `htf_zscore_min`, `htf_neutral_band`, `require_htf_alignment`, `hysteresis_bars`
  - `MultiTimeframeConfirmation.build_htf_zscore(ltf_df)`: resample LTF spread la HTF și calculează rolling z-score
  - `confirm(ltf_zscore, htf_zscore)`: confirmă sau blochează entry pe baza alinierii LTF vs HTF
  - `confirm_from_df(...)`: pipeline complet cu lookup automat al ultimului HTF bar valid
  - `batch_confirm(df)`: vectorizat pentru backtesting — returnează `pd.Series[bool]`
  - Logică neutral band: dacă HTF z-score ∈ ±neutral_band → no opinion → PASS
  - Previne intrări counter-trend pe semnale LTF zgomotoase

- **`core/volatility_regime.py`** — Volatility Regime Classifier
  - `RegimeLabel` enum: `LOW | NORMAL | HIGH | EXTREME`
  - `VolRegimeConfig`: lookback, percentile thresholds (low/high/extreme), size multipliers per regime, hysteresis, block_entry_regime
  - `VolatilityRegime.update(spread_return)`: online update cu rolling percentile rank
  - `update_from_prices(y, x, beta, prev_spread)`: convenience wrapper direct din prețuri
  - Properties: `current_regime`, `size_multiplier`, `entry_allowed`, `percentile`
  - `as_dict()`: snapshot serializabil pentru dashboard/logs
  - Hysteresis: N bare consecutive în noul regim înainte de switch (previne flickering)
  - `EXTREME` regime → `size_multiplier=0.0` → `entry_allowed=False` → no new trades

- **`execution/okx_order_router.py`** — OKX Exchange Router (al 3-lea venue după Bybit + Binance)
  - `OKXConfig`: api_key, api_secret, passphrase (OKX-specific), testnet, instrument_type, leverage, margin_mode
  - `OKXOrderRouter`: async CCXT-based, context manager (`async with`)
  - `connect()` / `close()`: lifecycle management cu sandbox mode pentru testnet
  - `place_market_order()` / `place_limit_order()`: cu reduce_only, post_only, client_order_id
  - `cancel_order()` / `cancel_all_orders()` / `get_open_orders()`
  - `get_positions()` / `get_balance()` / `get_ticker()` / `get_orderbook()`
  - `get_funding_rate()`: funding rate pentru perpetual SWAP
  - `set_leverage(symbol, leverage, margin_mode)`: setare leverage + margin mode
  - `open_pair()` / `close_pair()`: plasare simultană a ambelor picioare cu `asyncio.gather`
  - `_retry()`: exponential backoff pe rate-limit errors (3 retries default)

- **`tests/test_sprint16_enhancements.py`** — 20 teste Sprint 16
  - `TestMTFConfig`: import, defaults
  - `TestMTFConfirmation`: neutral band pass, aligned pass, misaligned block, HTF below min block, alignment disabled, build_htf_zscore, batch_confirm bool series, missing column raises, confirm_from_df
  - `TestVolRegimeConfig`: import, defaults
  - `TestVolatilityRegime`: starts normal, insufficient data, extreme vol triggers extreme, low vol triggers low, size multiplier decreases, extreme blocks entry, normal allows entry, percentile range, as_dict keys, reset clears state, update_from_prices, hysteresis delays switch
  - `TestOKXOrderRouterImport`: import, config defaults, not connected raises, ccxt unavailable raises

---

## [Sprint 15] — 2026-07-01

### Added
- **`backtest/engine_adapter.py`** — Bridge layer: `StrategyConfig` → `BacktestConfig` + `WalkForwardEngine`
  - `strategy_to_backtest_config()`: maps all `StrategyConfig` fields to `BacktestConfig`, including `bar_freq` string → `bar_freq_hours` float, `fee_rate`, `slippage_pct`
  - `BacktestEngine(cfg)`: public API accepting `StrategyConfig` directly; supports `run(y, x)`, `run(df=...)`, `run(data_dir=...)`; graceful fallback when full engine unavailable
  - `WalkForwardRunner(cfg)`: wraps `WalkForwardValidator` with `StrategyConfig`; propagates `purge_bars` + `embargo_bars` explicitly
  - `_MinimalSpreadEngine`: fallback spread engine using `KalmanHedgeRatio` directly (no `core.spread` dependency needed in testing)
- **`backtest/__init__.py`** — Exports `BacktestEngine`, `WalkForwardRunner` as top-level public names
- **`tests/test_sprint15_backtest.py`** — 25 teste covering adapter, purging gap, engine, walk-forward runner

### Changed / Fixed
- **Purging gap anti-lookahead** fully documented with explicit rationale:
  - `purge_bars` ≥ `warm_up_bars`: eliminates Kalman state contamination at IS/OOS boundary
  - `embargo_bars` ≥ estimated half-life: prevents spread mean-reversion echo (IS entry closing in OOS)
  - `BacktestEngine` defaults: `purge_bars=cfg.warm_up_bars`, `embargo_bars=24` (1 day @ 1h bars)
- **`scripts/optimize_params.py`**: `make_objective()` now imports `BacktestEngine` via `from backtest.engine_adapter import BacktestEngine` (Sprint 14 gap fixed)
- **`scripts/run_backtest.py`**: uses `BacktestEngine` from `backtest.engine_adapter` (not stale import)

---

## [Sprint 14] — 2026-07-01

### Added
- **`requirements.txt`** — completat cu `optuna>=3.5.0`, `fastapi>=0.110.0`, `uvicorn[standard]>=0.27.0`, `httpx>=0.27.0`, `pydantic>=2.6.0`, `pydantic-settings>=2.2.0`, `kaleido>=0.2.1`, `ruff>=0.4.0`; restructurat pe secțiuni cu comentarii
- **`config/cointegration_config.py`** — `CointegrationConfig` dataclass cu toți parametrii testelor de cointegration: `adf_alpha`, `johansen_signif`, `min/max_half_life_h`, `require_both_tests`; preseturi `conservative()`, `liberal()`, `from_env()`; `__post_init__` validation
- **`config/strategy_config.py`** — `StrategyConfig` master dataclass: agregă toți parametrii sistemului (Kalman, Z-score, Risk, Execution, Capital); `from_optimizer_json(path)`, `from_env()`, `to_dict()`, `summary()`
- **`config/__init__.py`** — exports `CointegrationConfig`, `StrategyConfig`
- **`dashboard/index.html`** — tab **Optimizer** complet: trial values scatter + running best chart (Plotly), parameter importances bar chart (fANOVA), best params table cu inline importance bars, top trials table
- **`tests/test_cointegration.py`** — 14 teste: `CointegrationConfig` validation, `EngleGrangerTest` cu config parametrizabil, `StrategyConfig` constraints
- **`.github/workflows/ci.yml`** — upgraded: ruff lint job, test matrix Python 3.10/3.11/3.12, `--cov-fail-under=60`, Codecov upload, docker-build job pe `main`
- **`pyproject.toml`** — `[tool.ruff]` config complet: reguli `E,W,F,I,UP,B,C4,SIM`; `[tool.coverage.run]`; `[project.scripts]` entry point
- **`.env.example`** — completat cu toate variabilele Sprint 14: `QUANTLUNA_COINT_*`, `OPTUNA_*`, `DASHBOARD_*`, `QUANTLUNA_HALF_LIFE_*`
- **`scripts/optimize_params.py`** — CLI Optuna complet: `--sampler` (tpe/random/cmaes), `--pruner` (median/hyperband/none), `--jobs`, `--dry-run`, `--export-best`; `_synthetic_sharpe()` fallback pentru CI
- **`scripts/run_backtest.py`** — `StrategyConfig.from_optimizer_json()` integration, CLI overrides, `--walk-forward`
- **`tests/conftest.py`** — rescris complet: fixture `rng` (session-scoped), `sample_prices`, `cointegrated`, `strategy_config`, `coint_config`, `mock_ccxt`, `mock_ws`, `sample_trades`

### Fixed
- `tests/conftest.py`: fixture `rng` lipsă — cauza crash CI pe `test_cointegration.py`

---

## [Sprint 13] — 2026-07-01

### Added
- **`dashboard/server.py`** — `GET /api/optimize/results`: Optuna trial history, `n_trials`, `n_complete`, `n_pruned`, `best_value`, `best_params`, `param_importances` (fANOVA ≥10 trials), top-N trials sorted by objective; `GET /api/health`; `WebSocket /ws/live` cu `_WSManager`
- **`tests/test_dashboard_api.py`** — 5 teste: `/api/status`, `/api/health`, `/api/optimize/results` null storage + mock Optuna study
- **`tests/test_kalman_filter.py`** — 17 teste: update mechanics, warmup, Joseph form PD, `fit()` stateful bug, warmup guard, delta setter, reset, history deque bounded
- **`tests/test_analytics.py`** — 7 teste: keys complete, max_dd negativ, win_rate bounds, no trades, finite values
- **`tests/test_rate_limiter.py`** — 5 teste async: token bucket, bybit/binance limits, burst, endpoint fallback
- **`tests/test_health_check.py`** — 7 teste: `all_passed`, critical failures, API key checks, mock asyncio
- **`tests/test_market_data_cache.py`** — 7 teste: cache miss/hit, metadata, deduplication, symbol normalization
- **`tests/test_telegram_notifier.py`** — 5 teste async: disabled notifier, network error fail-safe, entry/halt format
- **`tests/conftest.py`** — fixtures Sprint 13: mock CCXT, mock WebSocket, `sample_trades`
- **`pytest.ini`** — `asyncio_mode = auto`
- **`main.py`** — entry point CLI: subcomands `paper`, `optimize`, `health`, `dashboard`; argparse + ASCII banner
- **`core/state_bus.py`** — `state_bus.py` mutat din root cu `DeprecationWarning` shim la root
- **`core/__init__.py`** — creat

### Fixed
- **`core/kalman_filter.py`** — `fit()` stateful bug: `self.reset()` adăugat la începutul `fit()` — al doilea apel pe acelați obiect producea rezultate diferite
- **`core/kalman_filter.py`** — warmup guard: `WARNING` loggat când `len(y) < self.warm_up`

---

## [Sprint 12] — (anterior)

### Added
- Optuna hyperparameter optimization engine
- Walk-forward validation cu Monte Carlo bootstrap
- `backtest/engine.py`: `WalkForwardEngine` cu purged K-fold, anti-lookahead z-score (FIX-BT-1), `bar_freq_hours` configurabil (FIX-BT-2)
- `backtest/walk_forward.py`: `WalkForwardValidator` cu overfit detection (OOS < 50% IS Sharpe)
- `backtest/monte_carlo.py`: bootstrap empiric fără distribuție parametrică

### Fixed
- FIX-BT-1: z-score OOS era normalizat pe statistici OOS (look-ahead bias). Rezolvat: mean/std fixate din IS tail
- FIX-BT-2: `bars_per_day` hardcodat la 24. Rezolvat: calculat din `bar_freq_hours`

---

## [Sprint 9–11] — (anterior)

### Added
- `strategy/cointegration/engle_granger.py`: Engle-Granger test complet cu half-life AR(1)
- `strategy/cointegration/johansen.py`: Johansen test cu eigenvalue + trace statistics
- `strategy/cointegration/residual_diagnostics.py`: Ljung-Box, Hurst, Jarque-Bera
- `strategy/cointegration/validator.py`: `CointegrationValidator` agregator
- `core/kalman_filter.py`: Sprint 9 — Joseph form covariance update, x=0 guard, `deque(maxlen=10_000)`, atomic property setters
- `risk/`: Kelly sizing, vol-target, per-pair DD limits, portfolio hard stop
- `notifications/telegram.py`: async Telegram notifier cu fail-safe
- `execution/rate_limiter.py`: token bucket rate limiter (Bybit/Binance limits)

---

## [Sprint 1–8] — (anterior)

### Added
- Initial project structure: `core/`, `strategy/`, `execution/`, `risk/`, `backtest/`, `notifications/`
- `core/kalman_filter.py`: KalmanHedgeRatio initial implementation
- `core/spread.py`: SpreadEngine
- `strategy/signal.py`: SignalGenerator cu z-score thresholds
- `backtest/analytics.py`: PerformanceAnalytics
- Dockerfile, docker-compose.yml
- CI/CD initial workflow
- README.md complet

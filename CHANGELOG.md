# Changelog

All notable changes to QuantLuna are documented in this file.

Format: [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`

---

## [1.0.0-rc] — 2026-07-11

### Fixed — `bybit_live_runner.py` v3.3 · Sprint S20 review fixes

> Toate fix-urile de mai jos sunt prezente în fișierul
> `execution/bybit_live_runner.py`, clasa `BybitLiveRunner`.

- **FIX-1 [CRITIC]** `CircuitBreaker` — apeluri statice înlocuite cu apeluri pe instanță.
  `record_failure()` / `record_success()` / `.state` apelate pe obiectul `circuit_breaker`
  pasat explicit în `_execute_action()`. Nu mai există risc de state global.

- **FIX-2 [CRITIC]** Dual-leg partial fill — helper `_send_legs()` cu `try/except` per leg.
  Dacă `leg_x` eșuează după ce `leg_y` a reușit: se trimite emergency market-close pe `leg_y`
  (best-effort), se apelează `circuit_breaker.record_failure()` și se trimite alertă `critical`
  prin `NotifierBus`.

- **FIX-3 [CRITIC]** `FundingMonitor` singleton — nu mai este recreat la fiecare bar.
  `self._funding_monitor` inițializat o singură dată în `_build_components()` și reutilizat în
  `_check_funding_gate()`. Gate returnează `True` (deschis) dacă monitorul nu a putut fi inițializat.

- **FIX-4 [IMPORTANT]** `is_warmed_up` fallback `False` — anterior `True` pornea trading
  fără warm-up la orice refactor al `SpreadMonitor`.
  `getattr(spread_monitor, 'is_warmed_up', False)` în `_run_loop()`.

- **FIX-5 [IMPORTANT]** `price_x == 0` guard — bar malformat la restart WS cauzau
  `ZeroDivisionError` la calculul `x_qty = base_qty * price_y / price_x`.
  Guard prezent în `_run_loop()` (bar ignorat cu `continue`) și în `_execute_action()` (return
  imediat cu log `error`).

- **FIX-6 [IMPORTANT]** Import watchdog corectat: `execution.watchdog` → `execution.ws_watchdog`.
  Fișierul real este `ws_watchdog.py`.

- **FIX-7 [MINOR]** `BybitLiveRunnerConfig` — toate câmpurile folosesc
  `field(default_factory=lambda: os.getenv(...))` în loc de `os.getenv()` evaluat la import-time.
  Garantează citirea corectă a variabilelor de mediu la fiecare instanțiere.

### Added — Sprint 20 · Infrastructură

- `tests/test_sprint20.py` — 22 teste de regresie FIX-1..5:
  `TestCircuitBreakerFix1` (9 teste), `TestDualLegPartialFillFix2` (3),
  `TestFundingMonitorSingletonFix3` (5), `TestIsWarmedUpFallbackFix4` (3),
  `TestPriceXZeroGuardFix5` (4).

- `.github/workflows/ci.yml` — `mypy || true` înlocuit cu threshold 20 erori activ.
  Script bash extrage `ERROR_COUNT` din `--error-summary` și blochează CI dacă depășește pragul.
  Plan reducere: `v1.1.0 → 10 | v1.2.0 → 5 | v2.0.0 → strict`.

- `mypy.ini` — configurație mypy cu `ignore_missing_imports = True` global +
  suprimări explicite pentru: pybit, ccxt, aiohttp, loguru, statsmodels, scipy,
  sklearn, ta, redis, prometheus_client.

- `docs/ORPHAN_AUDIT.md` — audit fișiere orfane `execution/` cu decizie per fișier
  (`ACTIVE_SECONDARY` / `LEGACY` / `CANDIDATE_DELETE`) și plan de execuție v1.1.0.

- CI: Smoke test v3.3 în job `docker` — validează FIX-1 (instanțe independente),
  FIX-3 (singleton pattern), FIX-4 (`getattr` fallback), FIX-5 (`inspect.getsource` guard).

- `deploy.yml` — health check post-deploy cu rollback automat la tag anterior confirmat prezent.

- `state_bus.py` root — `DeprecationWarning(stacklevel=2)` confirmat prezent din Sprint 13.

---

## [0.15.0] — 2026-07-11

### Added — Sprint 19 / Fix-BT-7 / Optimizer SearchSpace

- `strategy/optimizer.py` — `SearchSpace` extins cu 16 câmpuri `ks_*` pentru toți parametrii din `KalmanScoringWeights`; `OptimizerConfig.optimize_kalman_score: bool = True` toggle; `_objective()` sugerează parametrii `ks_*` via Optuna cu constraint `p001 > p005 > 0 > p010`; `_run_backtest()` construiește `KalmanScoringWeights` din params și îl transmite în `BacktestConfig`.
- `backtest/engine.py` — `BacktestConfig` extins cu `coint_pvalue_window`, `coint_retest_interval_bars`, `kalman_scoring_weights`; helper static `_build_coint_pvalue_series()` pentru rolling ADF float p-values cu carry-forward fără lookahead; seria `coint_pvalue` calculată și atașată per bar în fold-urile IS și OOS (FIX-BT-7).
- `strategy/kalman_pairs_trading.py` — `generate_batch()` acceptă acum `coint_pvalue_series: Optional[pd.Series]`; valorile sunt atașate coloanei `coint_pvalue` în DataFrame-ul rezultat și disponibile downstream pentru `MarketContext` și `score()` (Fix #7 / Gap #3). Versiunea bumped la `4.2`.
- `strategy/auto_selector.py` — `generate_batch()` transmite `coint_pvalue_series` la `KalmanPairsTrading.generate_batch()` via introspectie `inspect.signature`; `MarketContext.coint_pvalue` primeşte `float` real per bar (nu `bool`) din `coint_p_arr` (Fix #7 / Gap #1). Wiring complet end-to-end: engine → fold_df["coint_pvalue"] → AutoStrategySelector → MarketContext → score().

### Fixed
- FIX-BT-7: `coint_pvalue` era hardcodat la `0.05` în toate apelurile `generate_batch()` — acum este calculat rolling (ADF) per bar cu carry-forward între retestări la interval configurabil.
- Gap #1: `MarketContext.coint_pvalue` primea `bool` (din `coint_valid`) în loc de `float` — corectat în `auto_selector.py`.
- Gap #2: `kalman_scoring_weights` din `BacktestConfig` acum construit și transmis corect la `KalmanPairsTrading` prin optimizer.
- Gap #3: `generate_batch()` în `KalmanPairsTrading` nu accepta `coint_pvalue_series` — parametru adăugat, valorile propagate în coloana `coint_pvalue`.

---

## [Unreleased → integrat în 1.0.0-rc]

### Added
- `Makefile` — comenzi dev unificate: `make test`, `make lint`, `make paper`, `make docker-build` etc.
- `.env.example` — template complet cu toate variabilele documentate
- `.pre-commit-config.yaml` — hooks: trailing-whitespace, ruff, mypy, detect-private-key, no-commit-to-main
- `.dockerignore` — exclude data/, state/, logs/, .env, __pycache__ din build context
- CI: Python 3.10 adăugat în matrix (era declarat în pyproject.toml dar nu testat)
- CI: Codecov upload cu `codecov-action@v4`
- CI: Job `typecheck` cu mypy pe `core/`, `risk/`, `execution/`, `strategy/`
- CI: Job `lint` cu ruff check + ruff format --check
- `Dockerfile`: HEALTHCHECK, `/app/state` directory, `APP_VERSION` build-arg, OCI labels, non-root user
- `docker-compose.yml`: healthchecks pe toate serviciile, `state/` volume, `stop_grace_period: 30s` pentru live, `x-common` YAML anchor, serviciu `backtest`, `DASHBOARD_PORT` env configurabil
- `requirements.txt`: `loguru`, `mypy`, `ruff`, `types-requests`, `coverage[toml]` adăugate explicit
- `README.md`: badges CI/codecov/ruff/version, Quick Start section, Docker section, Contributing section, roadmap extins S20/S21

### Fixed
- `README.md`: comenzile CLI actualizate să folosească noul format `main.py paper/live/backtest` (vechile `--mode` flags)
- `README.md`: structura proiectului actualizată cu toate modulele din `risk/` (auto_rebalancer, bybit_position_sizer, correlation_filter, correlation_matrix etc.)

---

## [0.14.0] — 2026-07

### Added — Sprint 31
- `plotly`, `kaleido`, `weasyprint` pentru charting si rapoarte PDF
- `risk/auto_rebalancer.py` — auto-rebalance pozitii
- `risk/bybit_position_sizer.py` — position sizer Bybit-specific
- `risk/correlation_filter.py` + `correlation_matrix.py`
- `risk/dashboard_engine.py` — risk dashboard engine
- `risk/drawdown_controller.py` — drawdown controller
- `risk/multi_pair_allocator.py` — alocator multi-perechi

---

## [0.13.0] — Sprint 18

### Added
- `core/spread_monitor.py` — SpreadMonitor real-time
- `strategy/regime_filter.py` — RegimeFilter gatekeeper central
- `notifications/notifier_bus.py` — NotifierBus fan-out
- Toate `__init__.py` completate cu exports publici

---

## [0.12.0] — Sprint 17

### Added
- `execution/order_manager.py` — OrderManager multi-exchange lifecycle
- `risk/circuit_breaker.py` — CircuitBreaker cu auto-reset
- `notifications/slack_notifier.py` — Slack integration
- `execution/adoption_engine.py` — AdoptionEngine (ADOPT/CLOSE_NOW/MONITOR)
- `execution/profit_optimizer.py` — ProfitOptimizer (TP/SL/trailing/break-even)

---

## [0.11.0] — Sprint 16

### Added
- `execution/okx_order_router.py` — OKX Router
- `strategy/multi_timeframe.py` — confirmare multi-timeframe
- `core/volatility_regime.py` — regim volatilitate
- `api/dashboard_api.py` — FastAPI REST dashboard

---

## [0.10.0] — Sprint 14–15

### Added
- `backtest/walk_forward_optimizer.py` — Optuna walk-forward
- `backtest/report_builder.py` — rapoarte HTML/JSON
- Walk-forward validation cu purge gap

---

## [0.9.0] — Sprint 12–13

### Added
- `backtest/engine.py` — backtest vectorizat
- `backtest/walk_forward.py` — walk-forward validation

---

## [0.8.0] — Sprint 9–11

### Added
- `core/cointegration.py` — Engle-Granger + Johansen
- `core/half_life.py` — Ornstein-Uhlenbeck half-life

---

## [0.1.0] — Sprint 1–8

### Added
- Core Kalman filter, spread calculator, signal generator
- Data fetchers: Bybit, Binance
- Paper trading engine
- Telegram notifications

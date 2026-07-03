# Changelog

All notable changes to QuantLuna are documented in this file.

Format: [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`

---

## [Unreleased]

### Added
- `Makefile` — comenzi dev unificte: `make test`, `make lint`, `make paper`, `make docker-build` etc.
- `CHANGELOG.md` — fisier change log
- `.env.example` — template complet cu toate variabilele documentate
- `.pre-commit-config.yaml` — hooks: trailing-whitespace, ruff, mypy, detect-private-key, no-commit-to-main
- `.dockerignore` — exclude data/, state/, logs/, .env, __pycache__ din build context
- CI: Python 3.10 adaugat in matrix (era declarat in pyproject.toml dar nu testat)
- CI: Codecov upload cu `codecov-action@v4`
- CI: Job `typecheck` cu mypy pe `core/`, `risk/`, `execution/`, `strategy/`
- CI: Job `lint` cu ruff check + ruff format --check
- `Dockerfile`: HEALTHCHECK, `/app/state` directory, `APP_VERSION` build-arg, OCI labels, non-root user
- `docker-compose.yml`: healthchecks pe toate serviciile, `state/` volume, `stop_grace_period: 30s` pentru live, `x-common` YAML anchor, serviciu `backtest`, `DASHBOARD_PORT` env configurabil
- `requirements.txt`: `loguru`, `mypy`, `ruff`, `types-requests`, `coverage[toml]` adaugate explicit
- `README.md`: badges CI/codecov/ruff/version, Quick Start section, Docker section, Contributing section, roadmap extins S20/S21

### Fixed
- `README.md`: comenzile CLI actualizate sa foloseasca noul format `main.py paper/live/backtest` (vechile `--mode` flags)
- `README.md`: structura proiectului actualizata cu toate modulele din `risk/` (auto_rebalancer, bybit_position_sizer, correlation_filter, correlation_matrix etc.)

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

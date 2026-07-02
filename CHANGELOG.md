# Changelog

All notable changes to QuantLuna are documented here.
Format: [Sprint X] — Component — Description

---

## [Sprint 19] — 2026-07-02

### Added
- `execution/integration_loop.py` — end-to-end async loop: Kalman → SpreadMonitor → RegimeFilter → OrderManager → NotifierBus
- `scripts/paper_run_harness.py` — CLI harness pentru paper run cu date sintetice OU
- `tests/test_sprint19.py` — 10 teste IntegrationLoop (import, dry-run, CB block, entry+exit, notifier, spread block)
- `.github/workflows/ci.yml` — GitHub Actions CI: pytest pe Python 3.11 + 3.12, coverage >= 60%
- `.github/workflows/lint.yml` — Ruff linter automat la fiecare push

---

## [Sprint 18] — 2026-07-02

### Added
- `core/spread_monitor.py` — monitor real-time spread: DRIFT, HALFLIFE_SLOW, KALMAN_DIVERGENCE, STUCK_POSITION, COINTEGRATION_BREAK
- `strategy/regime_filter.py` — gate unificat: CB + VolRegime + MTF + SpreadMonitor → GateResult cu size_multiplier
- `notifications/notifier_bus.py` — fan-out async bus: Slack + Telegram simultan, fail_silent, disable runtime
- `strategy/__init__.py` — creat (lipsea complet)
- `tests/test_sprint18.py` — 25 teste (SpreadMonitor x10, RegimeFilter x9, NotifierBus x6)
- `tests/test_smoke_s18.py` — 4 smoke tests integrare S18

### Updated
- `execution/__init__.py` — exporta acum OrderManager, PositionScanner, AdoptionEngine, ProfitOptimizer + dataclasi
- `notifications/__init__.py` — adaugat SlackNotifier, NotifierBus
- `risk/__init__.py` — adaugat CircuitBreaker, CircuitBreakerConfig, TripReason, TripEvent
- `README.md` — rescris complet: arhitectura, structura, instalare, exemple cod, roadmap

---

## [Sprint 17] — 2026-07-01

### Added
- `execution/order_manager.py` — lifecycle comenzi multi-exchange cu background monitor + timeout auto-cancel
- `execution/position_scanner.py` — scan pozitii exchange vs checkpoint: MANAGED / ORPHAN
- `execution/adoption_engine.py` — decizie ADOPT / CLOSE_NOW / MONITOR_ONLY pentru pozitii orfane
- `execution/profit_optimizer.py` — TP/SL, break-even, profit ladder, trailing stop pentru pozitii adoptate
- `risk/circuit_breaker.py` — circuit breaker cu auto-reset: consecutive losses, drawdown %, error rate comenzi
- `notifications/slack_notifier.py` — Slack via Incoming Webhook sau Bot Token, min_level filtering
- `tests/test_sprint17.py` — 22 teste (OrderManager x7, CircuitBreaker x9, SlackNotifier x6)

---

## [Sprint 16] — 2026-06-28

### Added
- `execution/okx_order_router.py` — OKX futures router complet
- `strategy/multi_timeframe.py` — confirmare MTF LTF+HTF
- `core/volatility_regime.py` — regim volatilitate cu size_multiplier
- `api/dashboard_api.py` — REST API FastAPI pentru dashboard
- `tests/test_sprint16_api.py`, `test_sprint16_enhancements.py`

---

## [Sprint 15] — 2026-06-25

### Added
- `backtest/walk_forward_optimizer.py` — Optuna walk-forward optimization
- `backtest/report_builder.py` — rapoarte HTML/JSON backtest
- `tests/test_sprint15_backtest.py` — 14 teste

---

## [Sprint 12-14] — 2026-06-20

### Added
- `backtest/engine.py` — engine backtest vectorizat cu costuri tranzactie
- `backtest/walk_forward.py` — walk-forward validation
- Optuna integration pentru hyperparameter search

---

## [Sprint 9-11] — 2026-06-10

### Added
- `core/cointegration.py` — Engle-Granger + Johansen cointegration tests
- `core/half_life.py` — Ornstein-Uhlenbeck half-life estimation
- `execution/multi_pair_manager.py` — management multi-perechi simultan

---

## [Sprint 1-8] — 2026-05-15

### Added
- `core/kalman_filter.py` — Kalman filter dinamic hedge ratio
- `core/spread_calculator.py` — spread computation + z-score rolling
- `strategy/signal_generator.py` — semnale intrare/iesire
- `execution/bybit_order_router.py`, `binance_order_router.py`
- `execution/paper_trader.py`, `paper_engine.py`
- `data/fetcher.py`, `historical_fetcher.py`, `store.py`
- `execution/health_check.py`, `checkpoint.py`, `pnl_reconciler.py`
- Setup initial: `main.py`, `config.py`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`

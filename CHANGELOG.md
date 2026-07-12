# Changelog

All notable changes to QuantLuna are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — S45 production cleanup

### Fixed
- **#19 — WorkflowOrchestrator canonical location** (PR #28)
  - `execution/workflow_orchestrator.py` = canonic (startup workflow 5 faze)
  - `core/workflow_orchestrator.py` rescris ca shim cu `DeprecationWarning`
  - `main.py` import explicit din `execution/` cu comentariu arhitectural
- **#23 — Config validation robusta** (PR #27)
  - `BybitLiveRunnerConfig.__post_init__` valideaza entry/exit z-score, warmup_bars,
    base_qty, max_drawdown_pct, cooldown_seconds la instantiere
  - `main.py` apeleaza `ConfigValidator` + `validate_trading_params()` si face
    sys.exit(1) la erori in live mode
  - `LOG_DIR` / `STATE_DIR` overridabile via env (Docker volumes)
  - `RUNNER_TIMEOUT_SECONDS` hard timeout configurabil

### Added
- **core/multi_market_orchestrator.py** — stub `MultiMarketOrchestrator`
  (extragere v2.2 din fostul `core/workflow_orchestrator.py`);
  implementare completa in Sprint 32

---

## [0.30.0] — 2026-07-12  (Sprint S44–S45)

### Added
- **S44 — MonitoringWatchdog** (`core/monitoring_watchdog.py`)
  - Task asyncio autonom: verifica Sharpe, DD, z-score, half-life, loss streak la 60s
  - Thresholds configurabile per pereche via `PairThreshold` dataclass
  - Actiuni: `ALERT_ONLY`, `REDUCE_SIZE` (50%), `HALT`
  - Alerte Telegram formatate cu emoji per severitate (INFO / WARNING / CRITICAL)
  - Silence per pereche cu countdown, unsilence on-the-fly
  - `from_env()` builder citeste `WATCHDOG_*` env vars
- **S44 — api/watchdog.py** (7 endpoint-uri REST `/api/watchdog/*`)
  - GET status / thresholds, POST update threshold, silence, unsilence, test alert
- **S44 — api/main.py v0.30.0**
  - Inregistreaza `services_router`, `optimizer_router`, `watchdog_router`
  - `WorkflowOrchestrator` in lifespan; `set_watchdog_state()` + `set_optimizer_state()`
- **S44b — WorkflowOrchestrator v2.2** (`core/workflow_orchestrator.py`)
  - `StartupContext.watchdog` camp nou
  - `_build_watchdog(ctx)` cu metrics_provider cascadat (RiskManager > PnLTracker > fallback)
  - `halt_callback` → `api.pairs.halt_pair()`, `reduce_callback` → `api.sizing.reduce_pair_size()`
  - `asyncio.gather()` include acum `watchdog.run_loop()`
  - `stop_runner()` async, apeleaza `watchdog.stop()`
  - `from_env()` classmethod, `build_context()` async, proprietati `.pairs` `.reoptimizer` `.watchdog`
- **S45 — Watchdog Dashboard** (`dashboard/pages/watchdog.tsx`)
  - Status card, thresholds table cu edit inline, alerts feed cu filtru severitate
  - Polling 2s pe toate 3 endpoint-urile simultan
- **S45 — NavBar v1.1** (`dashboard/components/NavBar.tsx`)
  - Link Watchdog cu badge rosu `🚨 N` pentru alerte in ultimele 5min
- **Tests** (`tests/test_monitoring_watchdog.py`) — 10 unit tests
- **Docs** (`docs/watchdog.md`) — arhitectura, thresholds, env vars, API reference

### Changed
- `requirements.txt` — adauga `sse-starlette>=1.8.0`, ordine curatata
- `api/__init__.py` — expune toti routerii noi in `__all__`

---

## [0.29.0] — 2026-07-12  (Sprint S41–S43)

### Added
- **S41 — Services Control Panel** (`api/services.py`, `dashboard/pages/services.tsx`)
  - start/stop/restart servicii, WebSocket live status
- **S42 — WorkflowOrchestrator v2.1**
  - AutoReoptimizer integrat ca task autonom in `asyncio.gather()`
  - `_register_all_services()` pentru Services Control Panel
- **S43 — Optimizer Dashboard** (`dashboard/pages/optimizer.tsx`)
  - Grid search WFO: Run Now, status live, heatmap Sharpe, history table

---

## [0.28.0] — 2026-07-11  (Sprint S39–S40)

### Added
- **S39 — ParamGridOptimizer** (`backtest/param_grid_optimizer.py`)
  - Grid search exhaustiv cu walk-forward validation
  - Anti-overfitting: OOS ratio, Sharpe consistency score
- **S40 — AutoReoptimizer** (`backtest/auto_reoptimizer.py`)
  - Scheduler saptamanal (default: Duminica 02:00 UTC)
  - Aplica automat cei mai buni parametri gasiti in live runner

---

## [0.27.0] — 2026-07-10  (Sprint S36–S38)

### Added
- Multi-Pair Manager cu halt cascade si correlation filter
- Position Sizer Kelly + Fixed, leverage-aware Bybit linear
- AlertDispatcher: Telegram + Discord, retry queue, event types

---

## [0.26.0] — 2026-07-09  (Sprint S33–S35)

### Added
- Risk Dashboard: Sharpe rolling 24h, max DD, win rate, exposure, SSE stream
- WalkForward Optimizer cu regime detection per window
- MarketContext: trending/ranging/volatile classifier

---

*Full history in git log.*

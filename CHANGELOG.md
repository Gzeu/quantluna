# QuantLuna — Changelog

## Sprint 8 — 2026-06-24

### Fixed
- `dashboard/server.py` — `/state` folosește acum `bus.snapshot_dict()` în loc de
  `bus.snapshot()`. Compatibil cu schimbarea din Sprint 7 unde `snapshot()`
  returnează `StateSnapshot` object, nu dict. Elimină bug-ul de serializare JSON.

### Added
- `backtest/monte_carlo.py` — `MonteCarloEngine` pentru robustness testing pe
  trade-uri OOS. Metode suportate:
  - `bootstrap_trades` — resample cu replacement
  - `permutation` — reshuffle ordine trade-uri
  - `block_bootstrap` — bootstrap pe blocuri pentru clustering parțial
  - `cost_stress` — multiplică fees/slippage/funding și rerulează equity path

  Output: DataFrame per-sim + `MonteCarloSummary` cu median/p05/p95 pentru
  total P&L, max DD, ruin probability, profit factor. Folosit după walk-forward,
  nu în locul lui.

- `strategy/live_pair_scanner.py` — `LivePairScanner` pentru pre-filter live
  al pair-urilor candidate. Scorează perechile după:
  corelație rolling, varianță beta rolling, volatilitate spread,
  half-life aproximată, funding net, lichiditate minimă.
  Output: listă `PairCandidate` sortată după score.

### Architecture
- Sprint 8 completează pipeline-ul de validare:
  1. `LivePairScanner` → shortlist operațional
  2. Engle-Granger/Johansen + Kalman Filter → validare structurală
  3. `WalkForwardEngine` → OOS robustness
  4. `MonteCarloEngine` → path dependency + tail risk
  5. `LiveTrader` + dashboard → execuție și monitorizare

### Risk Notes Sprint 8
- `LivePairScanner` este doar un pre-filter; nu validează cointegration.
- `MonteCarloEngine` nu inventează regimuri noi; doar resamplează din sample.
- `cost_stress_multiplier=1.5` e util pentru sanity check; pentru bull panic /
  liquidation events costurile reale pot depăși 2-3x baseline.

---

## Sprint 7 — 2026-06-24

### Added
- `execution/ws_watchdog.py` — `WsWatchdog` async task
- `backtest/engine.py` — `WalkForwardEngine` complet

### Changed
- `state_bus.py` — patch Sprint 6 + Sprint 7

---

## Sprint 6 — 2026-06-24

### Added
- `execution/funding_monitor.py` — `FundingMonitor` async task
- `execution/pnl_reconciler.py` — `PnLReconciler` async task
- `strategy/signal_adapter.py` — `LiveSignalAdapter` + `NormalizedSignal`
- `execution/live_trader_sprint6_patch.py` — ghid de integrare Sprint 6

---

## Sprint 5 — 2026-06-24

### Added
- `state_bus.py` — `StateBus` singleton + `StateSnapshot`
- `dashboard/server.py` — FastAPI: `GET /`, `GET /state`, `WS /ws`
- `dashboard/index.html` — Dashboard live
- `execution/live_trader.py` patch Sprint 5

---

## Sprint 4 — anterioare

- Kalman Filter adaptive hedge ratio (SpreadEngine)
- SignalGenerator v3 cu cointegration + regime detection
- PortfolioRisk + Kelly sizing
- Backtesting walk-forward engine

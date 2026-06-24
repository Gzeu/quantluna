# QuantLuna — Changelog

## Sprint 7 — 2026-06-24

### Added
- `execution/ws_watchdog.py` — `WsWatchdog` async task. Monitorizare health
  WebSocket price feed: `ping()` apelat de `LiveTrader._process_tick()`,
  check loop la fiecare 2s. Trei stari: LIVE / STALE (> 10s) / CRITICAL (> 30s).
  Publică `ws_stale`, `ws_stale_alert`, `ws_last_tick_age_s` în `StateBus`.
  La STALE: suprimaă drift alerts din `PnLReconciler` (evită false positives).
  Rezolvă risk rezidual Sprint 6 #1.
- `backtest/engine.py` — `WalkForwardEngine` complet:
  walk-forward cu `n_splits` configurabil, purge + embargo la granitțe IS/OOS,
  warm-start Kalman pe IS data pentru fiecare fold OOS,
  simulare tranzacții cu fees (maker/taker), slippage (bps), funding cost
  (pro-rata anual), volatilitate-țintă + Kelly fracțional sizing.
  Metrici complete: Sharpe, Sortino, Calmar, max DD, win rate, profit factor,
  Omega ratio. `BacktestResults.print_report()` cu cost breakdown OOS.

### Changed
- `state_bus.py` — patch Sprint 6 + Sprint 7:
  - Câmpuri Sprint 6: `funding_y`, `funding_x`, `funding_net`,
    `reconciled_open_pnl`, `pnl_drift_usd`, `pnl_drift_alert`,
    `position_size_y`, `position_size_x`, `open_pnl_usd` (alias `open_pnl`).
  - Câmpuri Sprint 7: `ws_stale`, `ws_last_tick_age_s`, `ws_stale_alert`.
  - `snapshot()` returnează `StateSnapshot` object (nu dict); `snapshot_dict()`
    nou pentru serializare JSON. `subscribe()` și `_broadcast()` folosesc
    `snapshot_dict()` intern.
  - `update()`: sync automat `open_pnl` ↔ `open_pnl_usd` alias.

### Architecture
- `WsWatchdog` se integrează în `LiveTrader`: `ping()` la fiecare tick,
  `run()` ca `asyncio.Task`. `is_live` property pentru gate entries.
- `PnLReconciler._publish()` verifică `bus.snapshot().ws_stale` înainte de
  `pnl_drift_alert` — suprimă alert dacă feed-ul e stale.
- `WalkForwardEngine.factory` pattern: injectare `SpreadEngine` fresh per fold
  pentru izolare completă a stării Kalman între fold-uri.

### Risk Mitigations Sprint 7
- `stale_warn_s=10s` default conservator; Bybit WS hiccups normale sunt < 5s.
  Ajustează la 15s dacă primești false STALE alerts la funding rollover.
- `purge_bars=10` + `embargo_bars=5` default — suficient pentru 1h bars.
  Pentru 5m bars, crește la 50 + 20.
- Funding cost în backtest simulat ca rată anuală constantă — nu capturează
  spikes de funding. Folosit ca lower bound al costului real.

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

# QuantLuna — Changelog

## Sprint 6 — 2026-06-24

### Added
- `execution/funding_monitor.py` — `FundingMonitor` async task, polling CCXT
  `fetch_funding_rate()` pentru ambele legs, anualizare configurabilă, publică
  `funding_y`, `funding_x`, `funding_net` în `StateBus`. Elimină câmpurile
  funding la `0.0` din dashboard (risk rezidual Sprint 5 #1).
- `execution/pnl_reconciler.py` — `PnLReconciler` async task, polling
  `fetch_positions()` la interval configurabil, compară `unrealizedPnl` de pe
  exchange cu `open_pnl_usd` calculat local din mark price WS. Alert la drift
  `> pnl_drift_alert_usd`. Publică `reconciled_open_pnl`, `pnl_drift_usd`,
  `pnl_drift_alert`, `position_size_y/x`, `entry_price_y/x` în `StateBus`
  (risk rezidual Sprint 5 #2).
- `strategy/signal_adapter.py` — `LiveSignalAdapter` + `NormalizedSignal`:
  wrapper explicit peste `SignalGenerator.generate_live()` care expune
  atributele corecte (`hedge_ratio` = `TradeSignal.beta`,
  `kalman_uncertainty` = `TradeSignal.uncertainty`). Elimină silențios
  `getattr` fallback cu `0.0` în `LiveTrader` (risk rezidual Sprint 5 #3).
- `execution/live_trader_sprint6_patch.py` — ghid de integrare structurat
  pentru patch-ul necesar în `live_trader.py`.

### Architecture
- `FundingMonitor` și `PnLReconciler` rulează ca `asyncio.Task` independente,
  lansate de `LiveTrader.run()`, oprite prin `task.cancel()` la shutdown.
- `LiveSignalAdapter` acceptă atât `SignalGenerator` raw cât și adapter deja
  instanțiat — backward compatible, fără modificare obligatorie în cod existent
  care folosește `SignalGenerator` direct.
- `StateBus.update()` thread-safe acceptă dict parțial — câmpurile noi de
  funding și reconciliere se adaugă fără modificări în schema existentă.

### Risk Mitigations
- Funding annualization factor configurat explicit în `FundingConfig` —
  nu hardcodat; verificare necesară per contract înainte de producție.
- `PnLReconciler` reutilizează exchange CCXT din `FundingMonitor` pentru
  a limita conexiunile REST simultane.
- `pnl_drift_alert_usd` default conservator (5 USD); documentat pentru
  scalare la conturi > 10k USD.

---

## Sprint 5 — 2026-06-24

### Added
- `state_bus.py` — `StateBus` singleton cu `StateSnapshot` dataclass,
  `update()` thread-safe, `subscribe()` async generator cu broadcast
  și evicție automată a subscriberilor lenti.
- `dashboard/server.py` — FastAPI: `GET /`, `GET /state`, `WS /ws`.
  `start_dashboard()` helper pentru lansare ca asyncio task.
- `dashboard/index.html` — Dashboard live: 8 KPI cards, chart z-score
  cu benzi ±2σ, chart P&L cumulat, panel Kalman, funding rates,
  tabel trades recente, teme light/dark, WS reconnect automat.
- `execution/live_trader.py` patch — `_publish_state()` la fiecare tick,
  `record_trade()` pe bus la fiecare exit, `state_bus_enabled` în
  `LiveConfig`.

---

## Sprint 4 — anterioare

- Kalman Filter adaptive hedge ratio (SpreadEngine)
- SignalGenerator v3 cu cointegration + regime detection
- PortfolioRisk + Kelly sizing
- Backtesting walk-forward engine

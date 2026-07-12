# QuantLuna — Roadmap S30–S46+

> Ultima actualizare: 2026-07-12  
> Baza: Sprint S46 (audit sync post-S45 — MonitoringWatchdog + WorkflowOrchestrator v2.2)

---

## Viziune generală

QuantLuna devine un sistem **multi-market, multi-strategie, auto-scaling** care:
- Tranzacționează **Futures Linear** (activ) + **Spot** (S30) + **Margin** (S35)
- Alocă capital dinamic per strategie bazat pe PnL zilnic (S31)
- Mută fonduri automat între wallet-urile Bybit intern (S32)
- Propune retrageri externe cu confirmare manuală Telegram (S33)
- Rulează toate piețele în paralel cu un singur orchestrator (S34)
- Monitorizează continuu Sharpe, DD, z-score, half-life prin Watchdog (S45)

---

## ✅ Completed Sprints (S29–S46)

| Sprint | Focus | Status |
|--------|-------|--------|
| S29 | Orchestrator v3.3 + HedgeManager activ | ✅ Done |
| S30 | Spot Trading Support — SpotOrderRouter | ✅ Done |
| S31 | Capital Allocator + DailyPnLTracker | ✅ Done |
| S32 | Internal Transfer Manager (Bybit intern) | ✅ Done |
| S33 | Withdrawal Guard (confirmare Telegram) | ✅ Done |
| S34 | Multi-Market Runner (Futures + Spot gather) | ✅ Done |
| S35 | Margin Trading + MarginRiskGuard | ✅ Done |
| S36 | Portfolio Dashboard Extension | ✅ Done |
| S37 | Walk-forward Optimizer v1 + baseline tests | ✅ Done |
| S38 | Regime Detector integration + vol adapter | ✅ Done |
| S39 | Live Data Bridge + WebSocket reconnect logic | ✅ Done |
| S40 | State Bus refactor + Redis pub/sub | ✅ Done |
| S41 | Performance Analytics + Trade Journal | ✅ Done |
| S42 | WFO v2 — rolling windows + fold validation | ✅ Done |
| S43 | Cointegration module + correlation matrix | ✅ Done |
| S44 | ParamGridOptimizer + AutoReoptimizer scaffold | ✅ Done |
| S45 | MonitoringWatchdog + WorkflowOrchestrator v2.2 | ✅ Done |
| S46 | Audit sync: core exports, env vars, tests, roadmap | ✅ Done |

---

## S30 — Spot Trading Support
**Fișiere noi:** `execution/spot_order_router.py`, `execution/spot_wallet_scanner.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| SpotOrderRouter cu `category=spot` | spot_order_router.py | CRITIC |
| SpotWalletScanner — citește balanțe spot | spot_wallet_scanner.py | CRITIC |
| exchange_factory.py extins cu `mode=spot` | exchange_factory.py | CRITIC |
| StrategyClassifier extins cu `AssetType.SPOT` | strategy_classifier.py | MEDIU |
| HealthCheck extins — verifică spot wallet | health_check.py | MEDIU |

**Logică:** Spot nu are poziții (e sold de monedă), deci `SpotWalletScanner`
citește `/v5/account/wallet` cu `accountType=SPOT` și construiește un
`SpotHolding` per asset. `SpotOrderRouter` wrappează market/limit orders
pe `category=spot` fără qty rounding de futures.

---

## S31 — Capital Allocator + Daily PnL Tracker
**Fișiere noi:** `execution/capital_allocator.py`, `execution/daily_pnl_tracker.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| DailyPnLTracker — SQLite zilnic per strategie | daily_pnl_tracker.py | CRITIC |
| CapitalAllocator — reguli % equity per strategie | capital_allocator.py | CRITIC |
| Trigger: PnL > threshold → muta % în rezervă | capital_allocator.py | CRITIC |
| Telegram raport zilnic 23:59 UTC | capital_allocator.py | MEDIU |
| ReserveManager — USDT idle tracking | capital_allocator.py | MEDIU |

---

## S32 — Internal Transfer Manager
**Fișiere noi:** `execution/internal_transfer_manager.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| Transfer Futures → Spot wallet (intern Bybit) | internal_transfer_manager.py | CRITIC |
| Transfer Spot → Futures wallet | internal_transfer_manager.py | CRITIC |
| Cooldown guard (min 10 min între transferuri) | internal_transfer_manager.py | CRITIC |
| Audit log SQLite pentru fiecare transfer | internal_transfer_manager.py | MEDIU |
| Telegram notificare per transfer | internal_transfer_manager.py | MEDIU |

**API folosit:** `POST /v5/asset/transfer/inter-transfer`  
**Risc:** ZERO — banii rămân în același cont Bybit, doar schimbă wallet-ul.

---

## S33 — Withdrawal Guard (confirmare manuală)
**Fișiere noi:** `execution/withdrawal_guard.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| WithdrawalProposal — generează cerere + UUID | withdrawal_guard.py | CRITIC |
| Telegram: "Confirmi retragerea X USDT? /confirm_UUID" | withdrawal_guard.py | CRITIC |
| Timeout 30 min — dacă nu confirmi, anulează | withdrawal_guard.py | CRITIC |
| Blacklist adrese neautorizate | withdrawal_guard.py | CRITIC |
| Audit log complet cu IP + timestamp | withdrawal_guard.py | CRITIC |

---

## S34 — Multi-Market Runner
**Fișiere noi:** `execution/multi_market_runner.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| MultiMarketRunner — gather Futures + Spot runners | multi_market_runner.py | CRITIC |
| WorkflowOrchestrator extins cu market_type param | workflow_orchestrator.py | CRITIC |
| Config: `markets: [futures, spot]` în runner_cfg | config/ | MEDIU |
| Dashboard: tab separat per piață | dashboard/ | MEDIU |

---

## S35 — Margin Trading
**Fișiere noi:** `execution/margin_order_router.py`, `execution/margin_risk_guard.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| MarginOrderRouter cu `category=spot` + margin mode | margin_order_router.py | CRITIC |
| MarginRiskGuard — monitorizare margin ratio | margin_risk_guard.py | CRITIC |
| Auto-deleverage dacă margin ratio < 110% | margin_risk_guard.py | CRITIC |
| Telegram ALERT la margin ratio < 150% | margin_risk_guard.py | CRITIC |

---

## S36 — Portfolio Dashboard Extension
**Fișiere modificate:** `dashboard/`, `api/`

| Task | Modul | Prioritate |
|------|-------|------------|
| Pagina /portfolio cu equity curve | dashboard/ | MEDIU |
| API endpoint /api/capital-allocation | api/ | MEDIU |
| Grafic PnL zilnic per strategie | dashboard/ | MEDIU |
| Alertă vizuală dacă margin ratio scade | dashboard/ | MEDIU |

---

## S47+ — Backlog planificat

| Sprint | Focus | Status |
|--------|-------|--------|
| S47 | AI/ML signal layer — feature engineering + model inference | ⏳ PLANNED |
| S48 | Multi-strategy coordinator — weight allocation per regime | ⏳ PLANNED |
| S49 | Live paper trading environment cu shadow P&L | ⏳ PLANNED |
| S50 | Production hardening — circuit breakers + chaos testing | ⏳ PLANNED |

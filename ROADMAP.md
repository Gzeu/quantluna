# QuantLuna — Roadmap complet S30–S36

> Ultima actualizare: 2026-07-12  
> Baza: Sprint S29 v3.3 (orchestrator + hedge manager activ)

---

## Viziune generală

QuantLuna devine un sistem **multi-market, multi-strategie, auto-scaling** care:
- Tranzacționează **Futures Linear** (deja activ) + **Spot** (S30) + **Margin** (S35)
- Alocă capital dinamic per strategie bazat pe PnL zilnic (S31)
- Mută fonduri automat între wallet-urile Bybit intern (S32)
- Propune retrageri externe cu confirmare manuală Telegram (S33)
- Rulează toate piețele în paralel cu un singur orchestrator (S34)

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

**Logică:**
```
zilnic la 23:55 UTC:
  pnl = DailyPnLTracker.get_today()
  if pnl.realised_pct > PROFIT_TAKE_PCT:        # ex: 3%
      CapitalAllocator.move_to_reserve(pnl.excess_usdt)
  if equity_total > HIGH_WATERMARK * 1.20:       # +20% față de max anterior
      CapitalAllocator.rebalance_strategies()
  Telegram: raport complet
```

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

**Principiu de securitate:** Sistemul NICIODATĂ nu execută o retragere fără
comanda `/confirm_<UUID>` din Telegram de la chat_id autorizat.
Retragerea este ireversibilă — confirmare dublă obligatorie.

---

## S34 — Multi-Market Runner
**Fișiere noi:** `execution/multi_market_runner.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| MultiMarketRunner — gather Futures + Spot runners | multi_market_runner.py | CRITIC |
| WorkflowOrchestrator extins cu market_type param | workflow_orchestrator.py | CRITIC |
| Config: `markets: [futures, spot]` în runner_cfg | config/ | MEDIU |
| Dashboard: tab separat per piață | dashboard/ | MEDIU |

**Arhitectură:**
```
MultiMarketRunner.start()
  ├── asyncio.gather(
  │     BybitLiveRunner.start(),        # Futures Linear
  │     SpotStrategyRunner.start(),     # Spot (nou S30)
  │     [HedgeManager_i.manage()],      # Solo hedges
  │     CapitalAllocator.run_loop(),    # S31
  │     InternalTransferManager.watch() # S32
  │   )
```

---

## S35 — Margin Trading
**Fișiere noi:** `execution/margin_order_router.py`, `execution/margin_risk_guard.py`

| Task | Modul | Prioritate |
|------|-------|------------|
| MarginOrderRouter cu `category=spot` + margin mode | margin_order_router.py | CRITIC |
| MarginRiskGuard — monitorizare margin ratio | margin_risk_guard.py | CRITIC |
| Auto-deleverage dacă margin ratio < 110% | margin_risk_guard.py | CRITIC |
| Telegram ALERT la margin ratio < 150% | margin_risk_guard.py | CRITIC |

**Atenție:** Margin trading adaugă risc de lichidare. `MarginRiskGuard`
monitor izează la fiecare 30s și închide automat dacă se apropie de liquidation.

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

## Status Sprints

| Sprint | Status | ETA |
|--------|--------|-----|
| S29 — Orchestrator v3.3 + HedgeManager | ✅ DONE | 2026-07-12 |
| S30 — Spot Support | 🔄 IN PROGRESS | 2026-07-12 |
| S31 — Capital Allocator | 🔄 IN PROGRESS | 2026-07-12 |
| S32 — Internal Transfer | ⏳ PLANNED | 2026-07-13 |
| S33 — Withdrawal Guard | ⏳ PLANNED | 2026-07-14 |
| S34 — Multi-Market Runner | ⏳ PLANNED | 2026-07-15 |
| S35 — Margin Trading | ⏳ PLANNED | 2026-07-17 |
| S36 — Portfolio Dashboard | ⏳ PLANNED | 2026-07-19 |

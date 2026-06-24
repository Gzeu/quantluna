# QuantLuna — Changelog

## Sprint 10 — 2026-06-24

### Added

**`risk/` — pachet extins cu 4 module noi**

- `risk/correlation_matrix.py` — `SpreadCorrelationMatrix`
  - Buffer rolling per pair (default 120 bare)
  - `check_new_pair()` — blocare candidat dacă |corr| > threshold cu oricare activ
  - `diversification_discount()` — factor [0,1] pentru penalizare Kelly
  - `get_correlation_matrix()` — DataFrame complet, cu Ledoit-Wolf shrinkage opțional
  - Ledoit-Wolf via scikit-learn (dezactivabil dacă sklearn nu e instalat)

- `risk/kelly.py` — `KellyCrossPair` + `KellyConfig` + `KellyResult`
  - Kelly continuu Thorp: f* = E[R] / E[R²]
  - Fractional Kelly configurable (default 0.25)
  - Vol target sizing ca fallback (< 20 trades sau E[R] <= 0)
  - Correlation discount din `SpreadCorrelationMatrix`
  - Portfolio cap: min(kelly_adj, vol_target, max_pair_cap, spațiu_rămas)

- `risk/drawdown_controller.py` — `DrawdownController` + `DDConfig` + `DDSnapshot`
  - NORMAL → SOFT_LIMIT (8% DD) → HARD_STOP (15% DD)
  - Pair-level force close la 5% pair DD
  - HWM tracking (high-water mark, nu capital inițial)
  - `manual_resume()` cu reset HWM opțional — re-activare explicită

- `risk/multi_pair_allocator.py` — `PortfolioAllocator` + `AllocatorConfig` + `AllocationDecision`
  - Orchestrator cu 5 gates secvențiale per entry request:
    1. DD level check
    2. Max concurrent pairs check
    3. Correlation check
    4. Kelly sizing cu discount
    5. PortfolioRisk exposure check
  - `update_state()` — per-tick: correlation matrix + DD controller + PnL sync
  - `record_exit()` — curăță toate structurile la exit
  - `manual_resume()` — re-activare după HARD_STOP
  - `portfolio_summary()` — snapshot complet incluzând correlation matrix

- `risk/__init__.py` — updatat cu toate exports Sprint 10

### Architecture Sprint 10

Pipeline complet:
```
LivePairScanner (S8)
    → CointegrationValidator (S9)
    → PortfolioAllocator.request_entry() (S10)  ← NOU
        ├─ DD level gate
        ├─ Max pairs gate
        ├─ Correlation gate
        ├─ Kelly cross-pair sizing
        └─ PortfolioRisk exposure gate
    → SpreadEngine / Kalman Filter
    → LiveTrader (S5-S7)
        └─ per tick: allocator.update_state()
    → Dashboard (S5)
```

### Utilizare rapidă Sprint 10

```python
from risk import PortfolioAllocator, AllocatorConfig
from risk.kelly import KellyConfig
from risk.drawdown_controller import DDConfig

cfg = AllocatorConfig(
    capital_usd=10_000,
    max_concurrent_pairs=4,
    kelly=KellyConfig(kelly_fraction=0.25, vol_target=0.01),
    drawdown=DDConfig(
        pair_soft_dd=0.05,
        portfolio_soft_dd=0.08,
        portfolio_hard_dd=0.15,
    ),
)
allocator = PortfolioAllocator(cfg)

# La semnal de intrare:
decision = allocator.request_entry(
    pair_id="ETH/BTC_perp",
    candidate_spread=spread_series,
    trade_pnl_history=oos_pnl_series,
    current_zscore=-2.3,
    entry_beta=0.0534,
)
print(decision.summary())

if decision.allowed:
    notional = decision.notional_usd  # → trimite ordin

# Per tick:
snap = allocator.update_state(
    open_pnl_per_pair={"ETH/BTC_perp": 45.2},
    spread_updates={"ETH/BTC_perp": 0.0118},
)
if snap.level.value == "HARD_STOP":
    await live_trader.close_all()
for pair_id in snap.pairs_force_close:
    await live_trader.close_pair(pair_id)

# La exit:
allocator.record_exit("ETH/BTC_perp")
```

### Risk Notes Sprint 10

- Ledoit-Wolf shrinkage necesită `scikit-learn`. Fără el, fallback automat la
  numpy `corrcoef`. Adăugați `scikit-learn` în requirements.txt.
- Kelly pe sample mic (< 20 trades) folosește vol_target_only.
  Pe pair nou fără historical trades, sizing-ul va fi conservativ — corect.
- HARD_STOP nu se auto-resetează. `manual_resume()` trebuie apelat
  explicit după analiză post-hoc a cauzei drawdown-ului.
- `max_concurrent_pairs=4-5` este recomandarea pentru capital 10-50k USD.
  Sub 10k: max 2-3 perechi. Peste 100k: reconsideraiți sizing și limits.
- Correlation matrix se actualizează per tick. La startup (înainte de
  `min_history` bare), toate pair-urile noi sunt permise automat —
  verificați manual primele 30 bare după lansare.

---

## Sprint 9 — 2026-06-24

### Added
- `strategy/cointegration/` — pachet complet
- `EngleGrangerTest`, `JohansenTest`, `ResidualDiagnostics`, `CointegrationValidator`

---

## Sprint 8 — 2026-06-24

### Fixed
- `dashboard/server.py` — `bus.snapshot_dict()` fix

### Added
- `backtest/monte_carlo.py`, `strategy/live_pair_scanner.py`

---

## Sprint 7 — 2026-06-24

### Added
- `execution/ws_watchdog.py`, `backtest/engine.py`

### Changed
- `state_bus.py` patch Sprint 6 + 7

---

## Sprint 6 — 2026-06-24

### Added
- `execution/funding_monitor.py`, `pnl_reconciler.py`
- `strategy/signal_adapter.py`

---

## Sprint 5 — 2026-06-24

### Added
- `state_bus.py`, `dashboard/server.py`, `dashboard/index.html`

---

## Sprint 4 — anterioare

- Kalman Filter adaptive hedge ratio (SpreadEngine)
- SignalGenerator v3, PortfolioRisk, PositionSizer
- Backtesting walk-forward engine

# QuantLuna — Changelog

## Sprint 9 — 2026-06-24

### Added

**`strategy/cointegration/` — pachet nou complet**

- `__init__.py` — exports publice pachet

- `engle_granger.py` — `EngleGrangerTest` + `EGResult`
  - OLS Y = alpha + beta·X, ADF pe reziduuri (MacKinnon tabele via statsmodels)
  - Half-life AR(1) din delta reziduuri
  - Avertismente automate: sample mic, half-life lungă, beta near zero
  - `EGResult.summary()` — printabil direct în logs

- `johansen.py` — `JohansenTest` + `JohansenResult`
  - Trace test + Max-Eigenvalue test cu valori critice 95%
  - Autoselect k_ar_diff via AIC pe VAR (max 5 lags)
  - Hedge ratios normalizate din eigenvectors (sym_y = 1.0)
  - Suport 2+ serii (basket trading ready)
  - `JohansenResult.summary()` — printabil direct

- `residual_diagnostics.py` — `ResidualDiagnostics` + `ResidualReport`
  - Ljung-Box autocorrelation test (lags = sqrt(n))
  - Jarque-Bera normality test
  - ARCH LM heteroskedasticity test
  - Half-life AR(1) check vs interval [min, max] configurabil
  - Structural stability: rolling mean z-score break detection
  - `ResidualReport.passed_all` — flag operațional

- `validator.py` — `CointegrationValidator` + `ValidatorConfig` + `ValidationReport`
  - Orchestrator complet: EG → Johansen → ResidualDiagnostics
  - Verdict VALID / MARGINAL / INVALID cu raționament explicit
  - Hedge ratio inițial recomandat ca seed pentru Kalman Filter
  - Risk flags specifice per pair
  - `ValidationReport.summary()` — raport complet printabil

### Architecture Sprint 9

Pipeline complet de validare acum:
```
LivePairScanner (Sprint 8)
    → CointegrationValidator (Sprint 9)
        → EngleGrangerTest
        → JohansenTest  
        → ResidualDiagnostics
        → ValidationReport (verdict + hedge_ratio_initial)
    → SpreadEngine / Kalman Filter (core/)
    → WalkForwardEngine (backtest/)
    → MonteCarloEngine (backtest/)
    → LiveTrader (execution/)
    → Dashboard (dashboard/)
```

### Utilizare rapidă Sprint 9

```python
from strategy.cointegration import CointegrationValidator, ValidatorConfig

validator = CointegrationValidator(ValidatorConfig(
    eg_alpha=0.05,
    require_both_tests=False,   # OR logic: EG sau Johansen suficient
    rd_half_life_min=2.0,
    rd_half_life_max=72.0,      # mai strict pe 1h data
))

report = validator.validate(
    close_y=df["ETH/USDT:USDT"],
    close_x=df["BTC/USDT:USDT"],
    sym_y="ETH/USDT:USDT",
    sym_x="BTC/USDT:USDT",
)

print(report.summary())

if report.verdict == "VALID":
    kalman_seed_beta = report.hedge_ratio_initial
    half_life = report.half_life_bars
    # → SpreadEngine(KalmanConfig(beta_init=kalman_seed_beta))
```

### Risk Notes Sprint 9
- `verdict == "VALID"` este condiție necesară, nu suficientă.
- Rulați re-validare periodică (recomandat săptămânal sau după volatilitate extremă).
- Un pair valid la 1h poate fi invalid la 4h sau 15m — timeframe-ul contează.
- Johansen cu det_order=1 (trend) pe crypto produce frecvent false positives.
  Lăsați det_order=0 (default) pentru majority of pairs.
- Structural break detection simplist — nu înlocuiește Chow test pe subperioade.

---

## Sprint 8 — 2026-06-24

### Fixed
- `dashboard/server.py` — `/state` folosește acum `bus.snapshot_dict()`

### Added
- `backtest/monte_carlo.py` — `MonteCarloEngine`
- `strategy/live_pair_scanner.py` — `LivePairScanner`

---

## Sprint 7 — 2026-06-24

### Added
- `execution/ws_watchdog.py` — `WsWatchdog`
- `backtest/engine.py` — `WalkForwardEngine`

### Changed
- `state_bus.py` — patch Sprint 6 + Sprint 7

---

## Sprint 6 — 2026-06-24

### Added
- `execution/funding_monitor.py`, `pnl_reconciler.py`
- `strategy/signal_adapter.py`
- `execution/live_trader_sprint6_patch.py`

---

## Sprint 5 — 2026-06-24

### Added
- `state_bus.py`, `dashboard/server.py`, `dashboard/index.html`

---

## Sprint 4 — anterioare

- Kalman Filter adaptive hedge ratio (SpreadEngine)
- SignalGenerator v3 cu cointegration + regime detection
- PortfolioRisk + Kelly sizing
- Backtesting walk-forward engine

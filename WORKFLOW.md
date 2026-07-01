# QuantLuna — Workflow Complet de Pornire şi Gestiune Pozitii

## Diagrama completă

```
                        STARTUP
                           │
              ┌──────────┴──────────┐
              │  FAZA 0: Pre-flight    │
              │  preflight_check.py    │
              └──────────┬──────────┘
                         │ (✔ trecut)
              ┌──────────┴──────────┐
              │  FAZA 1: Position Scan  │
              │  PositionScanner       │
              └───────┬──────┬────┘
                        │               │
                 MANAGED │        ORPHAN │
                        │               │
              ┌───────┴─────┐  ┌─────┴──────┐
              │  FAZA 2: Reconcile │  │  FAZA 3: Adopt   │
              │  ResumeManager    │  │  AdoptionEngine  │
              └───┬─────┬───┘  └──┬────┬────┘
                  │          │        │          │
              resume     HALT  CLOSE_NOW    ADOPT
                  │               │          │
                  │         market close    FAZA 4:
                  │                       ProfitOptimizer
                  │                       .register()
                  │                            │
              ┌──┴───────────────────────┴──┐
              │      FAZA 5: LiveTrader.run()      │
              │    + optimizer_loop (background)   │
              └──────────────────────────────┘
```

## Fazele explicate

### FAZA 0 — Pre-flight (`scripts/preflight_check.py`)
14 verificări: env, API keys, capital, leverage, conectivitate exchange, balance, simboluri valide, SQLite.

### FAZA 1 — Position Scan (`execution/position_scanner.py`)
Fetch toate pozițiile deschise de pe exchange. Clasificare:
- **MANAGED** — se găsesc şi în checkpoint (bot le gestionează deja)
- **ORPHAN**  — există pe cont dar fără checkpoint (create manual/altă sesiune)
- **STALE**   — în checkpoint dar nu pe exchange (deja închise extern)

### FAZA 2 — Reconciliere (`execution/resume_manager.py`)
Verifică dacă poziția din checkpoint corespunde cu ce e real pe exchange.
Trei ieşire: resume / fresh-start / HALT cu alert.

### FAZA 3 — Adoptie orfane (`execution/adoption_engine.py`)

Pentru fiecare poziție orfană, decizie automată:

| Conditie | Decizie | Actiune |
|---------|---------|--------|
| PnL > -2% şi dist_liq > 8% | ADOPT | Preia, setează TP/SL/trailing |
| PnL în [-5%, -2%] | ADOPT conservator | Preia cu SL strâns |
| PnL < -5% | CLOSE_NOW | Market order close imediat |
| dist_liq < 8% | CLOSE_NOW | Market order close imediat |
| notional < 5 USDT | MONITOR_ONLY | Urmăreşte fără ordine automate |

### FAZA 4 — Profit Optimizer (`execution/profit_optimizer.py`)

Fiecare poziție adoptată primete:

| Mecanism | Trigeer | Actiune |
|---------|---------|--------|
| **Break-even** | PnL ≥ +1.5% | Mută SL la entry+0.1% |
| **Profit Ladder L1** | PnL ≥ +2% | Încidem 25% din poziție |
| **Profit Ladder L2** | PnL ≥ +4% | Încidem încă 25% |
| **Profit Ladder L3** | PnL ≥ +7% | Încidem încă 30% |
| **Trailing Stop** | PnL ≥ +2% (activăm) | Stop la 1.5% sub peak |
| **Take-Profit** | Preț atinge TP (+4%) | Încidem tot restul |
| **Stop-Loss** | Preț atinge SL (-3%) | Încidem tot imediat |

### FAZA 5 — LiveTrader + Optimizer Loop
LiveTrader rulează normal (strategie pair-trading Kalman).
Optimizer loop rulează în background task separat pentru pozițiile adoptate.

---

## Integrare `main.py`

```python
from execution.workflow_orchestrator import WorkflowOrchestrator
from execution.live_trader import AlertConfig

# La startup, înainte de LiveTrader:
orch = WorkflowOrchestrator(
    exchange=ccxt_exchange,
    checkpoint_path="position_checkpoint.db",
    alert_cfg=alert_config,
)
ctx = await orch.run_startup_workflow()

if ctx.should_halt:
    logger.critical(f"Startup HALT: {ctx.halt_reason}")
    sys.exit(1)

if ctx.has_adopted_positions:
    asyncio.create_task(
        orch.run_optimizer_loop(ctx, get_current_prices, poll_interval_s=1.0)
    )

# Porneste LiveTrader normal
await live_trader.run()
```

---

## Fisiere noi adaugate

```
execution/
├── position_scanner.py      # FAZA 1 - detectie orphan positions
├── adoption_engine.py       # FAZA 3 - decizie si adoptie
├── profit_optimizer.py      # FAZA 4 - TP/SL/trailing/ladder
├── workflow_orchestrator.py  # glue code fazele 1-4
├── checkpoint.py            # FAZA 2 - SQLite position state
├── resume_manager.py        # FAZA 2 - reconciliere startup
├── circuit_breaker.py       # resilience
├── backoff.py               # resilience
```

---

## Configurare `.env` pentru adoption

```bash
# Praguri adoptie pozitii orfane
ADOPT_MIN_PNL_PCT=-0.02      # adopt daca PnL > -2%
CLOSE_LOSS_PCT=-0.05         # inchide daca PnL < -5%
MIN_LIQ_DISTANCE_PCT=0.08    # inchide daca dist_liq < 8%

# Profit optimizer
TP_TARGET_PCT=0.04            # TP la +4%
SL_MAX_LOSS_PCT=0.03          # SL la -3%
TRAILING_ACTIVATION_PCT=0.02  # activeaza trailing la +2%
TRAILING_DISTANCE_PCT=0.015   # trail la 1.5% sub peak
```

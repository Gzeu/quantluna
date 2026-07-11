# MonitoringWatchdog — QuantLuna S44

> Task asyncio autonom care monitorizeaza fiecare pereche de tranzactionare la interval fix
> si declanseaza actiuni (HALT / REDUCE_SIZE / ALERT_ONLY) via Telegram.

---

## Arhitectura

```
WorkflowOrchestrator.start_runner()
  └── asyncio.gather(
        runner.start(),
        reoptimizer.run_loop(),
        watchdog.run_loop(),
      )

MonitoringWatchdog.run_loop()
  └── la fiecare CHECK_INTERVAL secunde:
        pentru fiecare pereche configurata:
          metrics = await metrics_provider(pair)
          evalueaza contra PairThreshold
          daca violat → WatchdogAlert → Telegram → actiune
```

---

## Metrici monitorizate

| Metric | Sursa | Violation | Severitate default |
|---|---|---|---|
| `sharpe` | RiskManager / PnLTracker | `< sharpe_min` | WARNING |
| `drawdown` | RiskManager | `> max_drawdown` | CRITICAL → HALT |
| `z_score` | cointegration engine | `\|z\| > z_max` | INFO |
| `half_life` | cointegration engine | `> hl_max` ore | INFO |
| `loss_streak` | PnLTracker | `>= loss_streak` | WARNING |

---

## Thresholds per pereche

Fiecare pereche are propriul `PairThreshold`:

```python
@dataclass
class PairThreshold:
    pair: str
    sharpe_min: float = 0.3
    max_drawdown: float = 0.10
    z_max: float = 4.0
    hl_max: float = 96.0
    loss_streak: int = 5
    action: str = "ALERT_ONLY"
    silenced_until: Optional[datetime] = None
```

**Actiuni disponibile:**

| Actiune | Efect |
|---|---|
| `ALERT_ONLY` | Trimite Telegram, nu opreste |
| `REDUCE_SIZE` | Reduce sizing la 50% + alerta WARNING |
| `HALT` | Opreste complet perechea + alerta CRITICAL |

---

## Variabile de mediu

```bash
WATCHDOG_ENABLED=true
WATCHDOG_CHECK_INTERVAL=60
WATCHDOG_SHARPE_MIN=0.3
WATCHDOG_MAX_DD=0.10
WATCHDOG_Z_MAX=4.0
WATCHDOG_HL_MAX=96
```

Thresholds per pereche se pot suprascrie on-the-fly via API:

```bash
curl -X POST http://localhost:8000/api/watchdog/thresholds/BTCUSDT-ETHUSDT \
     -H 'Content-Type: application/json' \
     -d '{"sharpe_min": 0.5, "action": "HALT"}'
```

---

## Integrare WorkflowOrchestrator

`MonitoringWatchdog` este construit automat in `_build_watchdog()` si pornit in `asyncio.gather()`.

```python
ctx.watchdog = self._build_watchdog(ctx)
coros.append(self._watchdog.run_loop())
```

Metrics provider cascadeaza astfel:

```
1. api.risk.get_live_metrics(pair)
2. ctx.pnl_tracker.snapshot(pair)
3. fallback neutru
```

Callbacks automate:
- `halt_callback` → `api.pairs.halt_pair(pair, reason="watchdog_dd_breach")`
- `reduce_callback` → `api.sizing.reduce_pair_size(pair, 0.5)`

---

## API Reference `/api/watchdog`

| Method | Endpoint | Descriere |
|---|---|---|
| GET | `/api/watchdog/status` | Status watchdog + ultimele 10 alerte |
| GET | `/api/watchdog/thresholds` | Thresholds active per pereche |
| POST | `/api/watchdog/thresholds/{pair}` | Update partial threshold on-the-fly |
| POST | `/api/watchdog/silence/{pair}?minutes=60` | Mute alerte 1–60 min |
| POST | `/api/watchdog/unsilence/{pair}` | Anuleaza silence |
| GET | `/api/watchdog/alerts?limit=50` | Istoric alerte |
| POST | `/api/watchdog/test/{pair}` | Trimite alerta test pe Telegram |

---

## Dashboard `/watchdog`

Pagina Next.js (`dashboard/pages/watchdog.tsx`) include:
- Status card
- Thresholds table cu edit inline
- Alerts feed live cu filtru severitate
- NavBar badge rosu pentru alerte recente

---

## Exemple alerte Telegram

**CRITICAL (HALT):**
```
🚨 QuantLuna Watchdog 🛑
Pereche: BTCUSDT-ETHUSDT
Metric: drawdown = 0.1234
Threshold: 0.1000
Actiune: HALT
Severitate: CRITICAL
2026-07-12T02:05:00Z
```

**WARNING (ALERT_ONLY):**
```
⚠️ QuantLuna Watchdog 🔔
Pereche: SOLUSDT-AVAXUSDT
Metric: sharpe = 0.2100
Threshold: 0.3000
Actiune: ALERT_ONLY
Severitate: WARNING
2026-07-12T02:05:00Z
```

---

## Unit Tests

```bash
pytest tests/test_monitoring_watchdog.py -v
```

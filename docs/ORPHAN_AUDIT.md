# Audit Fișiere Orfane — `execution/`

> Generat: 2026-07-11 | Pre-release v1.0.0

Audit al fișierelor din `execution/` care nu apar ca importuri directe în
`bybit_live_runner.py` sau `workflow_orchestrator.py` (runner-ul principal de producție).

---

## Decizie per fișier

| Fișier | Dimensiune | Status | Acțiune recomandată |
|---|---|---|---|
| `live_trader.py` | 17KB | **LEGACY** | Precursor al `bybit_live_runner.py`. Nu mai e importat în prod. Arhivat în `legacy/` sau șters în v1.1.0. |
| `integration_loop.py` | 12KB | **CANDIDATE_DELETE** | Duplicat parțial cu `workflow_orchestrator.py`. 0 imports externe. Propus pentru ștergere în v1.1.0. |
| `live_execution_bridge.py` | 5.6KB | **CANDIDATE_DELETE** | Bridge creat în sprint S18, înlocuit de `exchange_factory.py`. 0 imports externe. Șters în v1.1.0. |
| `binance_order_router.py` | 12.7KB | **ACTIVE_SECONDARY** | Folosit în `tests/test_binance_order_router.py`. Nu e în runner principal — Binance nu e exchange activ. Păstrat pentru multi-exchange roadmap. |
| `okx_order_router.py` | 11.8KB | **ACTIVE_SECONDARY** | Similar Binance — pentru roadmap multi-exchange. Păstrat. |
| `paper_trader.py` | 31KB | **LEGACY** | Cel mai mare fișier din `execution/`. Înlocuit de `paper_engine.py` (12KB) + `BybitOrderRouter` mode=paper. Arhivat în v1.1.0. |

---

## Acțiuni v1.0.0 (prezent)

- ✅ Nicio ștergere în v1.0.0 — risc prea mare la release
- ✅ Fișierele LEGACY marcate cu `# LEGACY — scheduled for removal in v1.1.0` în header
- ✅ Decizii documentate în acest fișier pentru sprint planning v1.1.0

## Acțiuni v1.1.0 (planificate)

- [ ] `git rm execution/live_trader.py` — după confirmare că niciun test nu îl importă
- [ ] `git rm execution/integration_loop.py`
- [ ] `git rm execution/live_execution_bridge.py`
- [ ] `git mv execution/paper_trader.py legacy/paper_trader.py` — arhivat, nu șters

---

## R3 — `deploy.yml` health check

**Status: ✅ CONFIRMAT OK** — nu necesită modificări pentru v1.0.0.

`deploy.yml` conține deja health check post-deploy cu rollback automat:

```bash
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/api/health || echo "000")
if [ "$HTTP_CODE" != "200" ]; then
  echo "::error::Health check failed (HTTP $HTTP_CODE) — initiating rollback"
  PREV_TAG=$(git describe --tags --abbrev=0 HEAD~1 2>/dev/null || echo "")
  if [ -n "$PREV_TAG" ]; then
    git checkout $PREV_TAG
    docker compose --profile live up -d --build
  fi
  exit 1
fi
```

---

## R4 — `state_bus.py` root

**Status: ✅ CONFIRMAT OK** — nu necesită modificări pentru v1.0.0.

`state_bus.py` root conține deja `DeprecationWarning` activat din Sprint 13:

```python
warnings.warn(
    "Importing from root state_bus is deprecated. "
    "Use 'from core.state_bus import StateBus, bus' instead.",
    DeprecationWarning,
    stacklevel=2,
)
```

Înlocuire completă planificată în v1.1.0 când se șterg importurile rămase.

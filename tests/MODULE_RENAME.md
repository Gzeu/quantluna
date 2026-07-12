# Issue #22 — Redenumire teste `test_sprintXX.py` după modul

## Starea curentă

Fișierele cu naming sprint (`test_sprintXX.py`) conțin teste pentru mai multe
module și nu oferă informații despre *ce* testează la o simplă citire a directorului.

| Fișier vechi (sprint) | Modul(e) principal(e) testate | Fișier nou (modul) |
|---|---|---|
| `test_sprint10_allocator.py` | `execution.capital_allocator` | `test_capital_allocator.py` |
| `test_sprint15_backtest.py` | `backtest.engine` | `test_backtest_engine.py` |
| `test_sprint16_api.py` | `api.routes` (FastAPI) | `test_api_routes.py` |
| `test_sprint16_enhancements.py` | `api.routes` enhancements | `test_api_enhancements.py` |
| `test_sprint17.py` | `execution.multi_market_runner` | `test_multi_market_runner.py` |
| `test_sprint18.py` | `execution.adoption_engine` (flow) | `test_adoption_flow.py` |
| `test_sprint19.py` | `core.workflow_orchestrator` (v1) | `test_workflow_orchestrator_v1.py` |
| `test_sprint20.py` | `backtest.walk_forward` + `execution.runner_config` | `test_wfo_runner_config.py` |
| `test_sprint21.py` | `core.monitoring_watchdog` (integration) | `test_watchdog_integration.py` |
| `test_sprint22.py` | `execution.hedge_manager` + `notifications` | `test_hedge_notifications.py` |
| `test_smoke_s15_s17.py` | smoke: sprint 15-17 modules | `test_smoke_execution.py` |
| `test_smoke_s18.py` | smoke: sprint 18 modules | `test_smoke_adoption.py` |

## Strategia de redenumire (fără ștergere de teste)

1. **git mv** (nu copiere simplă) — păstrează history `git log --follow`:
   ```bash
   cd tests/
   git mv test_sprint10_allocator.py test_capital_allocator.py
   git mv test_sprint15_backtest.py test_backtest_engine.py
   git mv test_sprint16_api.py test_api_routes.py
   git mv test_sprint16_enhancements.py test_api_enhancements.py
   git mv test_sprint17.py test_multi_market_runner.py
   git mv test_sprint18.py test_adoption_flow.py
   git mv test_sprint19.py test_workflow_orchestrator_v1.py
   git mv test_sprint20.py test_wfo_runner_config.py
   git mv test_sprint21.py test_watchdog_integration.py
   git mv test_sprint22.py test_hedge_notifications.py
   git mv test_smoke_s15_s17.py test_smoke_execution.py
   git mv test_smoke_s18.py test_smoke_adoption.py
   git commit -m "refactor(#22): rename sprint-numbered tests to module-based names"
   ```

2. **CI** — după redenumire, `pytest tests/` continuă să găsească toate fișierele;
   nu e nevoie de configurare suplimentară în `pytest.ini`.

3. **Nu se șterg fișiere** — orice test din sprint files rămâne intact, doar
   filename-ul se schimbă.

## Status

- [x] Mapping documentat (acest fișier, sprint next)
- [ ] `git mv` executat per-sprint (următor commit pe branch)
- [ ] PR merge + issues #22 closed

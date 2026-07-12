"""
QuantLuna — Prometheus Metrics Endpoint
Sprint S35 (2026-07-12) — v1.0

Endpoint:
  GET /metrics  — format text Prometheus scrape-able

Metrici expuse:
  # Risk Dashboard (RiskDashboardEngine)
  quantluna_equity_usd               — equity curenta USD
  quantluna_rolling_sharpe           — Sharpe rolling (window 30)
  quantluna_drawdown_current         — drawdown curent (fractie)
  quantluna_drawdown_max             — drawdown maxim sesiune (fractie)
  quantluna_win_rate                 — win rate global
  quantluna_total_trades             — numar total trade-uri
  quantluna_exposure_usd             — expunere totala USD
  quantluna_exposure_pct             — expunere % din capital
  quantluna_net_pnl_usd              — PnL net USD
  quantluna_pair_exposure_usd{pair}  — expunere per pereche
  quantluna_pair_win_rate{pair}      — win rate per pereche
  quantluna_pair_net_pnl_usd{pair}   — PnL net per pereche

  # Sizing Engine (SizingEngine)
  quantluna_sizing_capital_usd       — capital configurata in sizer
  quantluna_n_reduced_pairs          — perechi cu factor < 1.0
  quantluna_pair_factor{pair}        — factor sizing per pereche

  # Watchdog (MonitoringWatchdog via api/watchdog)
  quantluna_watchdog_enabled         — 1 daca watchdog-ul ruleaza
  quantluna_watchdog_alerts_total    — numar total alerte emise
  quantluna_watchdog_halted_pairs    — perechi in stare HALT

  # Decision Engine (DecisionEngine v2.5)
  quantluna_decision_in_position     — 1 daca exista pozitie deschisa
  quantluna_decision_streak          — streak curent (pozitiv=win, negativ=loss)
  quantluna_decision_drawdown        — drawdown curent din DecisionEngine

Wiring (api/main.py):
  from api.metrics import metrics_router
  app.include_router(metrics_router)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from loguru import logger

metrics_router = APIRouter(tags=["metrics"])

# ---------------------------------------------------------------------------
# State injectabil — populat din api/main.py la lifespan startup
# ---------------------------------------------------------------------------
_METRICS_STATE: Dict[str, Any] = {
    "sizing_engine":  None,
    "watchdog":       None,
    "decision_engine": None,
}


def set_metrics_state(state: Dict[str, Any]) -> None:
    """Injectat din api/main.py la lifespan startup."""
    _METRICS_STATE.update(state or {})


# ---------------------------------------------------------------------------
# Helpers Prometheus text format
# ---------------------------------------------------------------------------

def _gauge(name: str, value: float, labels: Dict[str, str] | None = None, help_text: str = "") -> List[str]:
    """Genereaza linii Prometheus pentru un gauge."""
    lines = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_str}}} {value}")
    else:
        lines.append(f"{name} {value}")
    return lines


def _counter(name: str, value: float, labels: Dict[str, str] | None = None, help_text: str = "") -> List[str]:
    """Genereaza linii Prometheus pentru un counter."""
    lines = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} counter")
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_str}}} {value}")
    else:
        lines.append(f"{name} {value}")
    return lines


def _build_risk_metrics() -> List[str]:
    """Colecteaza metrici din RiskDashboardEngine via StateBus."""
    lines = []
    try:
        from core.state_bus import bus
        engine = bus.risk_engine
        snap = engine.snapshot()

        lines += _gauge("quantluna_equity_usd", snap.get("equity_usd", 0.0),
                        help_text="Equity curenta USD")
        lines += _gauge("quantluna_rolling_sharpe", snap.get("rolling_sharpe", 0.0),
                        help_text="Sharpe ratio rolling window 30")
        lines += _gauge("quantluna_drawdown_current", snap.get("current_dd", 0.0),
                        help_text="Drawdown curent fractie")
        lines += _gauge("quantluna_drawdown_max", snap.get("max_dd", 0.0),
                        help_text="Drawdown maxim sesiune fractie")
        lines += _gauge("quantluna_win_rate", snap.get("win_rate", 0.0),
                        help_text="Win rate global")
        lines += _counter("quantluna_total_trades", snap.get("total_trades", 0),
                          help_text="Total trade-uri inchise")
        lines += _gauge("quantluna_exposure_usd", snap.get("exposure_usd", 0.0),
                        help_text="Expunere totala USD")
        lines += _gauge("quantluna_exposure_pct", snap.get("exposure_pct", 0.0),
                        help_text="Expunere procent din capital")
        lines += _gauge("quantluna_net_pnl_usd", snap.get("net_pnl_usd", 0.0),
                        help_text="PnL net USD sesiune")

        # Per-pair metrici
        pairs_data = snap.get("pairs", {})
        lines.append("# HELP quantluna_pair_exposure_usd Expunere per pereche USD")
        lines.append("# TYPE quantluna_pair_exposure_usd gauge")
        for pair, ps in pairs_data.items():
            safe_pair = pair.replace("-", "_")
            lines.append(f'quantluna_pair_exposure_usd{{pair="{safe_pair}"}} {ps.get("exposure_usd", 0.0)}')

        lines.append("# HELP quantluna_pair_win_rate Win rate per pereche")
        lines.append("# TYPE quantluna_pair_win_rate gauge")
        for pair, ps in pairs_data.items():
            safe_pair = pair.replace("-", "_")
            lines.append(f'quantluna_pair_win_rate{{pair="{safe_pair}"}} {ps.get("win_rate", 0.0)}')

        lines.append("# HELP quantluna_pair_net_pnl_usd PnL net per pereche USD")
        lines.append("# TYPE quantluna_pair_net_pnl_usd gauge")
        for pair, ps in pairs_data.items():
            safe_pair = pair.replace("-", "_")
            lines.append(f'quantluna_pair_net_pnl_usd{{pair="{safe_pair}"}} {ps.get("net_pnl_usd", 0.0)}')

    except Exception as exc:
        logger.warning("metrics: RiskDashboardEngine indisponibil: {}", exc)
        lines += _gauge("quantluna_equity_usd", 0.0, help_text="Equity curenta USD (bot offline)")

    return lines


def _build_sizing_metrics() -> List[str]:
    """Colecteaza metrici din SizingEngine."""
    lines = []
    engine = _METRICS_STATE.get("sizing_engine")
    if engine is None:
        lines += _gauge("quantluna_sizing_capital_usd", 0.0,
                        help_text="Capital configurata SizingEngine USD")
        lines += _gauge("quantluna_n_reduced_pairs", 0.0,
                        help_text="Perechi cu factor sizing < 1.0")
        return lines

    try:
        status = engine.get_status()
        capital = status.get("capital_usdt", 0.0)
        pair_factors = status.get("pair_factors", {})
        n_reduced = status.get("n_reduced_pairs", 0)

        lines += _gauge("quantluna_sizing_capital_usd", capital,
                        help_text="Capital configurata SizingEngine USD")
        lines += _gauge("quantluna_n_reduced_pairs", n_reduced,
                        help_text="Perechi cu factor sizing < 1.0")

        lines.append("# HELP quantluna_pair_factor Factor sizing per pereche [0,1]")
        lines.append("# TYPE quantluna_pair_factor gauge")
        for pair, factor in pair_factors.items():
            safe_pair = pair.replace("-", "_")
            lines.append(f'quantluna_pair_factor{{pair="{safe_pair}"}} {factor}')

    except Exception as exc:
        logger.warning("metrics: SizingEngine.get_status() eroare: {}", exc)

    return lines


def _build_watchdog_metrics() -> List[str]:
    """Colecteaza metrici din MonitoringWatchdog."""
    lines = []
    watchdog = _METRICS_STATE.get("watchdog")

    enabled = 1.0 if watchdog is not None else 0.0
    lines += _gauge("quantluna_watchdog_enabled", enabled,
                    help_text="1 daca MonitoringWatchdog ruleaza")

    if watchdog is None:
        lines += _counter("quantluna_watchdog_alerts_total", 0.0,
                          help_text="Total alerte emise de watchdog")
        lines += _gauge("quantluna_watchdog_halted_pairs", 0.0,
                        help_text="Perechi in stare HALT")
        return lines

    try:
        alerts_total = getattr(watchdog, "_alerts_emitted", 0)
        halted = len(getattr(watchdog, "_halted_pairs", set()))

        lines += _counter("quantluna_watchdog_alerts_total", float(alerts_total),
                          help_text="Total alerte emise de watchdog")
        lines += _gauge("quantluna_watchdog_halted_pairs", float(halted),
                        help_text="Perechi in stare HALT")
    except Exception as exc:
        logger.warning("metrics: MonitoringWatchdog eroare: {}", exc)

    return lines


def _build_decision_metrics() -> List[str]:
    """Colecteaza metrici din DecisionEngine v2.5."""
    lines = []
    engine = _METRICS_STATE.get("decision_engine")

    in_position = 1.0 if (engine and getattr(engine, "_in_position", False)) else 0.0
    streak = float(getattr(engine, "_current_streak", 0)) if engine else 0.0
    dd = float(getattr(engine, "_current_dd", 0.0)) if engine else 0.0

    lines += _gauge("quantluna_decision_in_position", in_position,
                    help_text="1 daca exista pozitie deschisa")
    lines += _gauge("quantluna_decision_streak", streak,
                    help_text="Streak curent pozitiv=win negativ=loss")
    lines += _gauge("quantluna_decision_drawdown", dd,
                    help_text="Drawdown curent din DecisionEngine")

    return lines


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@metrics_router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """
    GET /metrics

    Prometheus scrape endpoint — text/plain format standard.
    Configureaza in prometheus.yml::

        scrape_configs:
          - job_name: quantluna
            static_configs:
              - targets: ['localhost:8000']

    Metrici expuse: risk (equity/sharpe/dd/win_rate/exposure),
    sizing (capital/factors), watchdog (enabled/alerts/halted),
    decision (position/streak/drawdown).
    """
    ts_ms = int(time.time() * 1000)
    all_lines: List[str] = []

    # Header
    all_lines.append(f"# QuantLuna Prometheus metrics — generated {ts_ms}ms")
    all_lines.append("")

    # Sectiune Risk
    all_lines.append("# === RISK DASHBOARD ===")
    all_lines.extend(_build_risk_metrics())
    all_lines.append("")

    # Sectiune Sizing
    all_lines.append("# === SIZING ENGINE ===")
    all_lines.extend(_build_sizing_metrics())
    all_lines.append("")

    # Sectiune Watchdog
    all_lines.append("# === MONITORING WATCHDOG ===")
    all_lines.extend(_build_watchdog_metrics())
    all_lines.append("")

    # Sectiune Decision
    all_lines.append("# === DECISION ENGINE ===")
    all_lines.extend(_build_decision_metrics())
    all_lines.append("")

    return PlainTextResponse(
        content="\n".join(all_lines),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

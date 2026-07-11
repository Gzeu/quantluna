"""
core/monitoring_watchdog.py  -  QuantLuna MonitoringWatchdog v1.0
Sprint S44 (2026-07-12)

Task asyncio care ruleaza la fiecare CHECK_INTERVAL (default 60s) si:
  1. Citeste metrici live din RiskManager per pereche
  2. Evalueaza fiecare pereche contra thresholds configurabile
  3. Declanseaza actiuni: HALT, REDUCE_SIZE, ALERT_ONLY
  4. Trimite alerta Telegram via AlertDispatcher
  5. Expune state pentru /api/watchdog/*

Thresholds per pereche (cu fallback la DEFAULT):
  sharpe_min     : Sharpe rolling 24h sub care alertam   (default 0.3)
  max_drawdown   : Drawdown % absolut maxim              (default 0.10 = 10%)
  z_max          : |z-score| maxim acceptat              (default 4.0)
  hl_max         : Half-life maxim (ore)                 (default 96)
  loss_streak    : Nr maxim tranzactii consecutive loss  (default 5)

Actiuni:
  ALERT_ONLY    : trimite notificare, nu opreste
  REDUCE_SIZE   : reduce sizing la 50% si alerta
  HALT          : opreste complet perechea si alerta CRITICAL

Variabile de mediu:
  WATCHDOG_CHECK_INTERVAL  : secunde intre verificari   (default 60)
  WATCHDOG_SHARPE_MIN      : global sharpe_min fallback  (default 0.3)
  WATCHDOG_MAX_DD          : global max_drawdown         (default 0.10)
  WATCHDOG_Z_MAX           : global z_max                (default 4.0)
  WATCHDOG_HL_MAX          : global hl_max ore           (default 96)
  WATCHDOG_ENABLED         : false => watchdog nu porneste
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


# ── Thresholds ────────────────────────────────────────────────────────────────

@dataclass
class PairThreshold:
    pair:          str
    sharpe_min:    float = 0.3
    max_drawdown:  float = 0.10    # fractie, ex: 0.10 = 10%
    z_max:         float = 4.0
    hl_max:        float = 96.0    # ore
    loss_streak:   int   = 5
    action:        str   = "ALERT_ONLY"  # ALERT_ONLY | REDUCE_SIZE | HALT
    silenced_until: Optional[datetime] = None

    def is_silenced(self) -> bool:
        if self.silenced_until is None:
            return False
        return datetime.now(timezone.utc) < self.silenced_until


@dataclass
class WatchdogAlert:
    timestamp:  str
    pair:       str
    metric:     str
    value:      float
    threshold:  float
    action:     str
    severity:   str   # INFO | WARNING | CRITICAL
    message:    str


# ── MonitoringWatchdog ──────────────────────────────────────────────────────────

class MonitoringWatchdog:
    """
    Task autonom de monitorizare. Se integreaza in WorkflowOrchestrator:

        coros.append(self._watchdog.run_loop())

    Necesita un `metrics_provider` callable async:
        async def get_metrics(pair: str) -> dict:
            return {
                "sharpe": 1.23, "drawdown": 0.05,
                "z_score": 1.8, "half_life": 24.0,
                "loss_streak": 2,
            }
    """

    MAX_HISTORY = 200

    def __init__(
        self,
        thresholds:       Dict[str, PairThreshold],
        metrics_provider: Callable[[str], Any],
        dispatcher:       Any,           # AlertDispatcher
        halt_callback:    Optional[Callable[[str], Any]] = None,
        reduce_callback:  Optional[Callable[[str, float], Any]] = None,
        check_interval:   int = 60,
    ):
        self._thresholds       = thresholds   # pair -> PairThreshold
        self._metrics_provider = metrics_provider
        self._dispatcher       = dispatcher
        self._halt_cb          = halt_callback
        self._reduce_cb        = reduce_callback
        self._check_interval   = check_interval
        self._running          = False
        self._alerts: List[WatchdogAlert] = []
        self._check_count      = 0
        self._last_check:      Optional[str] = None

    # ─ Lifecycle ─────────────────────────────────────────────────────────────────

    async def run_loop(self) -> None:
        self._running = True
        logger.info(
            f"[Watchdog] pornit | interval={self._check_interval}s "
            f"| perechi={list(self._thresholds.keys())}"
        )
        while self._running:
            try:
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[Watchdog] eroare in check_all: {exc}")
            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
        self._running = False
        logger.info("[Watchdog] oprit.")

    def stop(self) -> None:
        self._running = False

    # ─ Core check ───────────────────────────────────────────────────────────────────

    async def _check_all(self) -> None:
        self._check_count += 1
        self._last_check = datetime.now(timezone.utc).isoformat()

        for pair, thr in list(self._thresholds.items()):
            if thr.is_silenced():
                continue
            try:
                metrics = await self._metrics_provider(pair)
            except Exception as exc:
                logger.warning(f"[Watchdog] nu am putut obtine metrici {pair}: {exc}")
                continue
            await self._evaluate(pair, thr, metrics)

    async def _evaluate(
        self,
        pair:    str,
        thr:     PairThreshold,
        metrics: Dict[str, float],
    ) -> None:
        checks = [
            # (metric_key, value, threshold, violation_when, action_override)
            ("sharpe",      metrics.get("sharpe", 99.0),
             thr.sharpe_min,   lambda v, t: v < t,  None),
            ("drawdown",    metrics.get("drawdown", 0.0),
             thr.max_drawdown, lambda v, t: v > t,  "HALT"),
            ("z_score",     abs(metrics.get("z_score", 0.0)),
             thr.z_max,        lambda v, t: v > t,  "ALERT_ONLY"),
            ("half_life",   metrics.get("half_life", 0.0),
             thr.hl_max,       lambda v, t: v > t,  "ALERT_ONLY"),
            ("loss_streak", metrics.get("loss_streak", 0),
             float(thr.loss_streak), lambda v, t: v >= t, None),
        ]

        for metric, value, threshold, violated, action_override in checks:
            if not violated(value, threshold):
                continue

            action   = action_override or thr.action
            severity = self._severity(metric, action)
            message  = self._format_message(pair, metric, value, threshold, action, severity)

            alert = WatchdogAlert(
                timestamp=datetime.now(timezone.utc).isoformat(),
                pair=pair, metric=metric, value=value,
                threshold=threshold, action=action,
                severity=severity, message=message,
            )
            self._alerts.append(alert)
            if len(self._alerts) > self.MAX_HISTORY:
                self._alerts = self._alerts[-self.MAX_HISTORY:]

            logger.warning(f"[Watchdog] {severity} {pair} {metric}={value:.3f} (thr={threshold}) -> {action}")

            # Trimite Telegram
            await self._send_alert(alert)

            # Executa actiune
            await self._act(pair, action, metric, value)

    # ─ Actions ─────────────────────────────────────────────────────────────────────

    async def _act(
        self, pair: str, action: str, metric: str, value: float,
    ) -> None:
        if action == "HALT" and self._halt_cb is not None:
            try:
                await self._halt_cb(pair)
                logger.warning(f"[Watchdog] HALT executat pentru {pair}")
            except Exception as exc:
                logger.error(f"[Watchdog] HALT esuat {pair}: {exc}")

        elif action == "REDUCE_SIZE" and self._reduce_cb is not None:
            try:
                await self._reduce_cb(pair, 0.5)  # reduce la 50%
                logger.info(f"[Watchdog] REDUCE_SIZE 50% pentru {pair}")
            except Exception as exc:
                logger.error(f"[Watchdog] REDUCE_SIZE esuat {pair}: {exc}")

    # ─ Telegram ──────────────────────────────────────────────────────────────────────

    async def _send_alert(self, alert: WatchdogAlert) -> None:
        if self._dispatcher is None:
            return
        try:
            from notifications.event_types import AlertEvent, EventType
            sev_emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "CRITICAL": "🚨"}.get(alert.severity, "🔔")
            act_emoji = {"ALERT_ONLY": "🔔", "REDUCE_SIZE": "↓", "HALT": "🛑"}.get(alert.action, "")
            text = (
                f"{sev_emoji} <b>QuantLuna Watchdog</b> {act_emoji}\n"
                f"Pereche: <code>{alert.pair}</code>\n"
                f"Metric: <b>{alert.metric}</b> = {alert.value:.4f}\n"
                f"Threshold: {alert.threshold:.4f}\n"
                f"Actiune: <b>{alert.action}</b>\n"
                f"Severitate: {alert.severity}\n"
                f"<i>{alert.timestamp}</i>"
            )
            await self._dispatcher.emit(AlertEvent(
                event_type=EventType.RISK_ALERT,
                payload={"text": text, "pair": alert.pair, "metric": alert.metric},
            ))
        except Exception as exc:
            logger.error(f"[Watchdog] send_alert esuat: {exc}")

    # ─ Helpers ────────────────────────────────────────────────────────────────────

    @staticmethod
    def _severity(metric: str, action: str) -> str:
        if action == "HALT":
            return "CRITICAL"
        if metric in ("sharpe", "drawdown", "loss_streak"):
            return "WARNING"
        return "INFO"

    @staticmethod
    def _format_message(
        pair: str, metric: str, value: float,
        threshold: float, action: str, severity: str,
    ) -> str:
        direction = "sub" if metric in ("sharpe",) else "peste"
        return (
            f"[{severity}] {pair}: {metric}={value:.4f} "
            f"{direction} threshold {threshold:.4f} -> {action}"
        )

    # ─ Public API (pentru /api/watchdog) ──────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        return {
            "running":      self._running,
            "check_count":  self._check_count,
            "last_check":   self._last_check,
            "pairs_count":  len(self._thresholds),
            "alerts_total": len(self._alerts),
            "recent_alerts": [
                vars(a) for a in self._alerts[-10:]
            ],
        }

    def get_thresholds(self) -> Dict[str, Any]:
        return {
            pair: {
                "sharpe_min":   t.sharpe_min,
                "max_drawdown": t.max_drawdown,
                "z_max":        t.z_max,
                "hl_max":       t.hl_max,
                "loss_streak":  t.loss_streak,
                "action":       t.action,
                "silenced_until": t.silenced_until.isoformat()
                                  if t.silenced_until else None,
            }
            for pair, t in self._thresholds.items()
        }

    def update_threshold(self, pair: str, **kwargs: Any) -> None:
        if pair not in self._thresholds:
            self._thresholds[pair] = PairThreshold(pair=pair)
        thr = self._thresholds[pair]
        for k, v in kwargs.items():
            if hasattr(thr, k):
                setattr(thr, k, v)
        logger.info(f"[Watchdog] threshold actualizat {pair}: {kwargs}")

    def silence(self, pair: str, minutes: int = 60) -> None:
        from datetime import timedelta
        if pair not in self._thresholds:
            return
        self._thresholds[pair].silenced_until = (
            datetime.now(timezone.utc) + timedelta(minutes=minutes)
        )
        logger.info(f"[Watchdog] {pair} silentat {minutes}min")

    def get_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [vars(a) for a in self._alerts[-limit:]]

    # ─ Factory ────────────────────────────────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        pairs:            List[str],
        metrics_provider: Callable,
        dispatcher:       Any,
        halt_callback:    Optional[Callable] = None,
        reduce_callback:  Optional[Callable] = None,
        per_pair_cfg:     Optional[Dict[str, Dict]] = None,
    ) -> "MonitoringWatchdog":
        """
        Builder din env vars + optional config per pereche.

        per_pair_cfg = {
            "BTCUSDT-ETHUSDT": {"sharpe_min": 0.5, "action": "HALT"},
            "SOLUSDT-AVAXUSDT": {"max_drawdown": 0.08, "action": "REDUCE_SIZE"},
        }
        """
        if os.getenv("WATCHDOG_ENABLED", "true").lower() == "false":
            logger.info("[Watchdog] dezactivat via WATCHDOG_ENABLED=false")
            return cls(
                thresholds={}, metrics_provider=metrics_provider,
                dispatcher=dispatcher,
            )

        defaults = {
            "sharpe_min":  float(os.getenv("WATCHDOG_SHARPE_MIN", "0.3")),
            "max_drawdown": float(os.getenv("WATCHDOG_MAX_DD",     "0.10")),
            "z_max":        float(os.getenv("WATCHDOG_Z_MAX",      "4.0")),
            "hl_max":       float(os.getenv("WATCHDOG_HL_MAX",     "96")),
        }

        thresholds: Dict[str, PairThreshold] = {}
        for pair in pairs:
            cfg = {**defaults, **(per_pair_cfg or {}).get(pair, {})}
            thresholds[pair] = PairThreshold(pair=pair, **cfg)

        return cls(
            thresholds=thresholds,
            metrics_provider=metrics_provider,
            dispatcher=dispatcher,
            halt_callback=halt_callback,
            reduce_callback=reduce_callback,
            check_interval=int(os.getenv("WATCHDOG_CHECK_INTERVAL", "60")),
        )

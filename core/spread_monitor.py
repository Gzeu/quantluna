"""
QuantLuna — Spread Monitor (Sprint 18)

Real-time monitor for pair spread health. Runs as an async task alongside
the live trader and emits alerts when the spread shows signs of:
  - Cointegration breakdown (spread drifting beyond control limits)
  - Half-life regime change (mean-reversion slowing significantly)
  - Kalman divergence (filter uncertainty growing unbounded)
  - Spread stuck near entry (position not resolving — opportunity cost)

Design:
  - Stateless per-bar evaluation: feed a new bar, get a HealthReport
  - All thresholds configurable via SpreadMonitorConfig
  - Async alert callbacks: plug in NotifierBus, CircuitBreaker, etc.

Usage:
    monitor = SpreadMonitor(SpreadMonitorConfig())
    monitor.on_alert(cb=circuit_breaker.trip_manual)
    report = monitor.update(spread_value, kalman_uncertainty, half_life)
    if not report.healthy:
        await notifier.send_alert(report.summary)
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, List, Optional

from loguru import logger


class AlertType(str, Enum):
    SPREAD_DRIFT        = "spread_drift"         # |z-score| > control limit
    HALFLIFE_SLOW       = "halflife_slow"         # half-life > max threshold
    KALMAN_DIVERGENCE   = "kalman_divergence"     # P diagonal > threshold
    STUCK_POSITION      = "stuck_position"        # spread not crossing zero in N bars
    COINTEGRATION_BREAK = "cointegration_break"   # consecutive extreme bars


@dataclass
class SpreadAlert:
    alert_type:  AlertType
    value:       float
    threshold:   float
    description: str
    timestamp:   float = field(default_factory=time.time)


@dataclass
class SpreadHealthReport:
    healthy:        bool
    alerts:         List[SpreadAlert] = field(default_factory=list)
    zscore:         float = 0.0
    half_life:      float = 0.0
    kalman_p_diag:  float = 0.0
    bars_since_cross: int = 0
    timestamp:      float = field(default_factory=time.time)

    @property
    def summary(self) -> str:
        if self.healthy:
            return f"Spread healthy | z={self.zscore:.3f} hl={self.half_life:.1f}h"
        types = ", ".join(a.alert_type.value for a in self.alerts)
        return f"Spread UNHEALTHY [{types}] | z={self.zscore:.3f} hl={self.half_life:.1f}h"


@dataclass
class SpreadMonitorConfig:
    # Z-score control limits (beyond = drift alert)
    zscore_control_limit: float = 3.5

    # Half-life slow regime (hours)
    max_half_life_hours: float = 96.0

    # Kalman P[0,0] divergence threshold (hedge ratio uncertainty)
    kalman_p_divergence: float = 0.5

    # Bars without zero-cross before "stuck" alert
    stuck_bars_threshold: int = 48

    # Consecutive bars outside control limit before cointegration-break alert
    cointegration_break_bars: int = 6

    # Rolling window for z-score stats
    zscore_window: int = 200

    # Minimum bars before alerts fire (warm-up)
    min_bars: int = 30


class SpreadMonitor:
    """
    Monitors spread health bar-by-bar and fires alerts.

    Parameters
    ----------
    cfg : SpreadMonitorConfig
    """

    def __init__(self, cfg: Optional[SpreadMonitorConfig] = None) -> None:
        self.cfg = cfg or SpreadMonitorConfig()
        self._spread_history: Deque[float] = deque(maxlen=self.cfg.zscore_window)
        self._bars_since_cross: int = 0
        self._consecutive_extreme: int = 0
        self._bar_count: int = 0
        self._alert_callbacks: List[Callable] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_alert(self, cb: Callable) -> None:
        """Register a callback to be called with SpreadAlert on each alert."""
        self._alert_callbacks.append(cb)

    def update(
        self,
        spread: float,
        zscore: float,
        half_life: float,
        kalman_p_diag: float = 0.0,
    ) -> SpreadHealthReport:
        """
        Process one bar and return a health report.

        Parameters
        ----------
        spread        : raw spread value
        zscore        : current spread z-score
        half_life     : estimated half-life in hours
        kalman_p_diag : Kalman hedge-ratio variance P[0,0]
        """
        self._bar_count += 1
        self._spread_history.append(spread)
        self._update_cross_counter(spread)

        alerts: List[SpreadAlert] = []

        if self._bar_count < self.cfg.min_bars:
            return SpreadHealthReport(
                healthy=True,
                zscore=zscore,
                half_life=half_life,
                kalman_p_diag=kalman_p_diag,
                bars_since_cross=self._bars_since_cross,
            )

        cfg = self.cfg

        # 1. Z-score drift
        abs_z = abs(zscore)
        if abs_z > cfg.zscore_control_limit:
            self._consecutive_extreme += 1
            alerts.append(SpreadAlert(
                alert_type=AlertType.SPREAD_DRIFT,
                value=abs_z,
                threshold=cfg.zscore_control_limit,
                description=f"|z|={abs_z:.3f} > control limit {cfg.zscore_control_limit}",
            ))
        else:
            self._consecutive_extreme = 0

        # 2. Cointegration break (too many consecutive extreme bars)
        if self._consecutive_extreme >= cfg.cointegration_break_bars:
            alerts.append(SpreadAlert(
                alert_type=AlertType.COINTEGRATION_BREAK,
                value=float(self._consecutive_extreme),
                threshold=float(cfg.cointegration_break_bars),
                description=(
                    f"{self._consecutive_extreme} consecutive bars |z| > "
                    f"{cfg.zscore_control_limit}"
                ),
            ))

        # 3. Half-life too slow
        if half_life > cfg.max_half_life_hours:
            alerts.append(SpreadAlert(
                alert_type=AlertType.HALFLIFE_SLOW,
                value=half_life,
                threshold=cfg.max_half_life_hours,
                description=f"half_life={half_life:.1f}h > max {cfg.max_half_life_hours}h",
            ))

        # 4. Kalman divergence
        if kalman_p_diag > cfg.kalman_p_divergence:
            alerts.append(SpreadAlert(
                alert_type=AlertType.KALMAN_DIVERGENCE,
                value=kalman_p_diag,
                threshold=cfg.kalman_p_divergence,
                description=f"Kalman P[0,0]={kalman_p_diag:.4f} > {cfg.kalman_p_divergence}",
            ))

        # 5. Stuck position
        if self._bars_since_cross >= cfg.stuck_bars_threshold:
            alerts.append(SpreadAlert(
                alert_type=AlertType.STUCK_POSITION,
                value=float(self._bars_since_cross),
                threshold=float(cfg.stuck_bars_threshold),
                description=(
                    f"Spread has not crossed zero in {self._bars_since_cross} bars "
                    f"(threshold={cfg.stuck_bars_threshold})"
                ),
            ))

        healthy = len(alerts) == 0

        if not healthy:
            for alert in alerts:
                logger.warning(f"SpreadMonitor: {alert.description}")
                for cb in self._alert_callbacks:
                    try:
                        cb(alert)
                    except Exception as exc:
                        logger.error(f"SpreadMonitor: alert callback error: {exc}")

        return SpreadHealthReport(
            healthy=healthy,
            alerts=alerts,
            zscore=zscore,
            half_life=half_life,
            kalman_p_diag=kalman_p_diag,
            bars_since_cross=self._bars_since_cross,
        )

    def reset(self) -> None:
        """Full reset — clears all counters."""
        self._spread_history.clear()
        self._bars_since_cross = 0
        self._consecutive_extreme = 0
        self._bar_count = 0

    @property
    def bar_count(self) -> int:
        return self._bar_count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_cross_counter(self, spread: float) -> None:
        """Update bars-since-zero-cross counter."""
        history = list(self._spread_history)
        if len(history) < 2:
            return
        prev = history[-2]
        curr = history[-1]
        # Zero-cross: sign change
        if (prev < 0 and curr >= 0) or (prev >= 0 and curr < 0) or curr == 0:
            self._bars_since_cross = 0
        else:
            self._bars_since_cross += 1

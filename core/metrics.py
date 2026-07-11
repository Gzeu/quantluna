"""
core/metrics.py — lightweight Prometheus-style metrics registry for QuantLuna.
Sprint S20 — 2026-07-11

Changelog S20:
  - Gauges noi: quantluna_zscore_pair, quantluna_circuit_breaker_open,
    quantluna_warmup_bars_done, quantluna_vol_regime_high
  - LabeledGauge helper pentru metrici per-pair
  - render_prometheus() suportă labeled gauges

Features originale:
  - Counter, Gauge, Summary — zero dependință externă
  - Prometheus text exposition format
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional


@dataclass
class Counter:
    name: str
    description: str
    value: float = 0.0

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount


@dataclass
class Gauge:
    name: str
    description: str
    value: float = 0.0

    def set(self, value: float) -> None:
        self.value = value


@dataclass
class LabeledGauge:
    """Gauge cu label-uri, ex: quantluna_zscore{pair='BTCUSDT_ETHUSDT'}."""
    name: str
    description: str
    _values: Dict[str, float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = "_".join(f"{k}={v}" for k, v in sorted((labels or {}).items()))
        with self._lock:
            self._values[key] = float(value)

    def render_lines(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            for key, val in self._values.items():
                # key format: "pair=BTCUSDT_ETHUSDT"
                label_str = "{"
                for part in key.split("_"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        label_str += f'{k}="{v}",'
                label_str = label_str.rstrip(",") + "}"
                lines.append(f"{self.name}{label_str if label_str != '{}' else ''} {val}")
        return lines


@dataclass
class Summary:
    name: str
    description: str
    observations: List[float] = field(default_factory=list)

    def observe(self, value: float) -> None:
        self.observations.append(float(value))
        if len(self.observations) > 10_000:
            self.observations = self.observations[-5_000:]

    @property
    def count(self) -> int:
        return len(self.observations)

    @property
    def sum(self) -> float:
        return float(sum(self.observations))


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._labeled_gauges: Dict[str, LabeledGauge] = {}
        self._summaries: Dict[str, Summary] = {}

    def counter(self, name: str, description: str) -> Counter:
        with self._lock:
            return self._counters.setdefault(name, Counter(name=name, description=description))

    def gauge(self, name: str, description: str) -> Gauge:
        with self._lock:
            return self._gauges.setdefault(name, Gauge(name=name, description=description))

    def labeled_gauge(self, name: str, description: str) -> LabeledGauge:
        with self._lock:
            return self._labeled_gauges.setdefault(
                name, LabeledGauge(name=name, description=description)
            )

    def summary(self, name: str, description: str) -> Summary:
        with self._lock:
            return self._summaries.setdefault(name, Summary(name=name, description=description))

    def render_prometheus(self) -> str:
        lines: List[str] = []
        with self._lock:
            counters = list(self._counters.values())
            gauges = list(self._gauges.values())
            labeled = list(self._labeled_gauges.values())
            summaries = list(self._summaries.values())

        for metric in counters:
            lines += [
                f"# HELP {metric.name} {metric.description}",
                f"# TYPE {metric.name} counter",
                f"{metric.name} {metric.value}",
            ]
        for metric in gauges:
            lines += [
                f"# HELP {metric.name} {metric.description}",
                f"# TYPE {metric.name} gauge",
                f"{metric.name} {metric.value}",
            ]
        for metric in labeled:
            lines += metric.render_lines()
        for metric in summaries:
            lines += [
                f"# HELP {metric.name} {metric.description}",
                f"# TYPE {metric.name} summary",
                f"{metric.name}_count {metric.count}",
                f"{metric.name}_sum {metric.sum}",
            ]
        return "\n".join(lines) + "\n"


registry = MetricsRegistry()

# Metrici originale S13
trades_total         = registry.counter("quantluna_trades_total", "Total executed trades")
orders_total         = registry.counter("quantluna_orders_total", "Total submitted orders")
api_errors_total     = registry.counter("quantluna_api_errors_total", "Total API errors")
active_positions     = registry.gauge("quantluna_active_positions", "Current active positions")
pnl_usdt             = registry.gauge("quantluna_pnl_usdt", "Current realized PnL in USDT")
drawdown_pct         = registry.gauge("quantluna_drawdown_pct", "Current drawdown percentage")
spread_zscore        = registry.gauge("quantluna_spread_zscore", "Latest spread z-score")
order_latency_ms     = registry.summary("quantluna_order_latency_ms", "Order placement latency ms")
websocket_clients    = registry.gauge("quantluna_websocket_clients", "Connected WS clients")
funding_rate_bps     = registry.gauge("quantluna_funding_rate_bps", "Funding rate in basis points")

# Metrici noi S20
zscore_pair          = registry.labeled_gauge("quantluna_zscore", "Z-score per trading pair")
circuit_breaker_open = registry.gauge("quantluna_circuit_breaker_open", "Circuit breaker open 0/1")
warmup_bars_done     = registry.gauge("quantluna_warmup_bars_done", "Warm-up bars completed")
vol_regime_high      = registry.gauge("quantluna_vol_regime_high", "Volatility regime HIGH=1")
active_strategy_info = registry.labeled_gauge("quantluna_active_strategy", "Active strategy (1=active)")

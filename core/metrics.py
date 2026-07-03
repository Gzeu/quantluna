"""
core/metrics.py — lightweight Prometheus-style metrics registry for QuantLuna.

Features:
- Counter, Gauge, Histogram-like summaries (simple)
- Zero external dependency
- Render in Prometheus text exposition format
- Safe defaults for dashboard and trading loops
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List


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
        self._summaries: Dict[str, Summary] = {}

    def counter(self, name: str, description: str) -> Counter:
        with self._lock:
            return self._counters.setdefault(name, Counter(name=name, description=description))

    def gauge(self, name: str, description: str) -> Gauge:
        with self._lock:
            return self._gauges.setdefault(name, Gauge(name=name, description=description))

    def summary(self, name: str, description: str) -> Summary:
        with self._lock:
            return self._summaries.setdefault(name, Summary(name=name, description=description))

    def render_prometheus(self) -> str:
        lines: List[str] = []
        with self._lock:
            for metric in self._counters.values():
                lines.append(f"# HELP {metric.name} {metric.description}")
                lines.append(f"# TYPE {metric.name} counter")
                lines.append(f"{metric.name} {metric.value}")
            for metric in self._gauges.values():
                lines.append(f"# HELP {metric.name} {metric.description}")
                lines.append(f"# TYPE {metric.name} gauge")
                lines.append(f"{metric.name} {metric.value}")
            for metric in self._summaries.values():
                lines.append(f"# HELP {metric.name} {metric.description}")
                lines.append(f"# TYPE {metric.name} summary")
                lines.append(f"{metric.name}_count {metric.count}")
                lines.append(f"{metric.name}_sum {metric.sum}")
        return "\n".join(lines) + "\n"


registry = MetricsRegistry()

trades_total = registry.counter("quantluna_trades_total", "Total executed trades")
orders_total = registry.counter("quantluna_orders_total", "Total submitted orders")
api_errors_total = registry.counter("quantluna_api_errors_total", "Total API errors")
active_positions = registry.gauge("quantluna_active_positions", "Current active positions")
pnl_usdt = registry.gauge("quantluna_pnl_usdt", "Current realized or tracked pnl in USDT")
drawdown_pct = registry.gauge("quantluna_drawdown_pct", "Current drawdown percentage")
spread_zscore = registry.gauge("quantluna_spread_zscore", "Latest spread z-score")
order_latency_ms = registry.summary("quantluna_order_latency_ms", "Order placement latency in milliseconds")
websocket_clients = registry.gauge("quantluna_websocket_clients", "Connected dashboard websocket clients")
funding_rate_bps = registry.gauge("quantluna_funding_rate_bps", "Funding rate in basis points")

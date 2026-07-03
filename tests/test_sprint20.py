"""
Sprint 20 tests — metrics, funding rate, trade journal.
"""
from __future__ import annotations

from pathlib import Path

from core.funding_rate import FundingRateMonitor
from core.metrics import registry
from core.trade_journal import TradeJournal


def test_metrics_registry_renders_prometheus_text():
    gauge = registry.gauge("test_metric_value", "Example metric")
    gauge.set(12.5)
    text = registry.render_prometheus()
    assert "# HELP test_metric_value Example metric" in text
    assert "test_metric_value 12.5" in text


def test_funding_rate_monitor_classifies_expensive():
    mon = FundingRateMonitor(expensive_threshold_bps=5.0)
    snap = mon.classify("BTCUSDT", funding_rate=0.0008)
    assert snap.regime == "expensive"
    assert snap.expensive is True
    assert mon.should_block_entry(0.0008) is True


def test_trade_journal_append_and_read(tmp_path: Path):
    p = tmp_path / "journal.csv"
    journal = TradeJournal(str(p))
    journal.append_simple(pair="BTCUSDT/ETHUSDT", side="LONG_SPREAD", pnl_usdt=42.5, reason="tp")
    rows = journal.read_all()
    assert len(rows) == 1
    assert rows[0].pnl_usdt == 42.5
    assert rows[0].reason == "tp"

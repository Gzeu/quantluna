"""
Module: tests/test_report_builder.py
Sprint: 31 — S (Strategy Backtesting Report)
Description:
    8 pytest tests for ReportBuilder and ChartBuilder.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from reporting.report_builder import ReportBuilder
from reporting.chart_builder import ChartBuilder


@pytest.fixture
def sample_result() -> dict:
    return {
        "metrics": {
            "total_return_pct": 42.5,
            "sharpe": 1.87,
            "max_drawdown_pct": -12.3,
            "win_rate_pct": 58.2,
            "total_trades": 120,
            "profit_factor": 1.65,
            "sortino": 2.1,
            "calmar": 3.4,
            "var_95_pct": -1.2,
            "cvar_95_pct": -1.8,
            "recovery_time_bars": 45,
        },
        "equity_curve": [10000 + i * 10 for i in range(200)],
        "trades": [
            {"symbol": "BTCUSDT", "side": "Long", "entry_price": 50000,
             "exit_price": 51000, "pnl": 100.0, "duration_h": 4.5,
             "exit_time": "2024-01-15T10:00:00"}
        ] * 5,
        "regime_metrics": {
            "trending": {"trades": 40, "win_rate_pct": 62.0, "sharpe": 2.1, "avg_pnl": 80.0},
            "ranging": {"trades": 55, "win_rate_pct": 55.0, "sharpe": 1.5, "avg_pnl": 50.0},
            "volatile": {"trades": 25, "win_rate_pct": 48.0, "sharpe": 0.9, "avg_pnl": 20.0},
        },
        "regime_counts": {"trending": 40, "ranging": 55, "volatile": 25},
        "walk_forward": [
            {"is_sharpe": 2.0, "oos_sharpe": 1.5, "oos_return_pct": 8.2},
            {"is_sharpe": 1.8, "oos_sharpe": 1.3, "oos_return_pct": 6.1},
        ],
        "buy_hold": {"total_return_pct": 30.0},
    }


class TestReportBuilder:
    def test_build_html_returns_string(self, sample_result) -> None:
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value="FAKEBASE64"):
            rb = ReportBuilder(sample_result, report_id="test001")
            html = rb.build_html()
        assert isinstance(html, str)
        assert "QuantLuna Backtest Report" in html

    def test_html_contains_summary_metrics(self, sample_result) -> None:
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value=""):
            rb = ReportBuilder(sample_result, report_id="test002")
            html = rb.build_html()
        assert "42.50%" in html  # total return
        assert "1.8700" in html  # sharpe

    def test_html_contains_trade_table(self, sample_result) -> None:
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value=""):
            rb = ReportBuilder(sample_result, report_id="test003")
            html = rb.build_html()
        assert "BTCUSDT" in html
        assert "Trade List" in html

    def test_html_contains_regime_section(self, sample_result) -> None:
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value=""):
            rb = ReportBuilder(sample_result, report_id="test004")
            html = rb.build_html()
        assert "trending" in html
        assert "ranging" in html

    def test_html_contains_walk_forward(self, sample_result) -> None:
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value=""):
            rb = ReportBuilder(sample_result, report_id="test005")
            html = rb.build_html()
        assert "Walk-Forward" in html

    def test_html_contains_buy_hold(self, sample_result) -> None:
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value=""):
            rb = ReportBuilder(sample_result, report_id="test006")
            html = rb.build_html()
        assert "Buy" in html and "Hold" in html

    def test_save_creates_file(self, sample_result, tmp_path) -> None:
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value=""):
            rb = ReportBuilder(sample_result, report_id="test007")
            out = str(tmp_path / "report.html")
            rb.save(out)
        import os
        assert os.path.exists(out)

    def test_empty_trades_graceful(self) -> None:
        result = {"metrics": {}, "equity_curve": [], "trades": []}
        with patch("reporting.chart_builder.ChartBuilder._to_base64", return_value=""):
            rb = ReportBuilder(result, report_id="test008")
            html = rb.build_html()
        assert "No trades" in html

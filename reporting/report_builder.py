"""
Module: reporting/report_builder.py
Sprint: 31 — S (Strategy Backtesting Report)
Description:
    Generates a self-contained HTML (and optional PDF via WeasyPrint)
    report from a BacktestResult dictionary.  Sections include:
      - Executive Summary (key metrics)
      - Equity Curve (embedded Plotly PNG)
      - Trade List (paginated HTML table)
      - Metrics per Regime (trending/ranging/volatile)
      - Walk-Forward Analysis
      - Buy & Hold Comparison
      - Risk Metrics (Sharpe, Sortino, Calmar, VaR, CVaR, MaxDD)

Usage:
    rb = ReportBuilder(backtest_result, report_id="abc123")
    html = rb.build_html()
    rb.save("reports/abc123.html")
    # optionally:
    rb.save_pdf("reports/abc123.pdf")  # requires WeasyPrint
"""

from __future__ import annotations

import html
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from reporting.chart_builder import ChartBuilder

logger = logging.getLogger(__name__)


CHART_TAG = '<img src="data:image/png;base64,{b64}" style="max-width:100%;margin:12px 0;"/>'

CSS = """
body{font-family:system-ui,sans-serif;background:#0f1117;color:#e0e0e0;margin:0;padding:20px}
h1{color:#00d4ff}h2{color:#a0c4ff;border-bottom:1px solid #333;padding-bottom:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#1e2230;padding:8px;text-align:left;color:#a0c4ff}
td{padding:6px 8px;border-bottom:1px solid #1e2230}
.pos{color:#4caf50}.neg{color:#f44336}
.metric-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin:16px 0}
.metric-card{background:#1e2230;border-radius:8px;padding:14px}
.metric-card .val{font-size:22px;font-weight:bold;color:#00d4ff}
.metric-card .label{font-size:11px;color:#888;margin-top:4px}
"""


class ReportBuilder:
    """Build a self-contained HTML backtest report."""

    def __init__(self, result: dict[str, Any], report_id: str = "") -> None:
        self.result = result
        self.report_id = report_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.charts = ChartBuilder(result).build_all()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_html(self) -> str:
        """Render full HTML report as string."""
        sections = [
            self._section_summary(),
            self._section_equity(),
            self._section_trades(),
            self._section_regime_metrics(),
            self._section_walk_forward(),
            self._section_buy_hold(),
            self._section_risk_metrics(),
        ]
        body = "\n".join(sections)
        title = f"QuantLuna Backtest Report — {self.report_id}"
        return (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>{CSS}</style></head>"
            f"<body><h1>{title}</h1>{body}</body></html>"
        )

    def save(self, path: str) -> None:
        """Save HTML report to disk."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.build_html())
        logger.info("[REPORT_BUILDER] HTML saved: %s", path)

    def save_pdf(self, path: str) -> None:
        """Save PDF report via WeasyPrint (optional dependency)."""
        try:
            from weasyprint import HTML  # type: ignore
        except ImportError as exc:
            raise RuntimeError("WeasyPrint not installed. Run: pip install weasyprint") from exc
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        HTML(string=self.build_html()).write_pdf(path)
        logger.info("[REPORT_BUILDER] PDF saved: %s", path)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _section_summary(self) -> str:
        m = self.result.get("metrics", {})
        cards = [
            ("Total Return", f"{m.get('total_return_pct', 0):.2f}%"),
            ("Sharpe Ratio", f"{m.get('sharpe', 0):.3f}"),
            ("Max Drawdown", f"{m.get('max_drawdown_pct', 0):.2f}%"),
            ("Win Rate", f"{m.get('win_rate_pct', 0):.1f}%"),
            ("Total Trades", str(m.get('total_trades', 0))),
            ("Profit Factor", f"{m.get('profit_factor', 0):.3f}"),
        ]
        grid = "".join(
            f'<div class="metric-card"><div class="val">{v}</div><div class="label">{k}</div></div>'
            for k, v in cards
        )
        return f"<h2>Executive Summary</h2><div class='metric-grid'>{grid}</div>"

    def _section_equity(self) -> str:
        img = CHART_TAG.format(b64=self.charts.get("equity_curve", ""))
        dd_img = CHART_TAG.format(b64=self.charts.get("drawdown_curve", ""))
        return f"<h2>Equity Curve</h2>{img}{dd_img}"

    def _section_trades(self) -> str:
        trades = self.result.get("trades", [])[:200]  # cap at 200 rows
        if not trades:
            return "<h2>Trade List</h2><p>No trades.</p>"
        headers = ["#", "Symbol", "Side", "Entry", "Exit", "PnL", "Duration (h)"]
        th_row = "".join(f"<th>{h}</th>" for h in headers)
        rows = ""
        for i, t in enumerate(trades, 1):
            pnl = t.get("pnl", 0)
            cls = "pos" if pnl >= 0 else "neg"
            rows += (
                f"<tr><td>{i}</td><td>{html.escape(str(t.get('symbol','')))}</td>"
                f"<td>{t.get('side','')}</td>"
                f"<td>{t.get('entry_price',0):.4f}</td>"
                f"<td>{t.get('exit_price',0):.4f}</td>"
                f"<td class='{cls}'>{pnl:.4f}</td>"
                f"<td>{t.get('duration_h',0):.1f}</td></tr>"
            )
        return (
            f"<h2>Trade List ({len(self.result.get('trades', []))} total)</h2>"
            f"<table><thead><tr>{th_row}</tr></thead><tbody>{rows}</tbody></table>"
        )

    def _section_regime_metrics(self) -> str:
        rm = self.result.get("regime_metrics", {})
        pie = CHART_TAG.format(b64=self.charts.get("regime_breakdown_pie", ""))
        if not rm:
            return f"<h2>Metrics per Regime</h2>{pie}<p>No regime data.</p>"
        headers = ["Regime", "Trades", "Win Rate", "Sharpe", "Avg PnL"]
        th_row = "".join(f"<th>{h}</th>" for h in headers)
        rows = ""
        for regime, stats in rm.items():
            rows += (
                f"<tr><td>{html.escape(regime)}</td>"
                f"<td>{stats.get('trades',0)}</td>"
                f"<td>{stats.get('win_rate_pct',0):.1f}%</td>"
                f"<td>{stats.get('sharpe',0):.3f}</td>"
                f"<td>{stats.get('avg_pnl',0):.4f}</td></tr>"
            )
        table = f"<table><thead><tr>{th_row}</tr></thead><tbody>{rows}</tbody></table>"
        return f"<h2>Metrics per Regime</h2>{pie}{table}"

    def _section_walk_forward(self) -> str:
        wf = self.result.get("walk_forward", [])
        if not wf:
            return "<h2>Walk-Forward Analysis</h2><p>No walk-forward data.</p>"
        headers = ["Fold", "In-Sample Sharpe", "Out-Sample Sharpe", "Return"]
        th_row = "".join(f"<th>{h}</th>" for h in headers)
        rows = "".join(
            f"<tr><td>{i+1}</td><td>{f.get('is_sharpe',0):.3f}</td>"
            f"<td>{f.get('oos_sharpe',0):.3f}</td>"
            f"<td>{f.get('oos_return_pct',0):.2f}%</td></tr>"
            for i, f in enumerate(wf)
        )
        return (
            f"<h2>Walk-Forward Analysis</h2>"
            f"<table><thead><tr>{th_row}</tr></thead><tbody>{rows}</tbody></table>"
        )

    def _section_buy_hold(self) -> str:
        bh = self.result.get("buy_hold", {})
        strat_ret = self.result.get("metrics", {}).get("total_return_pct", 0)
        bh_ret = bh.get("total_return_pct", 0)
        cmp = (
            f"<p>Strategy: <b>{strat_ret:.2f}%</b> vs "
            f"Buy &amp; Hold: <b>{bh_ret:.2f}%</b></p>"
        )
        return f"<h2>Buy &amp; Hold Comparison</h2>{cmp}"

    def _section_risk_metrics(self) -> str:
        m = self.result.get("metrics", {})
        cards = [
            ("Sharpe", f"{m.get('sharpe', 0):.4f}"),
            ("Sortino", f"{m.get('sortino', 0):.4f}"),
            ("Calmar", f"{m.get('calmar', 0):.4f}"),
            ("VaR 95%", f"{m.get('var_95_pct', 0):.3f}%"),
            ("CVaR 95%", f"{m.get('cvar_95_pct', 0):.3f}%"),
            ("Max DD", f"{m.get('max_drawdown_pct', 0):.3f}%"),
            ("Recovery Time", f"{m.get('recovery_time_bars', 0)} bars"),
        ]
        grid = "".join(
            f'<div class="metric-card"><div class="val">{v}</div><div class="label">{k}</div></div>'
            for k, v in cards
        )
        return f"<h2>Risk Metrics</h2><div class='metric-grid'>{grid}</div>"

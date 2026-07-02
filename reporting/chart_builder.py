"""
Module: reporting/chart_builder.py
Sprint: 31 — S (Strategy Backtesting Report)
Description:
    Generates Plotly charts from backtest result data and encodes
    them as base64 PNG strings for embedding into self-contained
    HTML reports.  Charts produced:
      - equity_curve
      - drawdown_curve
      - monthly_returns_heatmap
      - trade_duration_histogram
      - win_loss_distribution
      - regime_breakdown_pie

Usage:
    cb = ChartBuilder(backtest_result)
    charts = cb.build_all()  # -> dict[str, str]  (name -> base64 PNG)
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

logger = logging.getLogger(__name__)


class ChartBuilder:
    """Build Plotly charts from BacktestResult and export as base64 PNG."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self._equity: list[float] = result.get("equity_curve", [])
        self._trades: list[dict[str, Any]] = result.get("trades", [])
        self._regimes: dict[str, int] = result.get("regime_counts", {})

    def build_all(self) -> dict[str, str]:
        """Build all charts and return dict of name -> base64 PNG."""
        builders = [
            ("equity_curve", self._equity_curve),
            ("drawdown_curve", self._drawdown_curve),
            ("monthly_returns_heatmap", self._monthly_heatmap),
            ("trade_duration_histogram", self._duration_histogram),
            ("win_loss_distribution", self._win_loss_distribution),
            ("regime_breakdown_pie", self._regime_pie),
        ]
        charts: dict[str, str] = {}
        for name, fn in builders:
            try:
                charts[name] = fn()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[CHART_BUILDER] Failed to build %s: %s", name, exc)
                charts[name] = ""
        return charts

    # ------------------------------------------------------------------
    # Individual chart builders
    # ------------------------------------------------------------------

    def _equity_curve(self) -> str:
        eq = self._equity
        fig = go.Figure()
        fig.add_trace(go.Scatter(y=eq, mode="lines", name="Equity", line={"color": "#00d4ff"}))
        fig.update_layout(
            title="Equity Curve",
            xaxis_title="Bar",
            yaxis_title="USD",
            template="plotly_dark",
            height=350,
        )
        return self._to_base64(fig)

    def _drawdown_curve(self) -> str:
        eq = pd.Series(self._equity, dtype=float)
        rolling_max = eq.cummax()
        dd = (eq - rolling_max) / rolling_max.replace(0, float("nan"))
        fig = go.Figure()
        fig.add_trace(go.Scatter(y=dd.tolist(), mode="lines", name="Drawdown",
                                  fill="tozeroy", line={"color": "#ff4444"}))
        fig.update_layout(title="Drawdown", xaxis_title="Bar",
                          yaxis_title="%", template="plotly_dark", height=300)
        return self._to_base64(fig)

    def _monthly_heatmap(self) -> str:
        trades = self._trades
        if not trades:
            fig = go.Figure()
            fig.update_layout(title="Monthly Returns (no data)", template="plotly_dark")
            return self._to_base64(fig)
        df = pd.DataFrame(trades)
        df["exit_time"] = pd.to_datetime(df.get("exit_time", pd.Series(dtype=str)))
        df["month"] = df["exit_time"].dt.to_period("M").astype(str)
        monthly = df.groupby("month")["pnl"].sum().reset_index()
        fig = px.bar(monthly, x="month", y="pnl", title="Monthly PnL",
                     color="pnl", color_continuous_scale="RdYlGn", template="plotly_dark")
        return self._to_base64(fig)

    def _duration_histogram(self) -> str:
        durations = [t.get("duration_h", 0) for t in self._trades]
        fig = px.histogram(x=durations, nbins=30, title="Trade Duration (hours)",
                           labels={"x": "Hours"}, template="plotly_dark")
        return self._to_base64(fig)

    def _win_loss_distribution(self) -> str:
        pnls = [t.get("pnl", 0) for t in self._trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        fig = go.Figure()
        fig.add_trace(go.Histogram(x=wins, name="Win", marker_color="green", opacity=0.7))
        fig.add_trace(go.Histogram(x=losses, name="Loss", marker_color="red", opacity=0.7))
        fig.update_layout(barmode="overlay", title="Win/Loss PnL Distribution",
                          template="plotly_dark", height=300)
        return self._to_base64(fig)

    def _regime_pie(self) -> str:
        counts = self._regimes
        if not counts:
            counts = {"trending": 0, "ranging": 0, "volatile": 0}
        fig = px.pie(names=list(counts.keys()), values=list(counts.values()),
                     title="Regime Breakdown", template="plotly_dark")
        return self._to_base64(fig)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _to_base64(fig: go.Figure) -> str:
        """Convert Plotly figure to base64-encoded PNG string."""
        buf = io.BytesIO()
        fig.write_image(buf, format="png", width=900, height=400, scale=1.5)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

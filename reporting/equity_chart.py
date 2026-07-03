"""
reporting/equity_chart.py — genereaza equity curve chart interactiv HTML.

Foloseste Plotly pentru a genera un fisier HTML standalone cu:
- Equity curve cumulativa
- Drawdown panel
- Statistici de performanta in titlu

Usage::

    from reporting.equity_chart import generate_equity_chart
    path = generate_equity_chart(pnl_series, output_path="output/equity.html")
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional


def generate_equity_chart(
    pnl_series: List[float],
    output_path: str = "output/equity.html",
    title: str = "QuantLuna — Equity Curve",
    pair: Optional[str] = None,
) -> str:
    """
    Genereaza raport HTML interactiv cu equity curve si drawdown.
    Returneaza calea fisierului generat.
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        raise ImportError("plotly este necesar: pip install plotly")

    import numpy as np

    if not pnl_series:
        pnl_series = [0.0]

    cum_pnl = list(np.cumsum(pnl_series))
    peak = list(np.maximum.accumulate(cum_pnl))
    drawdown = [p - c for p, c in zip(peak, cum_pnl)]
    indices = list(range(len(pnl_series)))

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        subplot_titles=("Equity Curve (Cumulative PnL USDT)", "Drawdown"),
        vertical_spacing=0.08,
    )

    fig.add_trace(
        go.Scatter(
            x=indices, y=cum_pnl,
            mode="lines", name="Equity",
            line=dict(color="#00d4aa", width=2),
            fill="tozeroy", fillcolor="rgba(0,212,170,0.08)",
        ),
        row=1, col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=indices, y=[-d for d in drawdown],
            mode="lines", name="Drawdown",
            line=dict(color="#ff4c6a", width=1.5),
            fill="tozeroy", fillcolor="rgba(255,76,106,0.12)",
        ),
        row=2, col=1,
    )

    n = len(pnl_series)
    total = sum(pnl_series)
    wins = sum(1 for p in pnl_series if p > 0)
    win_rate = wins / n * 100 if n else 0
    max_dd = max(drawdown) if drawdown else 0

    subtitle = (
        f"Trades: {n} | Total PnL: {total:+.2f} USDT | "
        f"Win Rate: {win_rate:.1f}% | Max DD: {max_dd:.2f} USDT"
    )
    if pair:
        subtitle = f"{pair} — " + subtitle

    fig.update_layout(
        title=dict(text=f"{title}<br><sup>{subtitle}</sup>", font=dict(size=16)),
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=600,
        margin=dict(l=60, r=40, t=80, b=40),
    )
    fig.update_xaxes(showgrid=True, gridcolor="#21262d")
    fig.update_yaxes(showgrid=True, gridcolor="#21262d")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs="cdn", full_html=True)
    return str(out)

"""
backtest/multi_strategy_backtester.py  -  QuantLuna Multi-Strategy Backtester v1.0

Sprint S38 (2026-07-12):
  Backtester care foloseste date REALE din DailyPnLTracker (SQLite) pentru
  a simula si compara performanta strategiilor.

  Metrici calculate per strategie:
    - Total PnL (USDT + %)
    - Sharpe Ratio (anualizat, risk-free=0)
    - Calmar Ratio (PnL anual / MaxDrawdown)
    - Max Drawdown (USDT + %)
    - Win Rate (% zile profitabile)
    - Avg Win / Avg Loss
    - Profit Factor (gross profit / gross loss)
    - Longest Drawdown (zile consecutive in drawdown)

  Output:
    - Dict cu metrici per strategie
    - Raport HTML (backtest/reports/report_YYYYMMDD_HHMMSS.html)
    - CSV cu equity curves (backtest/reports/equity_YYYYMMDD.csv)

  Sursa date:
    - Primar: DailyPnLTracker SQLite (date reale din trading)
    - Secundar: CSV historic daca DB nu are suficiente date

Usage::

    bt = MultiStrategyBacktester.from_db()
    results = await bt.run(strategies=["pairs_futures", "spot"], days=90)
    bt.export_html(results, "backtest/reports/report.html")
    print(bt.summary_table(results))
"""
from __future__ import annotations

import asyncio
import csv
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DailyReturn:
    date: str
    pnl_usdt: float
    equity: float
    trades: int
    fees: float
    strategy: str


@dataclass
class BacktestMetrics:
    strategy: str
    days: int
    total_pnl_usdt: float
    total_pnl_pct: float
    sharpe_ratio: float
    calmar_ratio: float
    max_drawdown_usdt: float
    max_drawdown_pct: float
    win_rate: float           # 0.0 - 1.0
    avg_win_usdt: float
    avg_loss_usdt: float
    profit_factor: float
    longest_drawdown_days: int
    total_trades: int
    total_fees: float
    start_equity: float
    end_equity: float
    equity_curve: List[Tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "days": self.days,
            "total_pnl_usdt": round(self.total_pnl_usdt, 4),
            "total_pnl_pct": round(self.total_pnl_pct * 100, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "max_drawdown_usdt": round(self.max_drawdown_usdt, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct * 100, 4),
            "win_rate_pct": round(self.win_rate * 100, 2),
            "avg_win_usdt": round(self.avg_win_usdt, 4),
            "avg_loss_usdt": round(self.avg_loss_usdt, 4),
            "profit_factor": round(self.profit_factor, 4),
            "longest_drawdown_days": self.longest_drawdown_days,
            "total_trades": self.total_trades,
            "total_fees": round(self.total_fees, 4),
            "start_equity": round(self.start_equity, 2),
            "end_equity": round(self.end_equity, 2),
        }


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------

class MultiStrategyBacktester:
    """
    Backtester multi-strategie cu date reale din DailyPnLTracker.
    """

    def __init__(
        self,
        db_path: str = "state/daily_pnl.db",
        reports_dir: str = "backtest/reports",
        risk_free_rate: float = 0.0,
    ) -> None:
        self._db_path = db_path
        self._reports_dir = Path(reports_dir)
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._rfr = risk_free_rate

    @classmethod
    def from_db(cls, db_path: Optional[str] = None) -> "MultiStrategyBacktester":
        return cls(
            db_path=db_path or os.getenv("DAILY_PNL_DB", "state/daily_pnl.db")
        )

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------

    async def _load_strategy_data(
        self, strategy: str, days: int
    ) -> List[DailyReturn]:
        """Incarca date din DailyPnLTracker pentru o strategie."""
        try:
            from execution.daily_pnl_tracker import DailyPnLTracker
            tracker = DailyPnLTracker(db_path=self._db_path)
            history = await tracker.get_history(strategy=strategy, limit=days)
        except Exception as exc:
            logger.warning(
                "[Backtester] Nu pot citi DB {} pentru {}: {}",
                self._db_path, strategy, exc,
            )
            history = []

        # Fallback: CSV daca DB gol
        if not history:
            history = self._load_csv_fallback(strategy, days)

        records = []
        for h in sorted(history, key=lambda x: x.get("date", "")):
            records.append(DailyReturn(
                date=h.get("date", ""),
                pnl_usdt=float(h.get("pnl", h.get("pnl_usdt", 0)) or 0),
                equity=float(h.get("equity_end", h.get("equity", 0)) or 0),
                trades=int(h.get("trades", 0) or 0),
                fees=float(h.get("fees", 0) or 0),
                strategy=strategy,
            ))
        return records

    def _load_csv_fallback(
        self, strategy: str, days: int
    ) -> List[Dict[str, Any]]:
        """Fallback: cauta CSV in data/ sau backtest/data/."""
        for candidate in [
            f"data/{strategy}_history.csv",
            f"backtest/data/{strategy}.csv",
            f"backtest/data/{strategy}_pnl.csv",
        ]:
            if Path(candidate).exists():
                logger.info(
                    "[Backtester] Fallback CSV: {}", candidate
                )
                rows = []
                with open(candidate, newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rows.append(dict(row))
                return rows[-days:] if len(rows) > days else rows
        return []

    # ------------------------------------------------------------------
    # Core metrics calculation
    # ------------------------------------------------------------------

    def _calculate_metrics(
        self, records: List[DailyReturn], strategy: str
    ) -> BacktestMetrics:
        """Calculeaza toate metricile pentru o serie de randamente zilnice."""
        if not records:
            return BacktestMetrics(
                strategy=strategy, days=0,
                total_pnl_usdt=0, total_pnl_pct=0,
                sharpe_ratio=0, calmar_ratio=0,
                max_drawdown_usdt=0, max_drawdown_pct=0,
                win_rate=0, avg_win_usdt=0, avg_loss_usdt=0,
                profit_factor=0, longest_drawdown_days=0,
                total_trades=0, total_fees=0,
                start_equity=0, end_equity=0,
            )

        pnls = [r.pnl_usdt for r in records]
        equities = [r.equity for r in records]

        # PnL total
        total_pnl = sum(pnls)
        start_eq = equities[0] if equities else 0
        end_eq = equities[-1] if equities else 0
        total_pnl_pct = (total_pnl / start_eq) if start_eq > 0 else 0

        # Sharpe ratio (anualizat, daily returns)
        daily_returns_pct = [
            (p / (eq - p)) if (eq - p) > 0 else 0
            for p, eq in zip(pnls, equities)
        ]
        if len(daily_returns_pct) > 1:
            mean_r = sum(daily_returns_pct) / len(daily_returns_pct)
            variance = sum((r - mean_r) ** 2 for r in daily_returns_pct) / (
                len(daily_returns_pct) - 1
            )
            std_r = math.sqrt(variance) if variance > 0 else 0
            sharpe = (
                (mean_r - self._rfr / 252) / std_r * math.sqrt(252)
                if std_r > 0 else 0.0
            )
        else:
            sharpe = 0.0

        # Max Drawdown
        peak = equities[0]
        max_dd_usdt = 0.0
        max_dd_pct = 0.0
        dd_days = 0
        current_dd_days = 0
        longest_dd = 0
        in_drawdown = False

        for eq in equities:
            if eq > peak:
                peak = eq
                if in_drawdown:
                    longest_dd = max(longest_dd, current_dd_days)
                    current_dd_days = 0
                    in_drawdown = False
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0
            if dd > max_dd_usdt:
                max_dd_usdt = dd
                max_dd_pct = dd_pct
            if dd > 0:
                in_drawdown = True
                current_dd_days += 1
                longest_dd = max(longest_dd, current_dd_days)

        # Calmar ratio
        annualized_pnl = total_pnl * (365 / max(len(records), 1))
        calmar = (
            annualized_pnl / max_dd_usdt if max_dd_usdt > 0 else 0.0
        )

        # Win rate, avg win/loss, profit factor
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        equity_curve = [(r.date, r.equity) for r in records]

        return BacktestMetrics(
            strategy=strategy,
            days=len(records),
            total_pnl_usdt=total_pnl,
            total_pnl_pct=total_pnl_pct,
            sharpe_ratio=sharpe,
            calmar_ratio=calmar,
            max_drawdown_usdt=max_dd_usdt,
            max_drawdown_pct=max_dd_pct,
            win_rate=win_rate,
            avg_win_usdt=avg_win,
            avg_loss_usdt=avg_loss,
            profit_factor=profit_factor,
            longest_drawdown_days=longest_dd,
            total_trades=sum(r.trades for r in records),
            total_fees=sum(r.fees for r in records),
            start_equity=start_eq,
            end_equity=end_eq,
            equity_curve=equity_curve,
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(
        self,
        strategies: Optional[List[str]] = None,
        days: int = 90,
    ) -> Dict[str, BacktestMetrics]:
        """
        Ruleaza backtestul pentru toate strategiile si returneaza metrici.
        Daca strategies e None, auto-detecteaza din DB.
        """
        if strategies is None:
            strategies = await self._detect_strategies()

        logger.info(
            "[Backtester] Run: {} strategii x {} zile | DB: {}",
            len(strategies), days, self._db_path,
        )

        results: Dict[str, BacktestMetrics] = {}
        for strategy in strategies:
            records = await self._load_strategy_data(strategy, days)
            metrics = self._calculate_metrics(records, strategy)
            results[strategy] = metrics
            logger.info(
                "[Backtester] {} | {} zile | PnL={:+.2f} USDT | "
                "Sharpe={:.3f} | MaxDD={:.2f}% | WinRate={:.1f}%",
                strategy, metrics.days,
                metrics.total_pnl_usdt,
                metrics.sharpe_ratio,
                metrics.max_drawdown_pct * 100,
                metrics.win_rate * 100,
            )

        return results

    async def _detect_strategies(self) -> List[str]:
        """Auto-detecteaza strategii distincte din DB."""
        try:
            import sqlite3
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT strategy FROM daily_pnl "
                    "ORDER BY strategy"
                ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return ["pairs_futures", "spot", "margin"]

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def summary_table(self, results: Dict[str, BacktestMetrics]) -> str:
        """Returneaza tabel ASCII cu metrici comparative."""
        if not results:
            return "(niciun rezultat)"
        header = (
            f"{'Strategy':<20} {'PnL USDT':>12} {'PnL %':>8} "
            f"{'Sharpe':>8} {'Calmar':>8} {'MaxDD %':>8} "
            f"{'WinRate':>8} {'PF':>6} {'Trades':>7}\n"
        )
        sep = "-" * len(header.rstrip()) + "\n"
        lines = [header, sep]
        for m in sorted(results.values(), key=lambda x: -x.total_pnl_usdt):
            lines.append(
                f"{m.strategy:<20} "
                f"{m.total_pnl_usdt:>+12.2f} "
                f"{m.total_pnl_pct*100:>7.2f}% "
                f"{m.sharpe_ratio:>8.3f} "
                f"{m.calmar_ratio:>8.3f} "
                f"{m.max_drawdown_pct*100:>7.2f}% "
                f"{m.win_rate*100:>7.1f}% "
                f"{m.profit_factor:>6.2f} "
                f"{m.total_trades:>7}\n"
            )
        return "".join(lines)

    def export_csv(
        self, results: Dict[str, BacktestMetrics], path: Optional[str] = None
    ) -> str:
        """Exporta equity curves in CSV."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = path or str(self._reports_dir / f"equity_{ts}.csv")
        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "strategy", "equity_usdt"])
            for m in results.values():
                for date, equity in m.equity_curve:
                    writer.writerow([date, m.strategy, equity])
        logger.info("[Backtester] CSV exportat: {}", out)
        return out

    def export_html(
        self, results: Dict[str, BacktestMetrics], path: Optional[str] = None
    ) -> str:
        """Genereaza raport HTML cu metrici si equity curves."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = path or str(self._reports_dir / f"report_{ts}.html")

        # Colori per strategie
        colors = ["#4ade80", "#60a5fa", "#f59e0b", "#a78bfa", "#fb7185"]
        strat_colors = {
            m.strategy: colors[i % len(colors)]
            for i, m in enumerate(results.values())
        }

        # SVG equity curves
        def make_svg(metrics_list) -> str:
            W, H = 700, 200
            all_eq = [eq for m in metrics_list for _, eq in m.equity_curve]
            if not all_eq:
                return "<p style='color:#666'>Date insuficiente</p>"
            min_eq = min(all_eq)
            max_eq = max(all_eq)
            rng = max_eq - min_eq or 1
            lines_svg = []
            for m in metrics_list:
                if len(m.equity_curve) < 2:
                    continue
                pts = " ".join(
                    f"{(i/(len(m.equity_curve)-1))*W:.1f},"
                    f"{H-((eq-min_eq)/rng)*(H-20)-10:.1f}"
                    for i, (_, eq) in enumerate(m.equity_curve)
                )
                c = strat_colors.get(m.strategy, "#888")
                lines_svg.append(
                    f'<polyline points="{pts}" fill="none" stroke="{c}" '
                    f'stroke-width="2"/>'
                )
            legend = " ".join(
                f'<span style="color:{strat_colors.get(m.strategy,"#888")}"'
                f'>&#9632; {m.strategy}</span>'
                for m in metrics_list
            )
            return (
                f'<svg width="100%" viewBox="0 0 {W} {H}" '
                f'style="background:#0a0a1a;border-radius:8px">'
                + "\n".join(lines_svg)
                + f'</svg><div style="margin-top:8px;font-size:12px">{legend}</div>'
            )

        # Metrics table rows
        rows_html = ""
        for m in sorted(results.values(), key=lambda x: -x.total_pnl_usdt):
            c = strat_colors.get(m.strategy, "#888")
            pnl_color = "#4ade80" if m.total_pnl_usdt >= 0 else "#f87171"
            rows_html += f"""
            <tr>
              <td style="color:{c};font-weight:600">{m.strategy}</td>
              <td>{m.days}</td>
              <td style="color:{pnl_color}">{m.total_pnl_usdt:+.2f} USDT</td>
              <td style="color:{pnl_color}">{m.total_pnl_pct*100:+.2f}%</td>
              <td>{m.sharpe_ratio:.3f}</td>
              <td>{m.calmar_ratio:.3f}</td>
              <td style="color:#f87171">{m.max_drawdown_pct*100:.2f}%</td>
              <td>{m.win_rate*100:.1f}%</td>
              <td>{m.profit_factor:.2f}</td>
              <td>{m.total_trades}</td>
              <td style="color:#888">{m.total_fees:.2f}</td>
            </tr>"""

        svg_chart = make_svg(list(results.values()))
        ts_human = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        html = f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="UTF-8">
<title>QuantLuna Backtest Report {ts_human}</title>
<style>
  body {{ background:#0f0f1a; color:#e0e0ff;
          font-family: Inter, system-ui, sans-serif; padding: 40px; }}
  h1 {{ font-size:24px; font-weight:800; margin-bottom:4px; }}
  .sub {{ color:#555; font-size:13px; margin-bottom:32px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; padding:8px 12px; background:#1a1a3e;
        color:#aaa; border-bottom:1px solid #2a2a4a; }}
  td {{ padding:8px 12px; border-bottom:1px solid #1a1a2a; }}
  .section {{ background:#1a1a2e; border-radius:12px;
               padding:20px 24px; margin-bottom:24px;
               border:1px solid #2a2a4a; }}
</style>
</head>
<body>
<h1>&#128202; QuantLuna Multi-Strategy Backtest</h1>
<div class="sub">Generat: {ts_human} | Sursa date: DailyPnLTracker SQLite</div>

<div class="section">
  <div style="font-weight:600;margin-bottom:16px">&#128200; Equity Curves</div>
  {svg_chart}
</div>

<div class="section">
  <div style="font-weight:600;margin-bottom:16px">&#128217; Metrici per Strategie</div>
  <table>
    <thead><tr>
      <th>Strategie</th><th>Zile</th><th>PnL USDT</th><th>PnL %</th>
      <th>Sharpe</th><th>Calmar</th><th>MaxDD %</th>
      <th>WinRate</th><th>Prof.Factor</th><th>Trades</th><th>Fees</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>

<div style="color:#444;font-size:12px;text-align:center">
  QuantLuna Backtester v1.0 &mdash; date din trading real
</div>
</body></html>"""

        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("[Backtester] Raport HTML exportat: {}", out)
        return out


# ---------------------------------------------------------------------------
# CLI quick-run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="QuantLuna Multi-Strategy Backtest")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--strategies", nargs="*", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--html", default=None)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    async def _main():
        bt = MultiStrategyBacktester.from_db(db_path=args.db)
        results = await bt.run(strategies=args.strategies, days=args.days)
        print(bt.summary_table(results))
        if args.html:
            bt.export_html(results, args.html)
        if args.csv:
            bt.export_csv(results, args.csv)
        return results

    asyncio.run(_main())

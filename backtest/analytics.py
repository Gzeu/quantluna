"""
QuantLuna — Performance Analytics

Metrics computed:
  Sharpe, Sortino, Calmar, Max Drawdown,
  Win Rate, Avg Win/Loss, Profit Factor,
  Annualised Return, Volatility
"""
import numpy as np
import pandas as pd
from typing import List, Dict


class PerformanceAnalytics:

    @staticmethod
    def compute(
        equity_curve: pd.Series,
        trades: list,
        freq_hours: float = 1.0,
        risk_free_annual: float = 0.05,
    ) -> Dict:
        bars_per_year = (365 * 24) / freq_hours
        returns = equity_curve.pct_change().dropna()

        if len(returns) < 2:
            return {}

        # Annualised stats
        ann_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (bars_per_year / len(equity_curve)) - 1
        ann_vol = returns.std() * np.sqrt(bars_per_year)
        risk_free_bar = (1 + risk_free_annual) ** (1 / bars_per_year) - 1
        excess = returns - risk_free_bar

        sharpe = (excess.mean() / excess.std() * np.sqrt(bars_per_year)) if excess.std() > 0 else 0

        downside = excess[excess < 0]
        sortino = (excess.mean() / downside.std() * np.sqrt(bars_per_year)) if len(downside) > 0 and downside.std() > 0 else 0

        # Drawdown
        rolling_max = equity_curve.cummax()
        dd = (equity_curve - rolling_max) / rolling_max
        max_dd = dd.min()
        calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

        # Trade stats
        if trades:
            pnls = [t.pnl_net for t in trades if hasattr(t, 'pnl_net')]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            win_rate = len(wins) / len(pnls) if pnls else 0
            avg_win = np.mean(wins) if wins else 0
            avg_loss = abs(np.mean(losses)) if losses else 1
            profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 0
            total_fees = sum(getattr(t, 'fees', 0) for t in trades)
            total_funding = sum(getattr(t, 'funding_paid', 0) for t in trades)
        else:
            win_rate = avg_win = avg_loss = profit_factor = total_fees = total_funding = 0

        return {
            "ann_return": ann_return,
            "ann_vol": ann_vol,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "max_drawdown": max_dd,
            "n_trades": len(trades),
            "win_rate": win_rate,
            "avg_win_usdt": avg_win,
            "avg_loss_usdt": avg_loss,
            "profit_factor": profit_factor,
            "total_fees_usdt": total_fees,
            "total_funding_usdt": total_funding,
            "final_equity": equity_curve.iloc[-1],
            "initial_equity": equity_curve.iloc[0],
        }

    @staticmethod
    def print_report(metrics: Dict) -> None:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title="QuantLuna — Performance Report")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        rows = [
            ("Ann. Return",      f"{metrics.get('ann_return', 0):.2%}"),
            ("Ann. Volatility",  f"{metrics.get('ann_vol', 0):.2%}"),
            ("Sharpe Ratio",     f"{metrics.get('sharpe', 0):.3f}"),
            ("Sortino Ratio",    f"{metrics.get('sortino', 0):.3f}"),
            ("Calmar Ratio",     f"{metrics.get('calmar', 0):.3f}"),
            ("Max Drawdown",     f"{metrics.get('max_drawdown', 0):.2%}"),
            ("Win Rate",         f"{metrics.get('win_rate', 0):.2%}"),
            ("N Trades",         str(metrics.get('n_trades', 0))),
            ("Profit Factor",    f"{metrics.get('profit_factor', 0):.2f}"),
            ("Total Fees",       f"${metrics.get('total_fees_usdt', 0):.2f}"),
            ("Funding Paid",     f"${metrics.get('total_funding_usdt', 0):.2f}"),
            ("Final Equity",     f"${metrics.get('final_equity', 0):.2f}"),
        ]
        for k, v in rows:
            table.add_row(k, v)
        console.print(table)

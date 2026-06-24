"""
QuantLuna — Backtest Engine

Event-driven backtest with:
  - Bar-by-bar Kalman Filter update
  - Realistic transaction costs (maker/taker + slippage)
  - Funding rate simulation for perpetual futures
  - Position tracking and P&L calculation
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from loguru import logger

from config.settings import QuantLunaConfig
from core.kalman_filter import KalmanHedgeRatio
from core.spread import SpreadEngine
from strategy.signal import SignalGenerator, Signal
from risk.position_sizer import PositionSizer


@dataclass
class Trade:
    pair: str
    direction: Signal
    entry_time: pd.Timestamp
    exit_time: Optional[pd.Timestamp]
    entry_price_y: float
    entry_price_x: float
    exit_price_y: Optional[float]
    exit_price_x: Optional[float]
    qty_y: float
    qty_x: float
    beta_at_entry: float
    entry_zscore: float
    exit_zscore: Optional[float]
    pnl_gross: float = 0.0
    pnl_net: float = 0.0    # After fees, slippage, funding
    fees: float = 0.0
    funding_paid: float = 0.0
    exit_reason: str = ""


class BacktestEngine:
    """
    Full backtest engine for a single pair.

    Parameters
    ----------
    cfg : QuantLunaConfig
    """

    def __init__(self, cfg: QuantLunaConfig = None):
        self.cfg = cfg or QuantLunaConfig()
        self._trades: List[Trade] = []
        self._equity: List[float] = []

    def run(
        self,
        y: pd.Series,
        x: pd.Series,
        funding_rate: Optional[pd.Series] = None,
        freq_hours: float = 1.0,
    ) -> Dict:
        """
        Run full backtest on aligned price series.

        Parameters
        ----------
        y, x          : aligned close price series
        funding_rate  : optional funding rate series (aligned to y)
        freq_hours    : bar duration in hours

        Returns
        -------
        dict with trades, equity_curve, metrics
        """
        capital = self.cfg.risk.max_capital_usdt
        equity = capital
        self._equity = [equity]
        self._trades = []

        # Fit Kalman + spread
        kf = KalmanHedgeRatio(
            delta=self.cfg.kalman.delta,
            observation_noise=self.cfg.kalman.observation_noise,
        )
        spread_engine = SpreadEngine(kalman=kf)
        signal_gen = SignalGenerator(spread_engine=spread_engine, cfg=self.cfg.signal)
        sizer = PositionSizer(cfg=self.cfg.risk)

        spread_df = spread_engine.fit(y, x)
        signal_df = signal_gen.generate_batch(spread_df)

        # Transaction cost params
        total_fee = self.cfg.execution.taker_fee * 2 + self.cfg.execution.slippage_bps / 10000 * 2

        in_trade = False
        current_trade: Optional[Trade] = None

        for i in range(len(signal_df)):
            row = signal_df.iloc[i]
            ts = signal_df.index[i]
            sig = Signal(int(row.get("signal", 0)))
            price_y = y.iloc[i]
            price_x = x.iloc[i]
            beta = row["beta"]
            z = row.get("zscore", 0.0)

            fr = float(funding_rate.iloc[i]) if funding_rate is not None else 0.0

            if not in_trade and sig != Signal.EXIT and row.get("is_warm", False):
                sizing = sizer.size(
                    capital_usdt=equity,
                    beta=beta,
                    spread_series=spread_df["spread"].iloc[max(0, i-100):i],
                    price_y=price_y,
                    price_x=price_x,
                    freq_hours=freq_hours,
                )
                qty_y = sizing.qty_y / price_y
                qty_x = sizing.qty_x / price_x

                current_trade = Trade(
                    pair=f"Y/X",
                    direction=sig,
                    entry_time=ts,
                    exit_time=None,
                    entry_price_y=price_y,
                    entry_price_x=price_x,
                    exit_price_y=None,
                    exit_price_x=None,
                    qty_y=qty_y,
                    qty_x=qty_x,
                    beta_at_entry=beta,
                    entry_zscore=z,
                    exit_zscore=None,
                )
                in_trade = True

            elif in_trade and current_trade is not None:
                # Mark-to-market P&L
                if current_trade.direction == Signal.LONG_SPREAD:
                    pnl = (
                        (price_y - current_trade.entry_price_y) * current_trade.qty_y
                        - (price_x - current_trade.entry_price_x) * current_trade.qty_x
                    )
                else:
                    pnl = (
                        -(price_y - current_trade.entry_price_y) * current_trade.qty_y
                        + (price_x - current_trade.entry_price_x) * current_trade.qty_x
                    )

                if sig == Signal.EXIT:
                    fees = (current_trade.qty_y * price_y + current_trade.qty_x * price_x) * total_fee
                    funding = abs(fr) * current_trade.qty_y * price_y * (freq_hours / 8)
                    pnl_net = pnl - fees - funding

                    current_trade.exit_time = ts
                    current_trade.exit_price_y = price_y
                    current_trade.exit_price_x = price_x
                    current_trade.exit_zscore = z
                    current_trade.pnl_gross = pnl
                    current_trade.pnl_net = pnl_net
                    current_trade.fees = fees
                    current_trade.funding_paid = funding
                    current_trade.exit_reason = "zscore_exit"

                    equity += pnl_net
                    self._trades.append(current_trade)
                    in_trade = False
                    current_trade = None

            self._equity.append(equity)

        metrics = PerformanceAnalytics.compute(
            pd.Series(self._equity),
            self._trades,
            freq_hours=freq_hours,
        )

        logger.info(
            f"Backtest complete: {len(self._trades)} trades, "
            f"Sharpe={metrics.get('sharpe', 0):.2f}, "
            f"Max DD={metrics.get('max_drawdown', 0):.1%}"
        )
        return {"trades": self._trades, "equity": self._equity, "metrics": metrics}


# Avoid circular import
from backtest.analytics import PerformanceAnalytics  # noqa: E402

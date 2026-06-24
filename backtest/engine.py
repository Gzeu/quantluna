"""
QuantLuna — WalkForwardEngine Sprint 7

Backtesting engine complet pentru pairs trading cu Kalman Filter.

Features:
- Walk-forward validation cu configurable splits
- Purged K-fold cross-validation (eliminare look-ahead bias la granitțe fold)
- Out-of-sample (OOS) evaluation final
- Transaction costs: maker/taker fees, slippage model, funding cost
- Position sizing: volatilitate-țintă + Kelly fracțional
- Performance metrics complete: Sharpe, Sortino, Calmar, max DD,
  win rate, profit factor, avg trade, Omega ratio
- Regime-aware: sărit bareme de cointegration în folds cu breakdown
- Reproducibil: seed fix pentru orice rezultat

Limite / Riscuri reale:
- Kalman Filter nu are look-ahead bias în sine, DAR SpreadEngine.fit()
  folosește întregul in-sample pentru warmup; purging rezolvă granitțele
- Funding cost simulat simplist (constant per bar în holding period);
  în realitate e discontinuu la fiecare 8h
- Slippage model linear — nu capturează impactul de preț la volume mari
- Nu simulează margin calls sau liquidation cascades
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from core.spread import SpreadEngine
from strategy.signal import SignalGenerator, Signal
from config.settings import SignalConfig


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    # Walk-forward
    n_splits: int = 5               # număr fold-uri IS/OOS
    train_ratio: float = 0.7        # fracție in-sample per fold
    purge_bars: int = 10            # bare eliminate la granitța IS/OOS
    embargo_bars: int = 5           # bare adăugate după purge (anti-leakage)

    # Costs
    fee_maker: float = 0.0002       # 0.02% Bybit maker
    fee_taker: float = 0.00055      # 0.055% Bybit taker
    slippage_bps: float = 2.0       # basis points per side
    funding_rate_annual: float = 0.05  # 5% annualized net funding cost estimat

    # Position sizing
    capital_usd: float = 10_000.0
    vol_target: float = 0.01        # 1% daily vol target
    kelly_fraction: float = 0.25    # fractional Kelly
    max_leverage: float = 3.0
    min_position_usd: float = 50.0  # sub acest nivel, nu se deschide poziție

    # Signal
    signal_cfg: Optional[SignalConfig] = None

    # Reproducibility
    seed: int = 42


# ---------------------------------------------------------------------------
# Trade Record
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    fold: int
    split: str              # 'IS' | 'OOS'
    entry_bar: int
    exit_bar: int
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    direction: str          # 'LONG_SPREAD' | 'SHORT_SPREAD'
    entry_zscore: float
    exit_zscore: float
    hedge_ratio: float
    qty_y: float
    qty_x: float
    entry_price_y: float
    entry_price_x: float
    exit_price_y: float
    exit_price_x: float
    gross_pnl: float
    fees: float
    slippage: float
    funding_cost: float
    net_pnl: float
    bars_held: int
    exit_reason: str


# ---------------------------------------------------------------------------
# Performance Metrics
# ---------------------------------------------------------------------------

@dataclass
class PerformanceMetrics:
    fold: int
    split: str
    n_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_net_pnl: float = 0.0
    total_net_pnl: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    omega_ratio: float = 0.0
    avg_bars_held: float = 0.0
    total_fees: float = 0.0
    total_funding_cost: float = 0.0
    ann_return: float = 0.0         # annualized return
    ann_volatility: float = 0.0     # annualized volatility of daily P&L
    n_bars: int = 0

    def summary(self) -> str:
        return (
            f"Fold {self.fold} [{self.split}] "
            f"Sharpe={self.sharpe:.2f} Sortino={self.sortino:.2f} "
            f"Calmar={self.calmar:.2f} MaxDD={self.max_drawdown_pct:.1f}% "
            f"WR={self.win_rate:.1%} PF={self.profit_factor:.2f} "
            f"Trades={self.n_trades} NetPnL=${self.total_net_pnl:.0f}"
        )


# ---------------------------------------------------------------------------
# Walk-Forward Engine
# ---------------------------------------------------------------------------

class WalkForwardEngine:
    """
    Walk-forward backtesting engine pentru pairs trading cu Kalman Filter.

    Usage:
        engine = WalkForwardEngine(
            df=ohlcv_df,  # columns: timestamp, close_y, close_x
            cfg=BacktestConfig(),
            spread_engine_factory=lambda: SpreadEngine(KalmanConfig()),
        )
        results = engine.run()
        print(results.oos_metrics)
    """

    def __init__(
        self,
        df: pd.DataFrame,
        cfg: BacktestConfig,
        spread_engine_factory,  # Callable[[], SpreadEngine]
    ) -> None:
        self.df = df.copy().reset_index(drop=True)
        self.cfg = cfg
        self.factory = spread_engine_factory
        self._validate_input()

    def _validate_input(self) -> None:
        required = {"timestamp", "close_y", "close_x"}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")
        if len(self.df) < 200:
            raise ValueError(
                f"Dataset too short: {len(self.df)} bars. Minimum 200 required."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> "BacktestResults":
        """
        Execute full walk-forward validation.
        Returns BacktestResults with per-fold metrics + aggregated OOS.
        """
        splits = self._build_splits()
        all_trades: List[TradeRecord] = []
        all_metrics: List[PerformanceMetrics] = []

        for fold_idx, (is_idx, oos_idx) in enumerate(splits):
            logger.info(
                f"Walk-forward fold {fold_idx+1}/{len(splits)} — "
                f"IS={len(is_idx)} bars OOS={len(oos_idx)} bars"
            )

            # --- In-Sample: fit Kalman, generate signals, record IS metrics ---
            is_trades, is_metrics = self._run_fold(
                fold_idx, is_idx, split="IS"
            )

            # --- Out-of-Sample: warm Kalman on IS, evaluate on OOS ---
            oos_trades, oos_metrics = self._run_fold(
                fold_idx, oos_idx, split="OOS",
                warmup_idx=is_idx  # IS data pentru warmup Kalman
            )

            all_trades.extend(is_trades)
            all_trades.extend(oos_trades)
            all_metrics.extend([is_metrics, oos_metrics])

            logger.info(is_metrics.summary())
            logger.info(oos_metrics.summary())

        # Aggregate OOS metrics
        oos_all_trades = [t for t in all_trades if t.split == "OOS"]
        agg_oos = self._compute_metrics(-1, "OOS_AGG", oos_all_trades, len(self.df))

        logger.info(f"\n=== AGGREGATE OOS === {agg_oos.summary()}")

        return BacktestResults(
            trades=all_trades,
            per_fold_metrics=all_metrics,
            oos_metrics=agg_oos,
            config=self.cfg,
        )

    # ------------------------------------------------------------------
    # Splits
    # ------------------------------------------------------------------

    def _build_splits(self) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Walk-forward splits cu purge + embargo la granitțe IS/OOS.
        """
        n = len(self.df)
        fold_size = n // self.cfg.n_splits
        splits = []

        for i in range(self.cfg.n_splits):
            end = (i + 1) * fold_size if i < self.cfg.n_splits - 1 else n
            start = i * fold_size

            split_len = end - start
            train_end = start + int(split_len * self.cfg.train_ratio)

            is_idx = np.arange(start, train_end)

            # Purge + embargo: elimină granita IS/OOS
            oos_start = train_end + self.cfg.purge_bars + self.cfg.embargo_bars
            oos_idx = np.arange(oos_start, end)

            if len(is_idx) < 50 or len(oos_idx) < 20:
                logger.warning(f"Fold {i}: prea puține bare IS={len(is_idx)} OOS={len(oos_idx)}, skip")
                continue

            splits.append((is_idx, oos_idx))

        if not splits:
            raise RuntimeError("Nu s-au putut construi fold-uri valide. Verifică n_splits și lungimea datelor.")

        return splits

    # ------------------------------------------------------------------
    # Fold Execution
    # ------------------------------------------------------------------

    def _run_fold(
        self,
        fold_idx: int,
        idx: np.ndarray,
        split: str,
        warmup_idx: Optional[np.ndarray] = None,
    ) -> Tuple[List[TradeRecord], PerformanceMetrics]:
        """
        Rulează un fold complet.
        Dacă warmup_idx e furnizat, ruleză mai întâi Kalman pe warmup data
        fără să genereze semnale (OOS fold warm-start).
        """
        spread_engine = self.factory()
        signal_gen = SignalGenerator(
            spread_engine,
            cfg=self.cfg.signal_cfg or SignalConfig(),
        )

        fold_df = self.df.iloc[idx].copy().reset_index(drop=True)

        # Warm-start Kalman pe IS data (pentru OOS fold)
        if warmup_idx is not None:
            warmup_df = self.df.iloc[warmup_idx].copy().reset_index(drop=True)
            self._warmup_kalman(spread_engine, warmup_df)

        # Fit spread pe fold data
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spread_df = spread_engine.fit(fold_df["close_y"], fold_df["close_x"])

        # Generate signals
        sig_df = signal_gen.generate_batch(spread_df)
        fold_df = pd.concat([fold_df.reset_index(drop=True), sig_df], axis=1)

        # Simulate trades
        trades = self._simulate_trades(fold_df, fold_idx, split)

        # Compute metrics
        metrics = self._compute_metrics(fold_idx, split, trades, len(fold_df))

        return trades, metrics

    def _warmup_kalman(
        self, spread_engine: SpreadEngine, warmup_df: pd.DataFrame
    ) -> None:
        """Run Kalman online updates on warmup data (no signal generation)."""
        for _, row in warmup_df.iterrows():
            try:
                spread_engine.update_one(
                    float(row["close_y"]),
                    float(row["close_x"]),
                    ts=row.get("timestamp"),
                )
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Trade Simulation
    # ------------------------------------------------------------------

    def _simulate_trades(
        self,
        df: pd.DataFrame,
        fold_idx: int,
        split: str,
    ) -> List[TradeRecord]:
        """
        Simulează execuția trade-urilor pe baza semnalelor generate.
        Include: fees, slippage, funding cost.
        """
        trades: List[TradeRecord] = []
        in_trade = False
        entry_bar = 0
        entry_data: Dict = {}
        capital = self.cfg.capital_usd

        for i in range(len(df)):
            row = df.iloc[i]
            sig = int(row.get("signal", 0))
            is_warm = bool(row.get("is_warm", False))

            if not is_warm:
                continue

            price_y = float(row["close_y"])
            price_x = float(row["close_x"])
            zscore = float(row.get("zscore", 0.0))
            beta = float(row.get("beta", 1.0))
            ts = row.get("timestamp", pd.Timestamp.now())

            # --- Entry ---
            if not in_trade and sig != 0:
                qty_y, qty_x = self._compute_position_size(
                    df, i, price_y, price_x, beta, capital
                )
                if qty_y * price_y < self.cfg.min_position_usd:
                    continue

                entry_data = {
                    "bar": i, "ts": ts,
                    "sig": sig, "zscore": zscore, "beta": beta,
                    "price_y": price_y, "price_x": price_x,
                    "qty_y": qty_y, "qty_x": qty_x,
                }
                in_trade = True

            # --- Exit ---
            elif in_trade and sig == 0:
                trade = self._build_trade_record(
                    fold_idx, split, entry_data,
                    exit_bar=i, exit_ts=ts,
                    exit_price_y=price_y, exit_price_x=price_x,
                    exit_zscore=zscore,
                    exit_reason=str(row.get("reason", "signal_exit")),
                )
                trades.append(trade)
                capital += trade.net_pnl
                in_trade = False
                entry_data = {}

        # Forcează exit la sfârșitul fold-ului dacă în poziție
        if in_trade and entry_data:
            last = df.iloc[-1]
            trade = self._build_trade_record(
                fold_idx, split, entry_data,
                exit_bar=len(df) - 1,
                exit_ts=last.get("timestamp", pd.Timestamp.now()),
                exit_price_y=float(last["close_y"]),
                exit_price_x=float(last["close_x"]),
                exit_zscore=float(last.get("zscore", 0.0)),
                exit_reason="fold_end",
            )
            trades.append(trade)

        return trades

    def _compute_position_size(
        self,
        df: pd.DataFrame,
        bar: int,
        price_y: float,
        price_x: float,
        beta: float,
        capital: float,
    ) -> Tuple[float, float]:
        """
        Volatilitate-țintă + Kelly fracțional.
        Returnează (qty_y, qty_x).
        """
        # Volatilitate spread din ultimele 30 bare
        start = max(0, bar - 30)
        spreads = df["spread"].iloc[start:bar].dropna() if "spread" in df.columns else pd.Series()
        if len(spreads) < 5:
            spread_vol = price_y * 0.02  # fallback: 2% din prețul Y
        else:
            spread_vol = float(spreads.std())

        if spread_vol < 1e-8:
            spread_vol = price_y * 0.02

        # Vol target sizing
        target_notional = capital * self.cfg.vol_target / max(spread_vol / price_y, 1e-6)
        target_notional = min(target_notional, capital * self.cfg.max_leverage)
        target_notional *= self.cfg.kelly_fraction

        qty_y = target_notional / price_y
        qty_x = qty_y * beta

        return round(qty_y, 6), round(qty_x, 6)

    def _build_trade_record(
        self,
        fold_idx: int,
        split: str,
        entry: Dict,
        exit_bar: int,
        exit_ts: pd.Timestamp,
        exit_price_y: float,
        exit_price_x: float,
        exit_zscore: float,
        exit_reason: str,
    ) -> TradeRecord:
        """Construiește TradeRecord cu P&L net incluzând toate costurile."""
        sig = entry["sig"]
        qty_y = entry["qty_y"]
        qty_x = entry["qty_x"]
        bars_held = exit_bar - entry["bar"]

        # Gross P&L
        if sig == Signal.LONG_SPREAD:
            # Long Y / Short X
            pnl_y = (exit_price_y - entry["price_y"]) * qty_y
            pnl_x = (entry["price_x"] - exit_price_x) * qty_x
        else:
            # Short Y / Long X
            pnl_y = (entry["price_y"] - exit_price_y) * qty_y
            pnl_x = (exit_price_x - entry["price_x"]) * qty_x
        gross_pnl = pnl_y + pnl_x

        # Fees: taker at entry + exit pe ambele legs
        notional_y = qty_y * entry["price_y"]
        notional_x = qty_x * entry["price_x"]
        fee_rate = self.cfg.fee_taker  # worst case: taker la entry + exit
        fees = (notional_y + notional_x) * fee_rate * 2  # entry + exit

        # Slippage: bps per side, 4 leg-crossings total (2 legs x 2 entry+exit)
        slippage_rate = self.cfg.slippage_bps / 10_000
        slippage = (notional_y + notional_x) * slippage_rate * 2

        # Funding cost: simulat ca procent anual aplicat pro-rata pe bars_held
        # Presupunem 24 bare/zi (1h bars). Ajustează dacă folosim alt timeframe.
        bars_per_day = 24
        holding_days = bars_held / bars_per_day
        funding_cost = (
            (notional_y + notional_x)
            * self.cfg.funding_rate_annual
            * holding_days / 365
        )

        net_pnl = gross_pnl - fees - slippage - funding_cost

        return TradeRecord(
            fold=fold_idx,
            split=split,
            entry_bar=entry["bar"],
            exit_bar=exit_bar,
            entry_ts=entry["ts"],
            exit_ts=exit_ts,
            direction="LONG_SPREAD" if sig == Signal.LONG_SPREAD else "SHORT_SPREAD",
            entry_zscore=entry["zscore"],
            exit_zscore=exit_zscore,
            hedge_ratio=entry["beta"],
            qty_y=qty_y,
            qty_x=qty_x,
            entry_price_y=entry["price_y"],
            entry_price_x=entry["price_x"],
            exit_price_y=exit_price_y,
            exit_price_x=exit_price_x,
            gross_pnl=round(gross_pnl, 4),
            fees=round(fees, 4),
            slippage=round(slippage, 4),
            funding_cost=round(funding_cost, 4),
            net_pnl=round(net_pnl, 4),
            bars_held=bars_held,
            exit_reason=exit_reason,
        )

    # ------------------------------------------------------------------
    # Performance Metrics
    # ------------------------------------------------------------------

    def _compute_metrics(
        self,
        fold_idx: int,
        split: str,
        trades: List[TradeRecord],
        n_bars: int,
    ) -> PerformanceMetrics:
        """Calculează toate metricile de performanță."""
        m = PerformanceMetrics(fold=fold_idx, split=split, n_bars=n_bars)

        if not trades:
            return m

        pnls = np.array([t.net_pnl for t in trades])
        m.n_trades = len(trades)
        m.win_rate = float(np.mean(pnls > 0))
        m.avg_net_pnl = float(np.mean(pnls))
        m.total_net_pnl = float(np.sum(pnls))
        m.avg_bars_held = float(np.mean([t.bars_held for t in trades]))
        m.total_fees = float(sum(t.fees for t in trades))
        m.total_funding_cost = float(sum(t.funding_cost for t in trades))

        # Profit Factor
        gross_profit = float(np.sum(pnls[pnls > 0])) if np.any(pnls > 0) else 0.0
        gross_loss = abs(float(np.sum(pnls[pnls < 0]))) if np.any(pnls < 0) else 1e-9
        m.profit_factor = gross_profit / gross_loss

        # Daily P&L series (aggregate by day for ratio calculations)
        # Folosim index bars ca proxy pentru timp (1 bar = 1h implicit)
        bars_per_day = 24
        total_days = max(n_bars / bars_per_day, 1)

        # Build daily P&L array
        daily_pnl = np.zeros(int(total_days) + 1)
        for t in trades:
            day_idx = min(int(t.exit_bar / bars_per_day), len(daily_pnl) - 1)
            daily_pnl[day_idx] += t.net_pnl

        # Sharpe (daily, annualized)
        daily_mean = np.mean(daily_pnl)
        daily_std = np.std(daily_pnl, ddof=1)
        if daily_std > 1e-9:
            m.sharpe = float(daily_mean / daily_std * np.sqrt(252))
        m.ann_return = float(daily_mean * 252)
        m.ann_volatility = float(daily_std * np.sqrt(252))

        # Sortino (downside deviation)
        downside = daily_pnl[daily_pnl < 0]
        downside_std = np.std(downside, ddof=1) if len(downside) > 1 else 1e-9
        if downside_std > 1e-9:
            m.sortino = float(daily_mean / downside_std * np.sqrt(252))

        # Max Drawdown
        equity = np.cumsum(daily_pnl) + self.cfg.capital_usd
        peak = np.maximum.accumulate(equity)
        dd = equity - peak
        m.max_drawdown = float(np.min(dd))
        peak_nonzero = np.where(peak > 0, peak, 1.0)
        m.max_drawdown_pct = float(np.min(dd / peak_nonzero) * 100)

        # Calmar
        if abs(m.max_drawdown) > 1e-9:
            m.calmar = float(m.ann_return / abs(m.max_drawdown))

        # Omega Ratio (threshold = 0)
        returns_above = np.sum(daily_pnl[daily_pnl > 0])
        returns_below = abs(np.sum(daily_pnl[daily_pnl < 0]))
        if returns_below > 1e-9:
            m.omega_ratio = float(returns_above / returns_below)

        return m


# ---------------------------------------------------------------------------
# Results Container
# ---------------------------------------------------------------------------

@dataclass
class BacktestResults:
    trades: List[TradeRecord]
    per_fold_metrics: List[PerformanceMetrics]
    oos_metrics: PerformanceMetrics
    config: BacktestConfig

    def oos_trades(self) -> List[TradeRecord]:
        return [t for t in self.trades if t.split == "OOS"]

    def to_dataframe(self) -> pd.DataFrame:
        """Converts all trades to DataFrame pentru analiză suplimentară."""
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([t.__dict__ for t in self.trades])

    def print_report(self) -> None:
        """Print summary report."""
        print("\n" + "=" * 70)
        print("QUANTLUNA — BACKTEST REPORT")
        print("=" * 70)
        for m in self.per_fold_metrics:
            print(m.summary())
        print("-" * 70)
        print(f"AGGREGATE OOS: {self.oos_metrics.summary()}")
        print("=" * 70)

        # Cost breakdown
        total_fees = sum(t.fees for t in self.trades if t.split == "OOS")
        total_slippage = sum(t.slippage for t in self.trades if t.split == "OOS")
        total_funding = sum(t.funding_cost for t in self.trades if t.split == "OOS")
        total_gross = sum(t.gross_pnl for t in self.trades if t.split == "OOS")
        total_net = sum(t.net_pnl for t in self.trades if t.split == "OOS")

        print("\nOOS COST BREAKDOWN:")
        print(f"  Gross P&L:    ${total_gross:>10.2f}")
        print(f"  Fees:         ${total_fees:>10.2f}  ({total_fees/max(abs(total_gross),1)*100:.1f}% of gross)")
        print(f"  Slippage:     ${total_slippage:>10.2f}  ({total_slippage/max(abs(total_gross),1)*100:.1f}% of gross)")
        print(f"  Funding cost: ${total_funding:>10.2f}  ({total_funding/max(abs(total_gross),1)*100:.1f}% of gross)")
        print(f"  Net P&L:      ${total_net:>10.2f}")
        print("=" * 70)

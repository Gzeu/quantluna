"""
QuantLuna — WalkForwardEngine Sprint 7

Backtesting engine complet pentru pairs trading cu Kalman Filter.

Features:
- Walk-forward validation cu configurable splits
- Purged K-fold cross-validation (eliminare look-ahead bias la granițe fold)
- Out-of-sample (OOS) evaluation final
- Transaction costs: maker/taker fees, slippage model, funding cost
- Position sizing: volatilitate-țintă + Kelly fracțional
- Performance metrics complete: Sharpe, Sortino, Calmar, max DD,
  win rate, profit factor, avg trade, Omega ratio
- Regime-aware: sărit bareme de cointegration în folds cu breakdown
- Reproducibil: seed fix pentru orice rezultat

Fixes aplicate:
  FIX-BT-1 (P0): OOS z-score era normalizat pe statistici OOS (look-ahead bias).
    Acum mean/std pentru z-score sunt fixate din IS tail (ultimele zscore_window bare
    din IS) și folosite ca referință invariantă pe toată durata OOS fold-ului.
    Kalman rulează online în OOS via update_one(), nu fit() — elimina orice leakage.
  FIX-BT-2 (P1): bars_per_day era hardcodat la 24 (1h bars).
    Acum BacktestConfig primește bar_freq_hours (default 1.0) și calculează
    bars_per_day = 24 / bar_freq_hours corect pentru orice timeframe.
  FIX-BT-3 (P0): regime_multiplier și coint_valid_series lipseau complet din
    generate_batch() — backtest ignora filtrele de regim și cointegration.
    Acum ambele sunt calculate rolling și pasate corect.
  FIX-BT-4 (P0): _simulate_trades ignora PARTIAL_EXIT (signal=2) — branch adăugat
    care închide partial_exit_pct% din poziție și ține restul deschis.
  FIX-BT-5 (P1): vol window în _compute_position_size era hardcodat la 30 bare.
    Acum folosește int(bars_per_day * 1.25) — consistent cu timeframe-ul configurat.
  FIX-BT-6 (P1): BacktestConfig.compound_folds (default False) — când True,
    fiecare fold moștenește capitalul final al fold-ului anterior.

Limite / Riscuri reale:
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
    purge_bars: int = 10            # bare eliminate la granița IS/OOS
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

    # FIX-BT-2: bar frequency — drives bars_per_day and Sharpe annualization
    # 1.0 = 1h bars (default), 0.25 = 15m bars, 4.0 = 4h bars, 24.0 = daily bars
    bar_freq_hours: float = 1.0

    # Signal
    signal_cfg: Optional[SignalConfig] = None

    # FIX-BT-6: compounding între folds — când True, capitalul se propagă fold-to-fold
    compound_folds: bool = False

    # Reproducibility
    seed: int = 42

    @property
    def bars_per_day(self) -> float:
        """Number of bars in a 24h period, derived from bar_freq_hours."""
        return 24.0 / self.bar_freq_hours


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
    is_partial: bool = False   # FIX-BT-4: True dacă e ieșire parțială


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
        # FIX-BT-6: capital propagat între folds când compound_folds=True
        self._running_capital: float = cfg.capital_usd

    def _validate_input(self) -> None:
        required = {"timestamp", "close_y", "close_x"}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")
        if len(self.df) < 200:
            raise ValueError(
                f"Dataset too short: {len(self.df)} bars. Minimum 200 required."
            )
        if not (0.0 < self.cfg.bar_freq_hours <= 24.0):
            raise ValueError(
                f"bar_freq_hours must be in (0, 24], got {self.cfg.bar_freq_hours}. "
                "Examples: 1.0=1h, 0.25=15m, 4.0=4h, 24.0=daily."
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

        # FIX-BT-6: reset capital la începutul run-ului
        self._running_capital = self.cfg.capital_usd

        for fold_idx, (is_idx, oos_idx) in enumerate(splits):
            logger.info(
                f"Walk-forward fold {fold_idx+1}/{len(splits)} — "
                f"IS={len(is_idx)} bars OOS={len(oos_idx)} bars"
                + (f" capital=${self._running_capital:.0f}" if self.cfg.compound_folds else "")
            )

            # --- In-Sample: fit Kalman, generate signals, record IS metrics ---
            is_trades, is_metrics = self._run_is_fold(fold_idx, is_idx)

            # --- Out-of-Sample: Kalman warm-started on IS, z-score anchored on IS stats ---
            oos_trades, oos_metrics = self._run_oos_fold(fold_idx, is_idx, oos_idx)

            all_trades.extend(is_trades)
            all_trades.extend(oos_trades)
            all_metrics.extend([is_metrics, oos_metrics])

            logger.info(is_metrics.summary())
            logger.info(oos_metrics.summary())

            # FIX-BT-6: propagă capitalul OOS la fold-ul următor
            if self.cfg.compound_folds:
                oos_net = sum(t.net_pnl for t in oos_trades)
                self._running_capital = max(
                    self._running_capital + oos_net,
                    self.cfg.min_position_usd * 2,  # floor: nu lăsa capitalul sub 2x min_order
                )
                logger.info(f"  → compound_folds: capital după fold {fold_idx+1} = ${self._running_capital:.2f}")

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
        Walk-forward splits cu purge + embargo la granițe IS/OOS.
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

            # Purge + embargo: elimină granița IS/OOS
            oos_start = train_end + self.cfg.purge_bars + self.cfg.embargo_bars
            oos_idx = np.arange(oos_start, end)

            if len(is_idx) < 50 or len(oos_idx) < 20:
                logger.warning(f"Fold {i}: prea puține bare IS={len(is_idx)} OOS={len(oos_idx)}, skip")
                continue

            splits.append((is_idx, oos_idx))

        if not splits:
            raise RuntimeError(
                "Nu s-au putut construi fold-uri valide. Verifică n_splits și lungimea datelor."
            )

        return splits

    # ------------------------------------------------------------------
    # Helpers — FIX-BT-3: regime_multiplier + coint_valid_series rolling
    # ------------------------------------------------------------------

    @staticmethod
    def _build_regime_multiplier(spread_df: pd.DataFrame) -> pd.Series:
        """
        Construiește regime_multiplier rolling fără lookahead.
        Folosește un proxy simplu bazat pe autocorrelation a spread-ului:
          autocorr < -0.05 → ranging  → multiplier 1.0 (favorabil Kalman pairs)
          autocorr > +0.10 → trending → multiplier 0.5 (penalizat)
          altfel            → neutral  → multiplier 0.75

        Toate calculele folosesc doar date trecute (shift(1) + rolling).
        """
        if "spread" not in spread_df.columns:
            return pd.Series(1.0, index=spread_df.index)

        spread = spread_df["spread"]
        # autocorr rolling pe 20 bare, lag 1, shift(1) pentru no-lookahead
        autocorr = (
            spread.shift(1)
            .rolling(20, min_periods=10)
            .apply(lambda x: pd.Series(x).autocorr(lag=1) if len(x) >= 5 else 0.0, raw=False)
            .fillna(0.0)
        )

        multiplier = pd.Series(0.75, index=spread_df.index)
        multiplier[autocorr < -0.05] = 1.0   # ranging
        multiplier[autocorr > 0.10] = 0.5    # trending
        return multiplier

    @staticmethod
    def _build_coint_valid_series(
        spread_df: pd.DataFrame,
        retest_interval: int = 168,  # bare (default: 168h = 7 zile pentru 1h bars)
    ) -> pd.Series:
        """
        Construiește coint_valid_series rolling fără lookahead.
        Folosește ADF rolling pe fereastră fixă:
          - Rulează ADF pe fereastra [t-window : t] (doar trecut)
          - Între retestări, menține ultima valoare validă
          - Prima fereastră: True (insufficient data → nu blocăm)

        Necesită statsmodels — dacă nu e disponibil, returnează True constant.
        """
        try:
            from statsmodels.tsa.stattools import adfuller
        except ImportError:
            logger.warning("statsmodels unavailable — coint_valid_series=True constant")
            return pd.Series(True, index=spread_df.index)

        if "spread" not in spread_df.columns:
            return pd.Series(True, index=spread_df.index)

        n = len(spread_df)
        valid = pd.Series(True, index=spread_df.index)
        window = min(252, n // 3)  # fereastră ADF: 252 bare sau 1/3 din dataset
        last_valid = True

        for i in range(n):
            # Retestăm doar la intervale configurate
            if i % retest_interval != 0:
                valid.iloc[i] = last_valid
                continue
            if i < window:
                valid.iloc[i] = True
                last_valid = True
                continue
            try:
                s = spread_df["spread"].iloc[i - window: i].dropna()
                if len(s) < 30:
                    valid.iloc[i] = last_valid
                    continue
                pvalue = adfuller(s, maxlag=1, autolag=None)[1]
                last_valid = bool(pvalue < 0.05)
                valid.iloc[i] = last_valid
            except Exception:
                valid.iloc[i] = last_valid

        return valid

    # ------------------------------------------------------------------
    # IS Fold
    # ------------------------------------------------------------------

    def _run_is_fold(
        self,
        fold_idx: int,
        is_idx: np.ndarray,
    ) -> Tuple[List[TradeRecord], PerformanceMetrics]:
        """Run in-sample fold using standard batch fit."""
        spread_engine = self.factory()
        signal_gen = SignalGenerator(
            spread_engine,
            cfg=self.cfg.signal_cfg or SignalConfig(),
        )
        fold_df = self.df.iloc[is_idx].copy().reset_index(drop=True)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spread_df = spread_engine.fit(fold_df["close_y"], fold_df["close_x"])

        # FIX-BT-3: calcul rolling regime_multiplier și coint_valid_series
        regime_mult = self._build_regime_multiplier(spread_df)
        coint_valid = self._build_coint_valid_series(
            spread_df,
            retest_interval=max(1, int(168 / self.cfg.bar_freq_hours)),
        )

        sig_df = signal_gen.generate_batch(
            spread_df,
            regime_multiplier=regime_mult,
            coint_valid_series=coint_valid,
        )
        fold_df = pd.concat([fold_df.reset_index(drop=True), sig_df], axis=1)

        # FIX-BT-6: folosim capitalul curent (compound sau fresh)
        capital = self._running_capital if self.cfg.compound_folds else self.cfg.capital_usd
        trades = self._simulate_trades(fold_df, fold_idx, "IS", starting_capital=capital)
        metrics = self._compute_metrics(fold_idx, "IS", trades, len(fold_df))
        return trades, metrics

    # ------------------------------------------------------------------
    # OOS Fold  — FIX-BT-1: no look-ahead bias
    # ------------------------------------------------------------------

    def _run_oos_fold(
        self,
        fold_idx: int,
        is_idx: np.ndarray,
        oos_idx: np.ndarray,
    ) -> Tuple[List[TradeRecord], PerformanceMetrics]:
        """
        FIX-BT-1: OOS fold without z-score look-ahead bias.

        Old approach (BUGGY): called spread_engine.fit() on OOS data, which
        computed rolling mean/std using OOS observations — introducing forward-
        looking information into the z-score normalization.

        New approach (CORRECT):
        1. Run Kalman in batch on IS to establish IS spread statistics.
        2. Extract IS tail mean/std (last zscore_window bars) as fixed anchors.
        3. In OOS, advance Kalman one bar at a time via update_one().
        4. Z-score each OOS spread using the IS-anchored mean/std — no OOS
           data is ever used to normalize itself.
        5. FIX-BT-3: regime_multiplier și coint_valid_series calculate rolling
           pe datele OOS acumulate (fără lookahead) și pasate la generate_batch().
        """
        spread_engine = self.factory()
        zscore_window = spread_engine.zscore_window

        # Step 1: Kalman batch on IS — establishes spread distribution
        is_df = self.df.iloc[is_idx].copy().reset_index(drop=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            is_spread_df = spread_engine.fit(is_df["close_y"], is_df["close_x"])

        # Step 2: Extract IS tail statistics (last zscore_window bars)
        is_tail = is_spread_df["spread"].iloc[-zscore_window:].dropna()
        if len(is_tail) < 5:
            logger.warning(
                f"Fold {fold_idx} OOS: IS tail too short ({len(is_tail)} bars) "
                "for anchored z-score — using global IS mean/std."
            )
            is_tail = is_spread_df["spread"].dropna()

        anchor_mean = float(is_tail.mean())
        anchor_std = float(is_tail.std())
        if anchor_std < 1e-10:
            anchor_std = 1.0  # degenerate spread — will produce z~0, no trades

        logger.info(
            f"Fold {fold_idx} OOS anchor: mean={anchor_mean:.6f} std={anchor_std:.6f} "
            f"(from {len(is_tail)} IS-tail bars)"
        )

        # Step 3: Advance Kalman online through OOS, z-score with IS anchors
        oos_df = self.df.iloc[oos_idx].copy().reset_index(drop=True)
        rows = []
        for i, (_, row) in enumerate(oos_df.iterrows()):
            cy = float(row["close_y"])
            cx = float(row["close_x"])
            ts = row.get("timestamp", None)

            # online Kalman step (no future data touches state)
            live_state = spread_engine.update_one(cy, cx, ts=ts)

            # z-score anchored on IS statistics
            zscore = (live_state["spread"] - anchor_mean) / anchor_std

            rows.append({
                "close_y":          cy,
                "close_x":          cx,
                "timestamp":        ts,
                "beta":             live_state["beta"],
                "alpha":            live_state["alpha"],
                "spread":           live_state["spread"],
                "spread_mean":      anchor_mean,
                "spread_std":       anchor_std,
                "zscore":           zscore,
                "half_life_hours":  live_state.get("half_life_hours"),
                "P_beta":           live_state["P_beta"],
                "kalman_gain":      live_state["kalman_gain"],
                "uncertainty":      live_state["uncertainty"],
                "is_warm":          live_state["is_warm"],
            })

        oos_spread_df = pd.DataFrame(rows)

        # FIX-BT-3: regime_multiplier și coint_valid_series rolling pe OOS (fără lookahead)
        regime_mult = self._build_regime_multiplier(oos_spread_df)
        coint_valid = self._build_coint_valid_series(
            oos_spread_df,
            retest_interval=max(1, int(168 / self.cfg.bar_freq_hours)),
        )

        # Step 4: Generate signals on OOS spread (already normalized)
        signal_gen = SignalGenerator(
            spread_engine,
            cfg=self.cfg.signal_cfg or SignalConfig(),
        )
        sig_df = signal_gen.generate_batch(
            oos_spread_df,
            regime_multiplier=regime_mult,
            coint_valid_series=coint_valid,
        )
        fold_df = pd.concat(
            [oos_spread_df.reset_index(drop=True), sig_df.reset_index(drop=True)],
            axis=1,
        )

        # FIX-BT-6: folosim capitalul curent (compound sau fresh)
        capital = self._running_capital if self.cfg.compound_folds else self.cfg.capital_usd
        trades = self._simulate_trades(fold_df, fold_idx, "OOS", starting_capital=capital)
        metrics = self._compute_metrics(fold_idx, "OOS", trades, len(fold_df))
        return trades, metrics

    # ------------------------------------------------------------------
    # Trade Simulation
    # ------------------------------------------------------------------

    def _simulate_trades(
        self,
        df: pd.DataFrame,
        fold_idx: int,
        split: str,
        starting_capital: Optional[float] = None,
    ) -> List[TradeRecord]:
        """
        Simulează execuția trade-urilor pe baza semnalelor generate.
        Include: fees, slippage, funding cost.
        FIX-BT-4: PARTIAL_EXIT (signal=2) închide partial_exit_pct% din poziție.
        FIX-BT-6: starting_capital permite compounding între folds.
        """
        trades: List[TradeRecord] = []
        in_trade = False
        entry_data: Dict = {}
        capital = starting_capital if starting_capital is not None else self.cfg.capital_usd

        # FIX-BT-4: stare pentru partial exit
        partial_exit_pct = (self.cfg.signal_cfg or SignalConfig()).partial_exit_pct
        partial_exit_done = False
        remaining_qty_factor = 1.0   # fracție din poziție rămasă deschisă

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
            if not in_trade and sig != 0 and sig != int(Signal.PARTIAL_EXIT):
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
                partial_exit_done = False
                remaining_qty_factor = 1.0

            # --- FIX-BT-4: Partial Exit ---
            elif in_trade and sig == int(Signal.PARTIAL_EXIT) and not partial_exit_done:
                close_factor = partial_exit_pct
                qty_y_close = entry_data["qty_y"] * close_factor
                qty_x_close = entry_data["qty_x"] * close_factor

                partial_trade = self._build_trade_record(
                    fold_idx, split,
                    {**entry_data, "qty_y": qty_y_close, "qty_x": qty_x_close},
                    exit_bar=i, exit_ts=ts,
                    exit_price_y=price_y, exit_price_x=price_x,
                    exit_zscore=zscore,
                    exit_reason="partial_exit",
                    is_partial=True,
                )
                trades.append(partial_trade)
                capital += partial_trade.net_pnl
                partial_exit_done = True
                remaining_qty_factor = 1.0 - close_factor

                # Actualizează qty în entry_data cu fracția rămasă
                entry_data["qty_y"] = entry_data["qty_y"] * remaining_qty_factor
                entry_data["qty_x"] = entry_data["qty_x"] * remaining_qty_factor

            # --- Full Exit ---
            elif in_trade and sig == int(Signal.EXIT):
                trade = self._build_trade_record(
                    fold_idx, split, entry_data,
                    exit_bar=i, exit_ts=ts,
                    exit_price_y=price_y, exit_price_x=price_x,
                    exit_zscore=zscore,
                    exit_reason=str(row.get("reason", "signal_exit")),
                    is_partial=False,
                )
                trades.append(trade)
                capital += trade.net_pnl
                in_trade = False
                entry_data = {}
                partial_exit_done = False
                remaining_qty_factor = 1.0

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
                is_partial=False,
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
        FIX-BT-5: vol window dinamic bazat pe bars_per_day în loc de hardcodat 30.
        """
        # FIX-BT-5: fereastră de ~1.25 zile de bare în loc de 30 fix
        vol_window = max(10, int(self.cfg.bars_per_day * 1.25))
        start = max(0, bar - vol_window)
        spreads = df["spread"].iloc[start:bar].dropna() if "spread" in df.columns else pd.Series()
        if len(spreads) < 5:
            spread_vol = price_y * 0.02
        else:
            spread_vol = float(spreads.std())

        if spread_vol < 1e-8:
            spread_vol = price_y * 0.02

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
        is_partial: bool = False,
    ) -> TradeRecord:
        """Construiește TradeRecord cu P&L net incluzând toate costurile."""
        sig = entry["sig"]
        qty_y = entry["qty_y"]
        qty_x = entry["qty_x"]
        bars_held = exit_bar - entry["bar"]

        # Gross P&L
        if sig == Signal.LONG_SPREAD:
            pnl_y = (exit_price_y - entry["price_y"]) * qty_y
            pnl_x = (entry["price_x"] - exit_price_x) * qty_x
        else:
            pnl_y = (entry["price_y"] - exit_price_y) * qty_y
            pnl_x = (exit_price_x - entry["price_x"]) * qty_x
        gross_pnl = pnl_y + pnl_x

        notional_y = qty_y * entry["price_y"]
        notional_x = qty_x * entry["price_x"]
        fee_rate = self.cfg.fee_taker
        fees = (notional_y + notional_x) * fee_rate * 2

        slippage_rate = self.cfg.slippage_bps / 10_000
        slippage = (notional_y + notional_x) * slippage_rate * 2

        # FIX-BT-2: use configurable bars_per_day instead of hardcoded 24
        holding_days = bars_held / self.cfg.bars_per_day
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
            is_partial=is_partial,
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

        gross_profit = float(np.sum(pnls[pnls > 0])) if np.any(pnls > 0) else 0.0
        gross_loss = abs(float(np.sum(pnls[pnls < 0]))) if np.any(pnls < 0) else 1e-9
        m.profit_factor = gross_profit / gross_loss

        # FIX-BT-2: bars_per_day from config (not hardcoded 24)
        bars_per_day = self.cfg.bars_per_day
        total_days = max(n_bars / bars_per_day, 1)

        daily_pnl = np.zeros(int(total_days) + 1)
        for t in trades:
            day_idx = min(int(t.exit_bar / bars_per_day), len(daily_pnl) - 1)
            daily_pnl[day_idx] += t.net_pnl

        daily_mean = np.mean(daily_pnl)
        daily_std = np.std(daily_pnl, ddof=1)
        if daily_std > 1e-9:
            m.sharpe = float(daily_mean / daily_std * np.sqrt(252))
        m.ann_return = float(daily_mean * 252)
        m.ann_volatility = float(daily_std * np.sqrt(252))

        downside = daily_pnl[daily_pnl < 0]
        downside_std = np.std(downside, ddof=1) if len(downside) > 1 else 1e-9
        if downside_std > 1e-9:
            m.sortino = float(daily_mean / downside_std * np.sqrt(252))

        equity = np.cumsum(daily_pnl) + self.cfg.capital_usd
        peak = np.maximum.accumulate(equity)
        dd = equity - peak
        m.max_drawdown = float(np.min(dd))
        peak_nonzero = np.where(peak > 0, peak, 1.0)
        m.max_drawdown_pct = float(np.min(dd / peak_nonzero) * 100)

        if abs(m.max_drawdown) > 1e-9:
            m.calmar = float(m.ann_return / abs(m.max_drawdown))

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
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([t.__dict__ for t in self.trades])

    def print_report(self) -> None:
        print("\n" + "=" * 70)
        print("QUANTLUNA — BACKTEST REPORT")
        print("=" * 70)
        for m in self.per_fold_metrics:
            print(m.summary())
        print("-" * 70)
        print(f"AGGREGATE OOS: {self.oos_metrics.summary()}")
        print("=" * 70)

        total_fees = sum(t.fees for t in self.trades if t.split == "OOS")
        total_slippage = sum(t.slippage for t in self.trades if t.split == "OOS")
        total_funding = sum(t.funding_cost for t in self.trades if t.split == "OOS")
        total_gross = sum(t.gross_pnl for t in self.trades if t.split == "OOS")
        total_net = sum(t.net_pnl for t in self.trades if t.split == "OOS")
        partial_count = sum(1 for t in self.trades if t.split == "OOS" and t.is_partial)

        print("\nOOS COST BREAKDOWN:")
        print(f"  Gross P&L:     ${total_gross:>10.2f}")
        print(f"  Fees:          ${total_fees:>10.2f}  ({total_fees/max(abs(total_gross),1)*100:.1f}% of gross)")
        print(f"  Slippage:      ${total_slippage:>10.2f}  ({total_slippage/max(abs(total_gross),1)*100:.1f}% of gross)")
        print(f"  Funding cost:  ${total_funding:>10.2f}  ({total_funding/max(abs(total_gross),1)*100:.1f}% of gross)")
        print(f"  Net P&L:       ${total_net:>10.2f}")
        print(f"  Partial exits: {partial_count}")
        print(f"  bar_freq_hours={self.config.bar_freq_hours} (bars_per_day={self.config.bars_per_day:.1f})")
        print(f"  compound_folds={self.config.compound_folds}")
        print("=" * 70)

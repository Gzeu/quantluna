"""
strategy/optimizer.py  —  QuantLuna Hyperparameter Optimizer

Sprint 12 — Optuna-based parameter search pentru:
  - Kalman Filter: delta, R
  - Signal: zscore_entry, zscore_exit
  - Risk: kelly_fraction, vol_target
  - Regime: stability gate params

Features:
  - Optuna TPE sampler (Tree-structured Parzen Estimator)
  - Walk-forward aware: optimizare pe train set, evaluare pe test set
  - Objective: Sharpe ratio (customizabil: Sortino, Calmar, profit_factor)
  - Pruning: MedianPruner — taie trial-uri slabe devreme
  - Parallel trials: n_jobs=-1 (toate CPU-urile)
  - Reproducibil: seed configurabil
  - Rezultate salvate în SQLite Optuna storage (opțional)
  - Export best params ca dict / JSON / LiveConfig patch

Usage:
    from strategy.optimizer import QuantLunaOptimizer, OptimizerConfig
    from data.market_data_cache import MarketDataCache

    cache = MarketDataCache()
    ohlcv_y = cache.load("BTCUSDT", "bybit", "1h")
    ohlcv_x = cache.load("ETHUSDT", "bybit", "1h")

    opt = QuantLunaOptimizer(OptimizerConfig(
        n_trials=200,
        n_jobs=4,
        objective="sharpe",
        train_ratio=0.7,
        seed=42,
    ))
    best = opt.optimize(ohlcv_y, ohlcv_x)
    print(best.params)        # best hyperparams
    print(best.sharpe_test)   # out-of-sample Sharpe
    best.save_json("best_params.json")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Literal, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ObjectiveType = Literal["sharpe", "sortino", "calmar", "profit_factor"]


@dataclass
class SearchSpace:
    """Defines the hyperparameter search space bounds."""
    # Kalman Filter
    delta_low:  float = 1e-6
    delta_high: float = 1e-2
    R_low:      float = 1e-4
    R_high:     float = 1e-1
    # Signal / z-score
    zscore_entry_low:  float = 1.5
    zscore_entry_high: float = 3.5
    zscore_exit_low:   float = 0.1
    zscore_exit_high:  float = 1.0
    # Risk
    kelly_fraction_low:  float = 0.1
    kelly_fraction_high: float = 0.5
    vol_target_low:  float = 0.005
    vol_target_high: float = 0.03
    # Regime
    half_life_min_h_low:  float = 6.0
    half_life_min_h_high: float = 24.0
    half_life_max_h_low:  float = 48.0
    half_life_max_h_high: float = 240.0
    # Warmup
    min_warmup_bars_low:  int = 20
    min_warmup_bars_high: int = 80


@dataclass
class OptimizerConfig:
    n_trials: int = 150
    n_jobs: int = 1              # -1 = all CPUs (requires Optuna joblib backend)
    objective: ObjectiveType = "sharpe"
    train_ratio: float = 0.70    # 70% train, 30% test (no leakage)
    seed: int = 42
    search_space: SearchSpace = field(default_factory=SearchSpace)
    # Pruning
    pruning_enabled: bool = True
    pruning_warmup_steps: int = 10
    # Optuna storage (None = in-memory)
    storage_url: Optional[str] = None
    study_name: str = "quantluna_opt"
    # Backtest config
    bar_freq: str = "1h"
    capital_usdt: float = 10_000.0
    # Minimum trades for valid trial
    min_trades: int = 10


@dataclass
class OptimizationResult:
    params: Dict
    sharpe_train: float
    sharpe_test: float
    sortino_test: float
    calmar_test: float
    max_dd_test: float
    win_rate_test: float
    n_trades_test: int
    profit_factor_test: float
    n_trials_completed: int
    objective: str

    def save_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)
        logger.info(f"Best params saved to {path}")

    def print_summary(self) -> None:
        logger.info("=" * 60)
        logger.info("QuantLuna Optimization Result")
        logger.info(f"  Objective:        {self.objective}")
        logger.info(f"  Trials completed: {self.n_trials_completed}")
        logger.info(f"  Sharpe (train):   {self.sharpe_train:.3f}")
        logger.info(f"  Sharpe (test):    {self.sharpe_test:.3f}")
        logger.info(f"  Sortino (test):   {self.sortino_test:.3f}")
        logger.info(f"  Calmar (test):    {self.calmar_test:.3f}")
        logger.info(f"  Max DD (test):    {self.max_dd_test:.2%}")
        logger.info(f"  Win rate (test):  {self.win_rate_test:.2%}")
        logger.info(f"  Trades (test):    {self.n_trades_test}")
        logger.info(f"  Profit factor:    {self.profit_factor_test:.2f}")
        logger.info("  Best params:")
        for k, v in self.params.items():
            logger.info(f"    {k}: {v}")
        logger.info("=" * 60)


class QuantLunaOptimizer:
    """
    Optuna-based hyperparameter optimizer pentru QuantLuna.

    Folosește backtestul vectorizat intern pentru a evalua fiecare trial.
    Train/test split strict — fără leakage.
    """

    def __init__(self, config: OptimizerConfig) -> None:
        self.cfg = config

    def optimize(
        self,
        ohlcv_y: pd.DataFrame,
        ohlcv_x: pd.DataFrame,
    ) -> OptimizationResult:
        """
        Rulează căutarea de parametri și returnează cel mai bun rezultat.

        Args:
            ohlcv_y: OHLCV DataFrame pentru simbolul Y (index = DatetimeIndex)
            ohlcv_x: OHLCV DataFrame pentru simbolul X

        Returns:
            OptimizationResult cu best params + metrici out-of-sample
        """
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            raise ImportError(
                "Optuna not installed. Run: pip install optuna"
            )

        # Train / test split — strict non-leakage
        split_idx = int(len(ohlcv_y) * self.cfg.train_ratio)
        train_y = ohlcv_y.iloc[:split_idx].copy()
        train_x = ohlcv_x.iloc[:split_idx].copy()
        test_y  = ohlcv_y.iloc[split_idx:].copy()
        test_x  = ohlcv_x.iloc[split_idx:].copy()

        logger.info(
            f"Optimizer: {len(train_y)} train bars / {len(test_y)} test bars "
            f"| {self.cfg.n_trials} trials | objective={self.cfg.objective}"
        )

        sampler = optuna.samplers.TPESampler(seed=self.cfg.seed)
        pruner = (
            optuna.pruners.MedianPruner(
                n_startup_trials=5,
                n_warmup_steps=self.cfg.pruning_warmup_steps,
            )
            if self.cfg.pruning_enabled
            else optuna.pruners.NopPruner()
        )

        study = optuna.create_study(
            direction="maximize",
            sampler=sampler,
            pruner=pruner,
            storage=self.cfg.storage_url,
            study_name=self.cfg.study_name,
            load_if_exists=True,
        )

        study.optimize(
            lambda trial: self._objective(trial, train_y, train_x),
            n_trials=self.cfg.n_trials,
            n_jobs=self.cfg.n_jobs,
            show_progress_bar=True,
        )

        best_params = study.best_params
        sharpe_train = study.best_value

        logger.info(f"Best train {self.cfg.objective}: {sharpe_train:.3f}")
        logger.info(f"Evaluating best params on test set...")

        # Out-of-sample evaluation
        test_metrics = self._run_backtest(best_params, test_y, test_x)

        result = OptimizationResult(
            params=best_params,
            sharpe_train=sharpe_train,
            sharpe_test=test_metrics.get("sharpe", 0.0),
            sortino_test=test_metrics.get("sortino", 0.0),
            calmar_test=test_metrics.get("calmar", 0.0),
            max_dd_test=test_metrics.get("max_drawdown", 0.0),
            win_rate_test=test_metrics.get("win_rate", 0.0),
            n_trades_test=test_metrics.get("n_trades", 0),
            profit_factor_test=test_metrics.get("profit_factor", 0.0),
            n_trials_completed=len(study.trials),
            objective=self.cfg.objective,
        )
        result.print_summary()
        return result

    def _objective(self, trial, ohlcv_y: pd.DataFrame, ohlcv_x: pd.DataFrame) -> float:
        """Optuna objective — returns score to maximize."""
        import optuna
        ss = self.cfg.search_space
        params = {
            "delta":            trial.suggest_float("delta", ss.delta_low, ss.delta_high, log=True),
            "R":                trial.suggest_float("R", ss.R_low, ss.R_high, log=True),
            "zscore_entry":     trial.suggest_float("zscore_entry", ss.zscore_entry_low, ss.zscore_entry_high),
            "zscore_exit":      trial.suggest_float("zscore_exit", ss.zscore_exit_low, ss.zscore_exit_high),
            "kelly_fraction":   trial.suggest_float("kelly_fraction", ss.kelly_fraction_low, ss.kelly_fraction_high),
            "vol_target":       trial.suggest_float("vol_target", ss.vol_target_low, ss.vol_target_high),
            "half_life_min_h":  trial.suggest_float("half_life_min_h", ss.half_life_min_h_low, ss.half_life_min_h_high),
            "half_life_max_h":  trial.suggest_float("half_life_max_h", ss.half_life_max_h_low, ss.half_life_max_h_high),
            "min_warmup_bars":  trial.suggest_int("min_warmup_bars", ss.min_warmup_bars_low, ss.min_warmup_bars_high),
        }

        # Constraint: half_life_min < half_life_max
        if params["half_life_min_h"] >= params["half_life_max_h"]:
            raise optuna.exceptions.TrialPruned()
        # Constraint: zscore_exit < zscore_entry
        if params["zscore_exit"] >= params["zscore_entry"]:
            raise optuna.exceptions.TrialPruned()

        try:
            metrics = self._run_backtest(params, ohlcv_y, ohlcv_x)
        except Exception as exc:
            logger.debug(f"Trial {trial.number} failed: {exc}")
            raise optuna.exceptions.TrialPruned()

        if metrics.get("n_trades", 0) < self.cfg.min_trades:
            raise optuna.exceptions.TrialPruned()

        score = metrics.get(self.cfg.objective, 0.0)
        # Guard against NaN/Inf
        if not np.isfinite(score):
            raise optuna.exceptions.TrialPruned()
        return float(score)

    def _run_backtest(self, params: Dict, ohlcv_y: pd.DataFrame, ohlcv_x: pd.DataFrame) -> Dict:
        """
        Runs the vectorised backtest with given params.
        Returns metrics dict from PerformanceAnalytics.
        """
        from backtest.engine import BacktestEngine, BacktestConfig
        from backtest.analytics import PerformanceAnalytics

        bt_cfg = BacktestConfig(
            delta=params["delta"],
            R=params["R"],
            zscore_entry=params["zscore_entry"],
            zscore_exit=params["zscore_exit"],
            kelly_fraction=params.get("kelly_fraction", 0.25),
            vol_target=params.get("vol_target", 0.01),
            half_life_min_h=params.get("half_life_min_h", 12.0),
            half_life_max_h=params.get("half_life_max_h", 168.0),
            min_warmup_bars=params.get("min_warmup_bars", 30),
            capital_usdt=self.cfg.capital_usdt,
            bar_freq=self.cfg.bar_freq,
        )
        engine = BacktestEngine(bt_cfg)
        result = engine.run(ohlcv_y, ohlcv_x)
        metrics = PerformanceAnalytics.compute(
            equity_curve=result.equity_curve,
            trades=result.trades,
            freq_hours=_bar_freq_to_hours(self.cfg.bar_freq),
        )
        return metrics


def _bar_freq_to_hours(bar_freq: str) -> float:
    """Converts bar_freq string to float hours."""
    mapping = {
        "1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
        "1h": 1.0, "2h": 2.0, "4h": 4.0, "6h": 6.0,
        "8h": 8.0, "12h": 12.0, "1d": 24.0,
    }
    return mapping.get(bar_freq, 1.0)

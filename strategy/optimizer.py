"""
strategy/optimizer.py  —  QuantLuna Hyperparameter Optimizer

Sprint 12 — Optuna-based parameter search pentru:
  - Kalman Filter: delta, R
  - Signal: zscore_entry, zscore_exit
  - Risk: kelly_fraction, vol_target
  - Regime: stability gate params
  - KalmanScoringWeights (Sprint 19 / Fix #6 extension)

Features:
  - Optuna TPE sampler (Tree-structured Parzen Estimator)
  - Walk-forward aware: optimizare pe train set, evaluare pe test set
  - Objective: Sharpe ratio (customizabil: Sortino, Calmar, profit_factor)
  - Pruning: MedianPruner — taie trial-uri slabe devreme
  - Parallel trials: n_jobs=-1 (toate CPU-urile)
  - Reproducibil: seed configurabil
  - Rezultate salvate în SQLite Optuna storage (opțional)
  - Export best params ca dict / JSON / LiveConfig patch
  - KalmanScoringWeights în SearchSpace: toti parametrii score() sunt optimizabili

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
        optimize_kalman_score=True,
    ))
    best = opt.optimize(ohlcv_y, ohlcv_x)
    print(best.params)
    print(best.sharpe_test)
    best.save_json("best_params.json")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Literal, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ObjectiveType = Literal["sharpe", "sortino", "calmar", "profit_factor"]


@dataclass
class SearchSpace:
    """Defines the hyperparameter search space bounds."""
    delta_low: float = 1e-6
    delta_high: float = 1e-2
    R_low: float = 1e-4
    R_high: float = 1e-1
    zscore_entry_low: float = 1.5
    zscore_entry_high: float = 3.5
    zscore_exit_low: float = 0.1
    zscore_exit_high: float = 1.0
    kelly_fraction_low: float = 0.1
    kelly_fraction_high: float = 0.5
    vol_target_low: float = 0.005
    vol_target_high: float = 0.03
    half_life_min_h_low: float = 6.0
    half_life_min_h_high: float = 24.0
    half_life_max_h_low: float = 48.0
    half_life_max_h_high: float = 240.0
    min_warmup_bars_low: int = 20
    min_warmup_bars_high: int = 80

    ks_baseline_low: float = 0.45
    ks_baseline_high: float = 0.75
    ks_regime_ranging_low: float = 0.05
    ks_regime_ranging_high: float = 0.25
    ks_regime_trending_low: float = -0.35
    ks_regime_trending_high: float = -0.05
    ks_regime_breakout_low: float = -0.25
    ks_regime_breakout_high: float = 0.00
    ks_coint_p001_low: float = 0.05
    ks_coint_p001_high: float = 0.30
    ks_coint_p005_low: float = 0.02
    ks_coint_p005_high: float = 0.15
    ks_coint_p010_low: float = -0.35
    ks_coint_p010_high: float = -0.05
    ks_hl_optimal_bonus_low: float = 0.02
    ks_hl_optimal_bonus_high: float = 0.20
    ks_hl_long_penalty_low: float = -0.30
    ks_hl_long_penalty_high: float = -0.05
    ks_hl_short_penalty_low: float = -0.15
    ks_hl_short_penalty_high: float = 0.00
    ks_autocorr_good_low: float = 0.02
    ks_autocorr_good_high: float = 0.20
    ks_autocorr_bad_low: float = -0.30
    ks_autocorr_bad_high: float = -0.02
    ks_vol_rank_good_low: float = 0.00
    ks_vol_rank_good_high: float = 0.15
    ks_vol_rank_extreme_low: float = -0.30
    ks_vol_rank_extreme_high: float = -0.02
    ks_win_rate_good_low: float = 0.00
    ks_win_rate_good_high: float = 0.15
    ks_win_rate_bad_low: float = -0.20
    ks_win_rate_bad_high: float = -0.02


@dataclass
class OptimizerConfig:
    n_trials: int = 150
    n_jobs: int = 1
    objective: ObjectiveType = "sharpe"
    train_ratio: float = 0.70
    seed: int = 42
    search_space: SearchSpace = field(default_factory=SearchSpace)
    pruning_enabled: bool = True
    pruning_warmup_steps: int = 10
    storage_url: Optional[str] = None
    study_name: str = "quantluna_opt"
    bar_freq: str = "1h"
    capital_usdt: float = 10_000.0
    min_trades: int = 10
    optimize_kalman_score: bool = True


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
    def __init__(self, config: OptimizerConfig) -> None:
        self.cfg = config

    def optimize(
        self,
        ohlcv_y: pd.DataFrame,
        ohlcv_x: pd.DataFrame,
    ) -> OptimizationResult:
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            raise ImportError("Optuna not installed. Run: pip install optuna")

        split_idx = int(len(ohlcv_y) * self.cfg.train_ratio)
        train_y = ohlcv_y.iloc[:split_idx].copy()
        train_x = ohlcv_x.iloc[:split_idx].copy()
        test_y = ohlcv_y.iloc[split_idx:].copy()
        test_x = ohlcv_x.iloc[split_idx:].copy()

        logger.info(
            f"Optimizer: {len(train_y)} train bars / {len(test_y)} test bars "
            f"| {self.cfg.n_trials} trials | objective={self.cfg.objective} "
            f"| optimize_kalman_score={self.cfg.optimize_kalman_score}"
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
        import optuna

        ss = self.cfg.search_space
        params = {
            "delta": trial.suggest_float("delta", ss.delta_low, ss.delta_high, log=True),
            "R": trial.suggest_float("R", ss.R_low, ss.R_high, log=True),
            "zscore_entry": trial.suggest_float("zscore_entry", ss.zscore_entry_low, ss.zscore_entry_high),
            "zscore_exit": trial.suggest_float("zscore_exit", ss.zscore_exit_low, ss.zscore_exit_high),
            "kelly_fraction": trial.suggest_float("kelly_fraction", ss.kelly_fraction_low, ss.kelly_fraction_high),
            "vol_target": trial.suggest_float("vol_target", ss.vol_target_low, ss.vol_target_high),
            "half_life_min_h": trial.suggest_float("half_life_min_h", ss.half_life_min_h_low, ss.half_life_min_h_high),
            "half_life_max_h": trial.suggest_float("half_life_max_h", ss.half_life_max_h_low, ss.half_life_max_h_high),
            "min_warmup_bars": trial.suggest_int("min_warmup_bars", ss.min_warmup_bars_low, ss.min_warmup_bars_high),
        }

        if params["half_life_min_h"] >= params["half_life_max_h"]:
            raise optuna.exceptions.TrialPruned()
        if params["zscore_exit"] >= params["zscore_entry"]:
            raise optuna.exceptions.TrialPruned()

        if self.cfg.optimize_kalman_score:
            ks_params = {
                "ks_baseline": trial.suggest_float("ks_baseline", ss.ks_baseline_low, ss.ks_baseline_high),
                "ks_regime_ranging": trial.suggest_float("ks_regime_ranging", ss.ks_regime_ranging_low, ss.ks_regime_ranging_high),
                "ks_regime_trending": trial.suggest_float("ks_regime_trending", ss.ks_regime_trending_low, ss.ks_regime_trending_high),
                "ks_regime_breakout": trial.suggest_float("ks_regime_breakout", ss.ks_regime_breakout_low, ss.ks_regime_breakout_high),
                "ks_coint_p001": trial.suggest_float("ks_coint_p001", ss.ks_coint_p001_low, ss.ks_coint_p001_high),
                "ks_coint_p005": trial.suggest_float("ks_coint_p005", ss.ks_coint_p005_low, ss.ks_coint_p005_high),
                "ks_coint_p010": trial.suggest_float("ks_coint_p010", ss.ks_coint_p010_low, ss.ks_coint_p010_high),
                "ks_hl_optimal_bonus": trial.suggest_float("ks_hl_optimal_bonus", ss.ks_hl_optimal_bonus_low, ss.ks_hl_optimal_bonus_high),
                "ks_hl_long_penalty": trial.suggest_float("ks_hl_long_penalty", ss.ks_hl_long_penalty_low, ss.ks_hl_long_penalty_high),
                "ks_hl_short_penalty": trial.suggest_float("ks_hl_short_penalty", ss.ks_hl_short_penalty_low, ss.ks_hl_short_penalty_high),
                "ks_autocorr_good": trial.suggest_float("ks_autocorr_good", ss.ks_autocorr_good_low, ss.ks_autocorr_good_high),
                "ks_autocorr_bad": trial.suggest_float("ks_autocorr_bad", ss.ks_autocorr_bad_low, ss.ks_autocorr_bad_high),
                "ks_vol_rank_good": trial.suggest_float("ks_vol_rank_good", ss.ks_vol_rank_good_low, ss.ks_vol_rank_good_high),
                "ks_vol_rank_extreme": trial.suggest_float("ks_vol_rank_extreme", ss.ks_vol_rank_extreme_low, ss.ks_vol_rank_extreme_high),
                "ks_win_rate_good": trial.suggest_float("ks_win_rate_good", ss.ks_win_rate_good_low, ss.ks_win_rate_good_high),
                "ks_win_rate_bad": trial.suggest_float("ks_win_rate_bad", ss.ks_win_rate_bad_low, ss.ks_win_rate_bad_high),
            }
            if not (ks_params["ks_coint_p001"] > ks_params["ks_coint_p005"] > 0 > ks_params["ks_coint_p010"]):
                raise optuna.exceptions.TrialPruned()
            params.update(ks_params)

        try:
            metrics = self._run_backtest(params, ohlcv_y, ohlcv_x)
        except Exception as exc:
            logger.debug(f"Trial {trial.number} failed: {exc}")
            raise optuna.exceptions.TrialPruned()

        if metrics.get("n_trades", 0) < self.cfg.min_trades:
            raise optuna.exceptions.TrialPruned()

        score = metrics.get(self.cfg.objective, 0.0)
        if not np.isfinite(score):
            raise optuna.exceptions.TrialPruned()
        return float(score)

    def _run_backtest(self, params: Dict, ohlcv_y: pd.DataFrame, ohlcv_x: pd.DataFrame) -> Dict:
        from backtest.engine import BacktestEngine, BacktestConfig
        from backtest.analytics import PerformanceAnalytics

        kalman_scoring_weights = None
        if self.cfg.optimize_kalman_score and "ks_baseline" in params:
            from strategy.kalman_pairs_trading import KalmanScoringWeights
            kalman_scoring_weights = KalmanScoringWeights(
                baseline=params["ks_baseline"],
                regime_ranging_bonus=params["ks_regime_ranging"],
                regime_trending_penalty=params["ks_regime_trending"],
                regime_breakout_penalty=params["ks_regime_breakout"],
                coint_p001_bonus=params["ks_coint_p001"],
                coint_p005_bonus=params["ks_coint_p005"],
                coint_p010_penalty=params["ks_coint_p010"],
                hl_optimal_bonus=params["ks_hl_optimal_bonus"],
                hl_long_penalty=params["ks_hl_long_penalty"],
                hl_short_penalty=params["ks_hl_short_penalty"],
                autocorr_good_bonus=params["ks_autocorr_good"],
                autocorr_bad_penalty=params["ks_autocorr_bad"],
                vol_rank_good_bonus=params["ks_vol_rank_good"],
                vol_rank_extreme_penalty=params["ks_vol_rank_extreme"],
                win_rate_good_bonus=params["ks_win_rate_good"],
                win_rate_bad_penalty=params["ks_win_rate_bad"],
            )

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
            kalman_scoring_weights=kalman_scoring_weights,
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
    mapping = {
        "1m": 1 / 60,
        "5m": 5 / 60,
        "15m": 0.25,
        "30m": 0.5,
        "1h": 1.0,
        "2h": 2.0,
        "4h": 4.0,
        "6h": 6.0,
        "8h": 8.0,
        "12h": 12.0,
        "1d": 24.0,
    }
    return mapping.get(bar_freq, 1.0)

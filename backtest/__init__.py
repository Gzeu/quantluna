"""
backtest — QuantLuna backtest package

Public API (Sprint 15):
  BacktestEngine      — StrategyConfig → walk-forward backtest (engine_adapter.py)
  WalkForwardRunner   — StrategyConfig → WalkForwardValidator wrapper
  WalkForwardEngine   — core engine (engine.py)
  BacktestConfig      — low-level config (engine.py)
  BacktestResults     — results container (engine.py)
  WalkForwardValidator— full validator with Monte Carlo (walk_forward.py)

Quick start:
    from backtest import BacktestEngine
    from config.strategy_config import StrategyConfig

    cfg = StrategyConfig()
    result = BacktestEngine(cfg).run(y=prices_y, x=prices_x)
    print(result["sharpe"])
"""
from __future__ import annotations

try:
    from backtest.engine_adapter import BacktestEngine, WalkForwardRunner
except ImportError:
    BacktestEngine = None  # type: ignore
    WalkForwardRunner = None  # type: ignore

try:
    from backtest.engine import (
        WalkForwardEngine,
        BacktestConfig,
        BacktestResults,
        TradeRecord,
        PerformanceMetrics,
    )
except ImportError:
    WalkForwardEngine = None  # type: ignore
    BacktestConfig = None  # type: ignore
    BacktestResults = None  # type: ignore

try:
    from backtest.walk_forward import WalkForwardValidator
except ImportError:
    WalkForwardValidator = None  # type: ignore

__all__ = [
    "BacktestEngine",
    "WalkForwardRunner",
    "WalkForwardEngine",
    "BacktestConfig",
    "BacktestResults",
    "TradeRecord",
    "PerformanceMetrics",
    "WalkForwardValidator",
]

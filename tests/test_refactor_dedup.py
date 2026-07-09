"""
tests/test_refactor_dedup.py

Regression tests for PR: "refactor: deduplicate modules, unify base classes,
replace subprocess dispatch".

Purpose
-------
Each test targets a specific structural guarantee introduced by the PR.
Tests are intentionally import-only or thin-unit-level — they do not spin
up exchanges, live feeds, or Optuna studies.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import warnings
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# risk/kelly.py
# ===========================================================================

class TestKellyImports:
    """KellySizer must be importable and non-None from risk.kelly and risk."""

    def test_kelly_sizer_importable_from_kelly(self):
        from risk.kelly import KellySizer
        assert KellySizer is not None
        assert inspect.isclass(KellySizer)

    def test_kelly_sizer_importable_from_risk_package(self):
        import risk
        assert risk.KellySizer is not None, (
            "risk.KellySizer is None — __init__.py import is broken"
        )

    def test_risk_init_exports_all_kelly_symbols(self):
        import risk
        for sym in ("KellySizer", "KellySimpleResult", "KellyCrossPair", "KellyConfig"):
            obj = getattr(risk, sym, None)
            assert obj is not None, f"risk.{sym} is None or missing"
            assert inspect.isclass(obj), f"risk.{sym} is not a class"

    def test_kelly_cross_pair_importable(self):
        from risk.kelly import KellyCrossPair, KellyConfig
        assert inspect.isclass(KellyCrossPair)
        assert inspect.isclass(KellyConfig)

    def test_kelly_sizer_shim_warns_and_same_class(self):
        """risk.kelly_sizer is a shim — must warn AND re-export the same class."""
        from risk.kelly import KellySizer as canonical
        # Force a fresh import to trigger the warning
        import sys
        sys.modules.pop("risk.kelly_sizer", None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from risk.kelly_sizer import KellySizer as shim_cls
        assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
            "risk.kelly_sizer did not raise DeprecationWarning"
        )
        assert shim_cls is canonical, (
            "KellySizer from shim is not the same class as risk.kelly.KellySizer"
        )


class TestKellySizerBehavior:
    """KellySizer.size() returns correct KellySimpleResult."""

    @pytest.fixture()
    def sizer(self):
        from risk.kelly import KellySizer
        return KellySizer(kelly_fraction=0.25, max_pct=0.10, min_pct=0.005)

    def test_returns_kelly_simple_result(self, sizer):
        from risk.kelly import KellySimpleResult
        result = sizer.size(
            capital_usdt=10_000.0,
            win_rate=0.60,
            avg_win=150.0,
            avg_loss=100.0,
        )
        assert isinstance(result, KellySimpleResult)

    def test_position_usdt_positive(self, sizer):
        result = sizer.size(
            capital_usdt=10_000.0, win_rate=0.60, avg_win=150.0, avg_loss=100.0
        )
        assert result.position_usdt > 0
        assert result.recommended_pct > 0

    def test_fractional_kelly_le_full_kelly(self, sizer):
        result = sizer.size(
            capital_usdt=10_000.0, win_rate=0.60, avg_win=150.0, avg_loss=100.0
        )
        assert result.fractional_kelly <= result.kelly_fraction + 1e-9

    def test_position_capped_at_max_pct(self, sizer):
        # Very high edge — should hit the 10% cap
        result = sizer.size(
            capital_usdt=10_000.0, win_rate=0.95, avg_win=500.0, avg_loss=10.0
        )
        assert result.capped is True
        assert abs(result.fractional_kelly - 0.10) < 1e-9

    def test_zero_win_rate_returns_fallback(self, sizer):
        result = sizer.size(
            capital_usdt=10_000.0, win_rate=0.0, avg_win=150.0, avg_loss=100.0
        )
        assert result.kelly_fraction == 0.0
        assert result.fractional_kelly == sizer._min_pct

    def test_negative_edge_returns_fallback(self, sizer):
        # win_rate 20%, b=1 → kelly = (1*0.2 - 0.8)/1 = -0.6 < 0
        result = sizer.size(
            capital_usdt=10_000.0, win_rate=0.20, avg_win=100.0, avg_loss=100.0
        )
        assert result.kelly_fraction == 0.0

    def test_capital_reflected_in_position_usdt(self, sizer):
        r1 = sizer.size(capital_usdt=10_000.0, win_rate=0.60, avg_win=150.0, avg_loss=100.0)
        r2 = sizer.size(capital_usdt=20_000.0, win_rate=0.60, avg_win=150.0, avg_loss=100.0)
        assert abs(r2.position_usdt / r1.position_usdt - 2.0) < 0.01


class TestKellyCrossPair:
    """KellyCrossPair.size() returns KellyResult with expected shape."""

    def test_returns_kelly_result(self):
        import numpy as np
        from risk.kelly import KellyCrossPair, KellyConfig, KellyResult
        rng = np.random.default_rng(0)
        pnl = list(rng.normal(50, 200, 100))
        sizer = KellyCrossPair(KellyConfig(kelly_fraction=0.25))
        result = sizer.size(
            pnl_history=pnl, capital_usdt=10_000.0, pair_vol=0.02, pair="BTCUSDT/ETHUSDT"
        )
        assert isinstance(result, KellyResult)
        assert result.final_usdt > 0
        assert 0 < result.final_pct <= 0.10

    def test_insufficient_history_returns_min(self):
        from risk.kelly import KellyCrossPair, KellyConfig
        sizer = KellyCrossPair(KellyConfig(min_position_pct=0.005))
        result = sizer.size(pnl_history=[10.0, 20.0], capital_usdt=10_000.0, pair_vol=0.02)
        assert result.final_pct == 0.005


# ===========================================================================
# strategy/base_strategy.py  — unified base classes
# ===========================================================================

class TestBaseStrategyUnification:
    """Both ABCs must coexist in strategy.base_strategy without name collision."""

    def test_pairs_base_strategy_importable(self):
        from strategy.base_strategy import PairsBaseStrategy
        assert inspect.isclass(PairsBaseStrategy)

    def test_base_strategy_importable(self):
        from strategy.base_strategy import BaseStrategy
        assert inspect.isclass(BaseStrategy)

    def test_pairs_base_and_base_are_distinct(self):
        from strategy.base_strategy import PairsBaseStrategy, BaseStrategy
        assert PairsBaseStrategy is not BaseStrategy, (
            "PairsBaseStrategy and BaseStrategy are the same object — "
            "class rename did not happen"
        )

    def test_signal_types_colocated(self):
        from strategy.base_strategy import Signal, TradeSignal, MarketContext
        assert inspect.isclass(TradeSignal)
        assert inspect.isclass(MarketContext)
        from enum import IntEnum
        assert issubclass(Signal, IntEnum)

    def test_async_engine_signal_types_colocated(self):
        from strategy.base_strategy import SignalDirection, SignalResult, StrategyState, StrategyMetrics
        for cls in (SignalDirection, SignalResult, StrategyState, StrategyMetrics):
            assert inspect.isclass(cls), f"{cls.__name__} not a class"

    def test_base_shim_warns(self):
        import sys
        sys.modules.pop("strategy.base", None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import strategy.base  # noqa: F401
        assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
            "strategy.base did not raise DeprecationWarning"
        )

    def test_base_shim_exports_pairs_base_as_base_strategy(self):
        import sys
        sys.modules.pop("strategy.base", None)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            from strategy.base import BaseStrategy as ShimBase
        from strategy.base_strategy import PairsBaseStrategy
        assert ShimBase is PairsBaseStrategy, (
            "strategy.base.BaseStrategy is not PairsBaseStrategy — backward compat broken"
        )

    def test_concrete_pairs_strategy_satisfies_abc(self):
        import pandas as pd
        from strategy.base_strategy import PairsBaseStrategy, Signal, TradeSignal, MarketContext

        class MinimalPairs(PairsBaseStrategy):
            @property
            def name(self): return "minimal"
            def generate_live(self, y, x, ts=None, funding_annual=0.0,
                              regime_multiplier=1.0, coint_valid=True):
                return self._make_exit("test", ts)
            def generate_batch(self, df, *args, **kwargs):
                return df
            def score(self, context: MarketContext) -> float:
                return 0.5
            def reset(self): pass

        strat = MinimalPairs()
        assert strat.name == "minimal"
        sig = strat.generate_live(y=100.0, x=100.0)
        assert isinstance(sig, TradeSignal)
        assert sig.signal == Signal.EXIT

    def test_concrete_async_strategy_satisfies_abc(self):
        from strategy.base_strategy import BaseStrategy, SignalResult, SignalDirection, StrategyMetrics, StrategyState

        class MinimalAsync(BaseStrategy):
            async def generate_signal(self, data):
                return SignalResult(direction=SignalDirection.FLAT, symbol="TEST")
            async def on_fill(self, fill_event): pass
            async def on_position_update(self, position_event): pass
            def get_metrics(self):
                return StrategyMetrics(
                    strategy_id=self.strategy_id,
                    state=self.state,
                )

        strat = MinimalAsync(strategy_id="test-strat")
        assert strat.is_active()
        result = asyncio.get_event_loop().run_until_complete(strat.generate_signal({}))
        assert isinstance(result, SignalResult)
        assert result.direction == SignalDirection.FLAT


# ===========================================================================
# strategy/regime_detector.py  — deprecation shim
# ===========================================================================

class TestRegimeDetectorShim:

    def test_shim_warns(self):
        import sys
        sys.modules.pop("strategy.regime_detector", None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import strategy.regime_detector  # noqa: F401
        assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
            "strategy.regime_detector shim did not raise DeprecationWarning"
        )

    def test_shim_exports_same_class_as_canonical(self):
        import sys
        sys.modules.pop("strategy.regime_detector", None)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            from strategy.regime_detector import RegimeDetector as ShimDetector
        from core.regime_detector import RegimeDetector as CanonicalDetector
        assert ShimDetector is CanonicalDetector, (
            "Shim re-exports different class than core.regime_detector"
        )


# ===========================================================================
# scripts async main() interface
# ===========================================================================

class TestScriptAsyncMainInterface:
    """Each adapted script must export an awaitable main."""

    @pytest.mark.parametrize("module_path", [
        "scripts.run_paper",
        "scripts.run_live",
        "scripts.run_backtest",
        "scripts.scan_pairs",
    ])
    def test_main_is_coroutine_function(self, module_path):
        mod = importlib.import_module(module_path)
        assert hasattr(mod, "main"), f"{module_path} has no main attribute"
        assert inspect.iscoroutinefunction(mod.main), (
            f"{module_path}.main is not a coroutine function — "
            "main.py dispatch will fail at runtime"
        )

    def test_scan_pairs_main_awaitable_with_mock_loader(self):
        """scan_pairs.main must await DataLoader directly (no re-entrant asyncio.run)."""
        import pandas as pd
        from scripts.scan_pairs import main

        fake_prices = pd.DataFrame(
            {"BTC/USDT:USDT": [100.0] * 10, "ETH/USDT:USDT": [50.0] * 10}
        )

        async def _run():
            with patch("scripts.scan_pairs.DataLoader") as MockLoader:
                instance = MockLoader.return_value
                instance.fetch_multiple = AsyncMock(return_value=fake_prices)
                with patch("scripts.scan_pairs.PairSelector") as MockSelector:
                    mock_sel = MockSelector.return_value
                    mock_sel.scan.return_value = pd.DataFrame()  # empty — returns early
                    await main(exchange="bybit", days=1, top=5)

        asyncio.get_event_loop().run_until_complete(_run())

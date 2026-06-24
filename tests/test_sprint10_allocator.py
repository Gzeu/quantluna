"""
QuantLuna — Sprint 10 Unit Tests

Acoperă:
  - SpreadCorrelationMatrix: update, check_new_pair, diversification_discount,
    get_correlation_matrix, remove
  - KellyCrossPair: vol_target_only (sample mic), Kelly cu E[R]>0, E[R]<=0,
    correlation discount, portfolio cap
  - DrawdownController: normal flow, pair-level DD, soft limit, hard stop,
    manual_resume, HWM tracking
  - PortfolioAllocator: request_entry (toate gatele), update_state,
    record_exit, manual_resume, portfolio_summary

Convenții:
  - Toate testele sunt deterministice (seed np.random).
  - Nu necesită date live sau exchange connections.
  - scikit-learn este marcat opțional (skipat dacă lipsește).
"""
import numpy as np
import pandas as pd
import pytest

from risk.correlation_matrix import SpreadCorrelationMatrix, CorrelationMatrixConfig
from risk.kelly import KellyCrossPair, KellyConfig
from risk.drawdown_controller import DrawdownController, DDConfig, DDLevel
from risk.multi_pair_allocator import PortfolioAllocator, AllocatorConfig
from risk.kelly import KellyConfig
from risk.drawdown_controller import DDConfig
from risk.correlation_matrix import CorrelationMatrixConfig


rng = np.random.default_rng(42)


# ===========================================================================
# Helpers
# ===========================================================================

def make_spread(n: int = 200, corr_with: np.ndarray | None = None, corr: float = 0.9) -> pd.Series:
    """Generează o serie de spread sintetică."""
    base = rng.standard_normal(n)
    if corr_with is not None:
        base = corr * corr_with[:n] + (1 - corr) * base[:n]
    return pd.Series(base)


def make_pnl(n: int = 30, mean: float = 0.005, std: float = 0.01) -> pd.Series:
    """P&L per trade, fracție din capital."""
    return pd.Series(rng.normal(mean, std, n))


# ===========================================================================
# SpreadCorrelationMatrix
# ===========================================================================

class TestSpreadCorrelationMatrix:

    def test_empty_matrix_allows_any_pair(self):
        matrix = SpreadCorrelationMatrix()
        spread = make_spread(50)
        allowed, max_corr, corr_pairs = matrix.check_new_pair("ETH/BTC", spread)
        assert allowed is True
        assert max_corr == 0.0
        assert corr_pairs == []

    def test_insufficient_history_returns_allowed(self):
        matrix = SpreadCorrelationMatrix(
            CorrelationMatrixConfig(min_history=30)
        )
        # Adaugăm un pair activ dar cu date insuficiente
        short_spread = make_spread(10)
        for v in short_spread:
            matrix.update("ETH/BTC", v)

        candidate = make_spread(10)
        allowed, max_corr, _ = matrix.check_new_pair("SOL/BTC", candidate)
        # Candidatul cu < min_history bare → allowed dar notat incert
        assert allowed is True

    def test_uncorrelated_pair_allowed(self):
        matrix = SpreadCorrelationMatrix(
            CorrelationMatrixConfig(max_corr_threshold=0.70, min_history=30)
        )
        base = rng.standard_normal(200)
        for v in base:
            matrix.update("ETH/BTC", v)

        # Spread complet independent
        independent = pd.Series(rng.standard_normal(200))
        allowed, max_corr, _ = matrix.check_new_pair("SOL/BTC", independent)
        assert allowed is True
        assert max_corr < 0.70

    def test_highly_correlated_pair_rejected(self):
        matrix = SpreadCorrelationMatrix(
            CorrelationMatrixConfig(max_corr_threshold=0.70, min_history=30)
        )
        base = rng.standard_normal(200)
        for v in base:
            matrix.update("ETH/BTC", v)

        # Spread aproape identic
        correlated = pd.Series(base + rng.standard_normal(200) * 0.05)
        allowed, max_corr, corr_pairs = matrix.check_new_pair("SOL/BTC", correlated)
        assert allowed is False
        assert max_corr > 0.70
        assert any("ETH/BTC" in cp for cp in corr_pairs)

    def test_diversification_discount_uncorrelated(self):
        matrix = SpreadCorrelationMatrix()
        base = rng.standard_normal(200)
        for v in base:
            matrix.update("ETH/BTC", v)

        independent = pd.Series(rng.standard_normal(200))
        discount = matrix.diversification_discount("SOL/BTC", independent)
        # Corelație aproape zero → discount aproape 1.0
        assert discount > 0.90

    def test_diversification_discount_high_corr(self):
        matrix = SpreadCorrelationMatrix(
            CorrelationMatrixConfig(min_history=30)
        )
        base = rng.standard_normal(200)
        for v in base:
            matrix.update("ETH/BTC", v)

        # corr ~0.95
        highly_corr = pd.Series(base + rng.standard_normal(200) * 0.05)
        discount = matrix.diversification_discount("SOL/BTC", highly_corr)
        # discount = 1 - 0.95 * 0.5 = ~0.525
        assert discount < 0.60

    def test_remove_pair_clears_buffer(self):
        matrix = SpreadCorrelationMatrix()
        base = rng.standard_normal(200)
        for v in base:
            matrix.update("ETH/BTC", v)
        matrix.remove("ETH/BTC")
        assert "ETH/BTC" not in matrix._buffers

    def test_get_correlation_matrix_shape(self):
        matrix = SpreadCorrelationMatrix(
            CorrelationMatrixConfig(min_history=30)
        )
        base1 = rng.standard_normal(100)
        base2 = rng.standard_normal(100)
        for v in base1:
            matrix.update("A", v)
        for v in base2:
            matrix.update("B", v)
        df = matrix.get_correlation_matrix()
        assert df.shape == (2, 2)
        assert abs(df.loc["A", "A"] - 1.0) < 1e-6
        assert abs(df.loc["B", "B"] - 1.0) < 1e-6

    def test_buffer_max_window(self):
        matrix = SpreadCorrelationMatrix(
            CorrelationMatrixConfig(window=50)
        )
        for v in rng.standard_normal(200):
            matrix.update("TEST", v)
        assert len(matrix._buffers["TEST"]) == 50


# ===========================================================================
# KellyCrossPair
# ===========================================================================

class TestKellyCrossPair:

    def test_vol_target_only_on_small_sample(self):
        kelly = KellyCrossPair(KellyConfig(
            kelly_fraction=0.25,
            vol_target=0.01,
            min_trades_for_kelly=20,
        ))
        pnl = make_pnl(10)  # < 20 trades
        spread = make_spread(100)
        result = kelly.compute(
            pair_id="TEST",
            trade_pnl_series=pnl,
            spread_series=spread,
            capital_usd=10_000,
        )
        assert result.method_used == "vol_target_only"
        assert result.final_fraction >= 0.0
        assert result.final_notional_usd >= 0.0

    def test_kelly_positive_edge(self):
        kelly = KellyCrossPair(KellyConfig(
            kelly_fraction=0.25,
            vol_target=0.01,
            min_trades_for_kelly=20,
            max_fraction_per_pair=0.20,
        ))
        # P&L cu edge pozitiv clar
        pnl = make_pnl(50, mean=0.008, std=0.005)
        spread = make_spread(200)
        result = kelly.compute(
            pair_id="TEST",
            trade_pnl_series=pnl,
            spread_series=spread,
            capital_usd=10_000,
        )
        assert result.method_used == "kelly"
        assert result.kelly_full > 0.0
        assert result.kelly_fractional <= result.kelly_full
        assert result.final_fraction <= 0.20  # max_fraction_per_pair
        assert result.final_notional_usd > 0.0

    def test_kelly_negative_edge_returns_zero(self):
        kelly = KellyCrossPair(KellyConfig(min_trades_for_kelly=20))
        pnl = make_pnl(50, mean=-0.005, std=0.003)  # E[R] < 0
        spread = make_spread(200)
        result = kelly.compute(
            pair_id="TEST",
            trade_pnl_series=pnl,
            spread_series=spread,
            capital_usd=10_000,
        )
        assert result.kelly_full == 0.0
        assert result.kelly_fractional == 0.0

    def test_correlation_discount_reduces_sizing(self):
        kelly = KellyCrossPair(KellyConfig(
            kelly_fraction=0.25,
            min_trades_for_kelly=20,
        ))
        pnl = make_pnl(50, mean=0.008)
        spread = make_spread(200)

        result_no_discount = kelly.compute(
            pair_id="A",
            trade_pnl_series=pnl,
            spread_series=spread,
            capital_usd=10_000,
            diversification_discount=1.0,
        )
        result_high_corr = kelly.compute(
            pair_id="A",
            trade_pnl_series=pnl,
            spread_series=spread,
            capital_usd=10_000,
            diversification_discount=0.5,
        )
        assert result_high_corr.kelly_corr_adjusted <= result_no_discount.kelly_corr_adjusted

    def test_portfolio_cap_respected(self):
        kelly = KellyCrossPair(KellyConfig(
            kelly_fraction=0.25,
            max_fraction_portfolio=0.60,
        ))
        pnl = make_pnl(50, mean=0.01)
        spread = make_spread(200)
        # Deja 55% deployed
        result = kelly.compute(
            pair_id="TEST",
            trade_pnl_series=pnl,
            spread_series=spread,
            capital_usd=10_000,
            deployed_fraction=0.55,
        )
        # Spațiu rămas = 5%; sizing nu poate depăși asta
        assert result.final_fraction <= 0.06  # mică toleranță

    def test_kelly_result_summary_string(self):
        kelly = KellyCrossPair()
        pnl = make_pnl(30, mean=0.005)
        spread = make_spread(100)
        result = kelly.compute("ETH/BTC", pnl, spread, 10_000)
        s = result.summary()
        assert "ETH/BTC" in s
        assert "f*=" in s
        assert "final=" in s


# ===========================================================================
# DrawdownController
# ===========================================================================

class TestDrawdownController:

    def test_normal_state_on_init(self):
        ctrl = DrawdownController()
        assert ctrl.level == DDLevel.NORMAL
        assert ctrl.is_trading_allowed is True
        assert ctrl.can_open_new is True

    def test_normal_flow_no_dd(self):
        ctrl = DrawdownController(DDConfig(capital_usd=10_000))
        ctrl.open_pair("ETH/BTC")
        snap = ctrl.update({"ETH/BTC": 50.0})  # profit mic
        assert snap.level == DDLevel.NORMAL
        assert snap.portfolio_dd_pct == 0.0
        assert snap.pairs_force_close == []

    def test_pair_level_force_close(self):
        ctrl = DrawdownController(DDConfig(
            pair_soft_dd=0.05,
            capital_usd=10_000,
        ))
        ctrl.open_pair("ETH/BTC")
        # Pierdere mare pe pair: 600 USD din 10k = 6% > 5%
        snap = ctrl.update({"ETH/BTC": -600.0})
        assert "ETH/BTC" in snap.pairs_force_close

    def test_soft_limit_triggered(self):
        ctrl = DrawdownController(DDConfig(
            portfolio_soft_dd=0.08,
            portfolio_hard_dd=0.15,
            capital_usd=10_000,
        ))
        # DD 9% > soft limit 8%
        snap = ctrl.update({"ETH/BTC": -900.0})
        assert snap.level == DDLevel.SOFT_LIMIT
        assert ctrl.can_open_new is False
        assert ctrl.is_trading_allowed is False

    def test_hard_stop_triggered(self):
        ctrl = DrawdownController(DDConfig(
            portfolio_hard_dd=0.15,
            capital_usd=10_000,
        ))
        # DD 16% > hard stop 15%
        snap = ctrl.update({"ETH/BTC": -1600.0})
        assert snap.level == DDLevel.HARD_STOP
        assert ctrl.is_trading_allowed is False
        assert set(snap.pairs_force_close) == {"ETH/BTC"}

    def test_hard_stop_does_not_auto_reset(self):
        ctrl = DrawdownController(DDConfig(
            portfolio_hard_dd=0.15,
            capital_usd=10_000,
        ))
        ctrl.update({"A": -1600.0})
        # Chiar dacă PnL revine la 0, hard stop rămâne activ
        snap = ctrl.update({"A": 0.0})
        assert snap.level == DDLevel.HARD_STOP

    def test_manual_resume_resets_hard_stop(self):
        ctrl = DrawdownController(DDConfig(
            portfolio_hard_dd=0.15,
            capital_usd=10_000,
            hwm_reset_on_manual_resume=True,
        ))
        ctrl.update({"A": -1600.0})
        assert ctrl.level == DDLevel.HARD_STOP
        success = ctrl.manual_resume()
        assert success is True
        assert ctrl.level == DDLevel.NORMAL
        assert ctrl.can_open_new is True

    def test_manual_resume_fails_if_no_hard_stop(self):
        ctrl = DrawdownController()
        result = ctrl.manual_resume()
        assert result is False

    def test_hwm_tracked_correctly(self):
        ctrl = DrawdownController(DDConfig(capital_usd=10_000))
        # Profil: profit → pierdere
        ctrl.update({"A": 500.0})   # equity 10500, HWM=10500
        ctrl.update({"A": 200.0})   # equity 10200, HWM=10500
        snap = ctrl.update({"A": -100.0})  # equity 9900, DD față de 10500
        expected_dd = (10500 - 9900) / 10500
        assert abs(snap.portfolio_dd_pct - expected_dd) < 0.001

    def test_close_pair_removes_state(self):
        ctrl = DrawdownController()
        ctrl.open_pair("ETH/BTC")
        ctrl.close_pair("ETH/BTC")
        assert "ETH/BTC" not in ctrl._pair_states


# ===========================================================================
# PortfolioAllocator (integration)
# ===========================================================================

class TestPortfolioAllocator:

    def _make_allocator(self, capital: float = 10_000, max_pairs: int = 3) -> PortfolioAllocator:
        cfg = AllocatorConfig(
            capital_usd=capital,
            max_concurrent_pairs=max_pairs,
            kelly=KellyConfig(
                kelly_fraction=0.25,
                vol_target=0.01,
                max_fraction_per_pair=0.20,
                max_fraction_portfolio=0.60,
                min_trades_for_kelly=20,
            ),
            correlation=CorrelationMatrixConfig(
                max_corr_threshold=0.70,
                min_history=30,
            ),
            drawdown=DDConfig(
                pair_soft_dd=0.05,
                portfolio_soft_dd=0.08,
                portfolio_hard_dd=0.15,
                capital_usd=capital,
            ),
        )
        return PortfolioAllocator(cfg)

    def test_first_entry_allowed(self):
        alloc = self._make_allocator()
        spread = make_spread(200)
        pnl = make_pnl(30, mean=0.006)
        decision = alloc.request_entry("ETH/BTC", spread, pnl)
        assert decision.allowed is True
        assert decision.notional_usd > 0.0
        assert decision.kelly_result is not None

    def test_max_pairs_gate(self):
        alloc = self._make_allocator(max_pairs=1)
        spread = make_spread(200)
        pnl = make_pnl(30, mean=0.006)

        d1 = alloc.request_entry("ETH/BTC", spread, pnl)
        assert d1.allowed is True

        d2 = alloc.request_entry("SOL/BTC", make_spread(200), pnl)
        assert d2.allowed is False
        assert "MAX_PAIRS" in d2.reject_reason

    def test_correlation_gate_rejects_redundant_pair(self):
        alloc = self._make_allocator(max_pairs=5)
        base = rng.standard_normal(200)

        # Populam buffer-ul matricei cu date suficiente
        for v in base:
            alloc._corr.update("ETH/BTC", v)

        # Candidat aproape identic
        correlated_spread = pd.Series(base + rng.standard_normal(200) * 0.03)
        decision = alloc.request_entry("SOL/BTC", correlated_spread)
        assert decision.allowed is False
        assert "CORR_HIGH" in decision.reject_reason

    def test_dd_gate_blocks_on_soft_limit(self):
        alloc = self._make_allocator()
        # Declanșăm SOFT_LIMIT manual
        alloc._dd.update({"A": -900.0})  # 9% DD > 8% soft
        assert alloc._dd.level == DDLevel.SOFT_LIMIT

        decision = alloc.request_entry("ETH/BTC", make_spread(200))
        assert decision.allowed is False
        assert "DD_LEVEL" in decision.reject_reason

    def test_update_state_feeds_dd_controller(self):
        alloc = self._make_allocator()
        spread = make_spread(200)
        pnl = make_pnl(30, mean=0.005)
        d = alloc.request_entry("ETH/BTC", spread, pnl)
        assert d.allowed

        snap = alloc.update_state(
            open_pnl_per_pair={"ETH/BTC": 50.0},
            spread_updates={"ETH/BTC": float(spread.iloc[-1])},
        )
        assert snap.level == DDLevel.NORMAL
        assert snap.portfolio_equity > alloc.cfg.capital_usd  # profit

    def test_record_exit_cleans_up(self):
        alloc = self._make_allocator()
        spread = make_spread(200)
        pnl = make_pnl(30, mean=0.005)
        alloc.request_entry("ETH/BTC", spread, pnl)
        assert alloc._n_pairs == 1

        alloc.record_exit("ETH/BTC")
        assert alloc._n_pairs == 0
        assert "ETH/BTC" not in alloc._active_pairs
        assert "ETH/BTC" not in alloc._corr._buffers
        assert "ETH/BTC" not in alloc._dd._pair_states

    def test_manual_resume_after_hard_stop(self):
        alloc = self._make_allocator()
        # Triggerez HARD_STOP direct pe DD controller
        alloc._dd.update({"X": -1600.0})
        assert alloc._dd.level == DDLevel.HARD_STOP

        # Entry blocată
        d = alloc.request_entry("ETH/BTC", make_spread(200))
        assert d.allowed is False

        # Resume
        ok = alloc.manual_resume()
        assert ok is True
        assert alloc.can_open_new

    def test_portfolio_summary_structure(self):
        alloc = self._make_allocator()
        summary = alloc.portfolio_summary()
        assert "dd_level" in summary
        assert "n_active_pairs" in summary
        assert "active_pairs" in summary
        assert "total_exposure_usdt" in summary

    def test_entry_without_pnl_history_uses_vol_target(self):
        """Pair nou fără historical trades → vol_target_only, dar allowed."""
        alloc = self._make_allocator()
        spread = make_spread(200)
        # Nu pasăm pnl_history
        decision = alloc.request_entry("NEW/PAIR", spread)
        assert decision.allowed is True
        assert decision.kelly_result.method_used == "vol_target_only"

    def test_is_trading_allowed_property(self):
        alloc = self._make_allocator()
        assert alloc.is_trading_allowed is True

        alloc._dd.update({"X": -1600.0})
        assert alloc.is_trading_allowed is False

    @pytest.mark.skipif(
        not __import__('importlib').util.find_spec('sklearn'),
        reason="scikit-learn not installed"
    )
    def test_ledoit_wolf_shrinkage(self):
        """Ledoit-Wolf este aplicat dacă sklearn este disponibil."""
        from risk.correlation_matrix import CorrelationMatrixConfig, SpreadCorrelationMatrix
        matrix = SpreadCorrelationMatrix(
            CorrelationMatrixConfig(use_ledoit_wolf=True, min_history=30)
        )
        b1 = rng.standard_normal(100)
        b2 = rng.standard_normal(100)
        for v in b1:
            matrix.update("A", v)
        for v in b2:
            matrix.update("B", v)
        df = matrix.get_correlation_matrix()
        # Matricea cu Ledoit-Wolf trebuie să fie validă (diagonal = 1, simetrică)
        assert df.shape == (2, 2)
        assert abs(df.loc["A", "A"] - 1.0) < 1e-4
        assert abs(df.loc["A", "B"] - df.loc["B", "A"]) < 1e-6

"""
Unit tests for risk/ modules.

Covers PositionSizer:
- Positive sizes for valid inputs
- Kelly + vol-target sizing logic
- Funding drag (>5% annual) triggers size reduction
- Zero capital / zero spread vol edge cases
- Leverage stays below max_leverage

Covers PortfolioRisk:
- add_position allows / blocks based on exposure cap
- Circuit breaker fires at max_drawdown threshold
- Circuit breaker blocks subsequent adds
- summary() returns correct keys
- remove_position clears exposure
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.settings import RiskConfig
from risk.position_sizer import PositionSizer, SizingResult
from risk.portfolio_risk import PortfolioRisk, PairExposure


# ── helpers ────────────────────────────────────────────────────────────────────

def _spread(n: int = 200, std: float = 50.0, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal(n) * std
    return pd.Series(raw, name="spread")


def _make_sizer(cfg: RiskConfig = None) -> PositionSizer:
    return PositionSizer(cfg or RiskConfig())


# ── PositionSizer ──────────────────────────────────────────────────────────────

class TestPositionSizer:
    def test_returns_sizing_result(self, risk_cfg):
        sizer = PositionSizer(risk_cfg)
        result = sizer.size(
            capital_usdt=10_000,
            beta=0.85,
            spread_series=_spread(),
            price_y=3000.0,
            price_x=60_000.0,
        )
        assert isinstance(result, SizingResult)

    def test_positive_quantities(self, risk_cfg):
        sizer = PositionSizer(risk_cfg)
        result = sizer.size(
            capital_usdt=10_000, beta=0.85,
            spread_series=_spread(), price_y=3000.0, price_x=60_000.0,
        )
        assert result.qty_y > 0
        assert result.qty_x > 0
        assert result.final_size > 0

    def test_legs_respect_beta_ratio(self, risk_cfg):
        """qty_x / qty_y should be approximately beta."""
        sizer = PositionSizer(risk_cfg)
        beta = 0.75
        result = sizer.size(
            capital_usdt=10_000, beta=beta,
            spread_series=_spread(), price_y=3000.0, price_x=60_000.0,
        )
        ratio = result.qty_x / result.qty_y
        assert abs(ratio - beta) < 1e-9, f"Expected ratio {beta}, got {ratio:.6f}"

    def test_leverage_below_max(self, risk_cfg):
        sizer = PositionSizer(risk_cfg)
        result = sizer.size(
            capital_usdt=10_000, beta=0.85,
            spread_series=_spread(), price_y=3000.0, price_x=60_000.0,
        )
        assert result.leverage_y <= risk_cfg.max_leverage + 1e-9
        assert result.leverage_x <= risk_cfg.max_leverage + 1e-9

    def test_final_size_le_vol_target_and_kelly(self, risk_cfg):
        sizer = PositionSizer(risk_cfg)
        result = sizer.size(
            capital_usdt=10_000, beta=0.85,
            spread_series=_spread(), price_y=3000.0, price_x=60_000.0,
        )
        assert result.final_size <= result.vol_target_size + 1e-6
        assert result.final_size <= result.kelly_size + 1e-6

    def test_high_funding_reduces_size(self, risk_cfg):
        """funding_rate_8h = 0.001 -> annual = 0.001*3*365 ~109% -> triggers 25% cut."""
        sizer = PositionSizer(risk_cfg)
        normal = sizer.size(
            capital_usdt=10_000, beta=0.85,
            spread_series=_spread(), price_y=3000.0, price_x=60_000.0,
            funding_rate_8h=0.0,
        )
        high_fund = sizer.size(
            capital_usdt=10_000, beta=0.85,
            spread_series=_spread(), price_y=3000.0, price_x=60_000.0,
            funding_rate_8h=0.001,
        )
        assert high_fund.final_size < normal.final_size, \
            "High funding must reduce final_size"
        assert high_fund.warning is not None

    def test_zero_spread_vol_returns_zero_size(self, risk_cfg):
        sizer = PositionSizer(risk_cfg)
        zero_spread = pd.Series([0.0] * 200, name="spread")
        result = sizer.size(
            capital_usdt=10_000, beta=0.85,
            spread_series=zero_spread, price_y=3000.0, price_x=60_000.0,
        )
        assert result.final_size == 0
        assert result.warning == "zero_spread_vol"

    def test_larger_capital_gives_larger_size(self, risk_cfg):
        sizer = PositionSizer(risk_cfg)
        spread = _spread()
        r1 = sizer.size(5_000, 0.85, spread, 3000.0, 60_000.0)
        r2 = sizer.size(20_000, 0.85, spread, 3000.0, 60_000.0)
        assert r2.final_size > r1.final_size

    def test_size_not_zero_for_reasonable_inputs(self, risk_cfg):
        sizer = PositionSizer(risk_cfg)
        result = sizer.size(
            capital_usdt=10_000, beta=0.85,
            spread_series=_spread(std=200.0),
            price_y=3000.0, price_x=60_000.0, sharpe_estimate=0.8,
        )
        assert result.final_size > 0, "Expected non-zero size for reasonable inputs"


# ── PortfolioRisk ──────────────────────────────────────────────────────────────

def _make_portfolio(capital: float = 10_000) -> PortfolioRisk:
    return PortfolioRisk(
        max_total_exposure_pct=0.80,
        max_pair_corr=0.70,
        max_drawdown=0.15,
        capital_usdt=capital,
    )


def _exposure(pair: str, notional: float, pnl: float = 0.0) -> PairExposure:
    return PairExposure(
        pair=pair, notional_usdt=notional, current_pnl=pnl,
        entry_zscore=-2.2, current_zscore=-1.5, beta=0.85,
    )


class TestPortfolioRisk:
    def test_add_position_allowed(self):
        pr = _make_portfolio()
        allowed = pr.add_position(_exposure("ETH-BTC", 1_000))
        assert allowed is True
        assert pr.summary()["n_positions"] == 1

    def test_add_position_blocked_at_exposure_cap(self):
        pr = _make_portfolio(capital=10_000)
        pr.add_position(_exposure("ETH-BTC", 7_500))
        blocked = pr.add_position(_exposure("SOL-BTC", 1_000))
        assert blocked is False, "Should be blocked over 80% cap"

    def test_remove_position(self):
        pr = _make_portfolio()
        pr.add_position(_exposure("ETH-BTC", 2_000))
        removed = pr.remove_position("ETH-BTC")
        assert removed is not None
        assert pr.summary()["n_positions"] == 0

    def test_remove_nonexistent_returns_none(self):
        pr = _make_portfolio()
        result = pr.remove_position("NONEXISTENT")
        assert result is None

    def test_circuit_breaker_fires_at_max_dd(self):
        pr = _make_portfolio(capital=10_000)
        pr.add_position(_exposure("ETH-BTC", 5_000, pnl=-1_600))
        dd = pr.check_drawdown()
        assert dd >= 0.15
        assert pr._circuit_breaker is True
        assert pr.is_active is False

    def test_circuit_breaker_blocks_new_positions(self):
        pr = _make_portfolio(capital=10_000)
        pr.add_position(_exposure("ETH-BTC", 5_000, pnl=-1_600))
        pr.check_drawdown()
        blocked = pr.add_position(_exposure("SOL-BTC", 500))
        assert blocked is False, "Circuit breaker must block new positions"

    def test_no_circuit_breaker_small_loss(self):
        pr = _make_portfolio(capital=10_000)
        pr.add_position(_exposure("ETH-BTC", 5_000, pnl=-100))
        pr.check_drawdown()
        assert pr.is_active is True

    def test_summary_keys(self):
        pr = _make_portfolio()
        pr.add_position(_exposure("ETH-BTC", 2_000, pnl=50))
        s = pr.summary()
        required = {"n_positions", "total_exposure_usdt",
                    "total_pnl_usdt", "capital",
                    "circuit_breaker", "drawdown"}
        assert required.issubset(set(s.keys()))

    def test_summary_exposure_sums_correctly(self):
        pr = _make_portfolio()
        pr.add_position(_exposure("ETH-BTC", 2_000))
        pr.add_position(_exposure("SOL-BTC", 1_500))
        s = pr.summary()
        assert abs(s["total_exposure_usdt"] - 3_500) < 1e-9

    def test_update_pnl_reflected_in_summary(self):
        pr = _make_portfolio()
        pr.add_position(_exposure("ETH-BTC", 2_000, pnl=0))
        pr.update_pnl("ETH-BTC", 300.0)
        s = pr.summary()
        assert abs(s["total_pnl_usdt"] - 300.0) < 1e-9

"""
QuantLuna — Tests: risk/bybit_position_sizer.py
Sprint 28  |  10 tests
"""
from __future__ import annotations

import math
import pytest

from risk.bybit_position_sizer import BybitPositionSizer, SizingParams, SizingResult


@pytest.fixture
def sizer():
    return BybitPositionSizer(
        capital_usdt=10_000.0,
        max_leverage=3.0,
        kelly_fraction="half",
        max_position_pct=0.25,
        min_notional=5.0,
    )


def _params(**kwargs) -> SizingParams:
    base = dict(
        symbol="BTCUSDT", entry_price=65_000.0,
        win_rate=0.55, avg_win_usd=120.0, avg_loss_usd=80.0,
        leverage=2.0, qty_step=0.001, contract_size=1.0,
    )
    base.update(kwargs)
    return SizingParams(**base)


class TestBybitPositionSizer:

    def test_kelly_fraction_raw_positive(self, sizer):
        f = sizer.kelly_fraction_raw(0.55, 120.0, 80.0)
        # b = 120/80 = 1.5, f = (0.55*1.5 - 0.45) / 1.5 = 0.525/1.5 = 0.35
        assert f == pytest.approx(0.35, rel=1e-4)

    def test_kelly_fraction_negative_edge(self, sizer):
        # Win rate 40%, avg_win = avg_loss -> kelly negative
        f = sizer.kelly_fraction_raw(0.40, 100.0, 100.0)
        assert f == 0.0  # clamped la 0

    def test_half_kelly_applied(self, sizer):
        result = sizer.calculate(_params())
        raw_kelly = sizer.kelly_fraction_raw(0.55, 120.0, 80.0)  # 0.35
        expected_eff_f = raw_kelly * 0.5  # half kelly
        assert result.effective_f == pytest.approx(expected_eff_f, rel=1e-3)

    def test_qty_rounded_to_step(self, sizer):
        result = sizer.calculate(_params(qty_step=0.001))
        qty_str = f"{result.qty_contracts:.10f}"
        # Qty trebuie sa fie multiplu de 0.001
        assert abs(result.qty_contracts % 0.001) < 1e-9 or result.qty_contracts == 0

    def test_max_leverage_capped(self, sizer):
        result = sizer.calculate(_params(leverage=10.0))
        assert result.leverage == pytest.approx(3.0)  # capped la max_leverage
        assert "capped" in " ".join(result.warnings).lower() or result.leverage <= 3.0

    def test_max_position_pct_cap(self, sizer):
        # Win rate 90% -> kelly enorm, trebuie capata
        result = sizer.calculate(_params(win_rate=0.90, avg_win_usd=200.0, avg_loss_usd=10.0))
        assert result.pct_of_capital <= 0.25 + 1e-6  # max 25%
        assert result.capped is True

    def test_notional_equals_qty_times_price(self, sizer):
        result = sizer.calculate(_params())
        expected_notional = result.qty_contracts * 65_000.0 * 1.0
        assert result.notional_usdt == pytest.approx(expected_notional, rel=1e-4)

    def test_margin_equals_notional_div_leverage(self, sizer):
        result = sizer.calculate(_params(leverage=2.0))
        assert result.margin_usdt == pytest.approx(result.notional_usdt / 2.0, rel=1e-4)

    def test_fixed_fraction_method(self, sizer):
        result = sizer.calculate(_params(), method="fixed")
        assert result.sizing_method == "fixed"
        assert result.qty_contracts >= 0
        assert result.kelly_f == 0.0

    def test_zero_result_on_min_notional_fail(self):
        sizer_tiny = BybitPositionSizer(
            capital_usdt=10.0,   # capital mic
            max_leverage=1.0,
            kelly_fraction="quarter",
            min_notional=5.0,
        )
        result = sizer_tiny.calculate(
            SizingParams(symbol="BTCUSDT", entry_price=65_000.0,
                         win_rate=0.55, avg_win_usd=10.0, avg_loss_usd=8.0,
                         leverage=1.0, qty_step=0.001)
        )
        assert result.qty_contracts == 0.0

    def test_invalid_kelly_fraction_raises(self):
        with pytest.raises(ValueError, match="kelly_fraction"):
            BybitPositionSizer(kelly_fraction="third")

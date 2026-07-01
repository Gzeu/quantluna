"""
tests/test_adoption_workflow.py  —  Tests pentru modulele S19

Acopera:
  - PositionScanner: clasificare MANAGED / ORPHAN, parse errors, scan errors
  - AdoptionEngine: decizii ADOPT / CLOSE_NOW / MONITOR_ONLY, calcul TP/SL
  - ProfitOptimizer: HOLD, SL/TP hit, break-even, profit ladder, trailing stop
  - ProfitOptimizer.register() integrat cu AdoptionResult
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.position_scanner import ExchangePosition, PositionScanner
from execution.adoption_engine import (
    AdoptionConfig, AdoptionDecision, AdoptionEngine, AdoptionResult,
)
from execution.profit_optimizer import (
    ActionType, ProfitOptimizer, TrackedPosition,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _raw_pos(symbol='BTC/USDT:USDT', side='long', qty=0.1,
             entry=50000.0, mark=51000.0, upnl=100.0,
             lev=5.0, notional=5000.0, liq=45000.0, margin=1000.0):
    return {
        'symbol': symbol, 'side': side, 'contracts': qty,
        'entryPrice': entry, 'markPrice': mark, 'unrealizedPnl': upnl,
        'leverage': lev, 'notional': notional,
        'liquidationPrice': liq, 'initialMargin': margin,
    }


def _ep(symbol='BTC/USDT:USDT', side='long', qty=0.1,
        entry=50000.0, mark=51000.0, upnl=100.0,
        notional=5000.0, liq=45000.0) -> ExchangePosition:
    return ExchangePosition(
        symbol=symbol, side=side, qty=qty, entry_price=entry,
        mark_price=mark, unrealized_pnl=upnl, leverage=5.0,
        notional_usdt=notional, liquidation_price=liq, margin_used=1000.0,
    )


@pytest.fixture
def mock_checkpoint():
    cp = MagicMock()
    cp.load.return_value = None
    cp.save_open = MagicMock()
    cp.update_qty = MagicMock()
    return cp


@pytest.fixture
def mock_exchange():
    ex = AsyncMock()
    ex.fetch_positions.return_value = []
    ex.create_order.return_value = {'id': 'test-order-123'}
    ex.fetch_ticker.return_value = {'last': 50000.0}
    return ex


# ---------------------------------------------------------------------------
# PositionScanner
# ---------------------------------------------------------------------------

class TestPositionScanner:

    @pytest.mark.asyncio
    async def test_no_positions_returns_empty_report(self, mock_exchange, mock_checkpoint):
        mock_exchange.fetch_positions.return_value = []
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        report = await scanner.scan()
        assert len(report.managed) == 0
        assert len(report.orphans) == 0
        assert not report.has_orphans

    @pytest.mark.asyncio
    async def test_orphan_detected_when_no_checkpoint(self, mock_exchange, mock_checkpoint):
        mock_exchange.fetch_positions.return_value = [_raw_pos()]
        mock_checkpoint.load.return_value = None
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        report = await scanner.scan()
        assert len(report.orphans) == 1
        assert report.orphans[0].symbol == 'BTC/USDT:USDT'
        assert report.has_orphans is True

    @pytest.mark.asyncio
    async def test_scan_error_returns_report_with_error(self, mock_exchange, mock_checkpoint):
        mock_exchange.fetch_positions.side_effect = Exception("network error")
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        report = await scanner.scan()
        assert report.scan_error is not None
        assert 'network error' in report.scan_error

    def test_parse_position_invalid_raw_returns_none(self, mock_exchange, mock_checkpoint):
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        result = scanner._parse_position({'contracts': 0, 'side': 'long'})
        assert result is None

    def test_exchange_position_pnl_pct(self):
        pos = _ep(upnl=100.0, notional=5000.0)
        assert pos.pnl_pct == pytest.approx(0.02)

    def test_exchange_position_distance_to_liq_long(self):
        pos = _ep(side='long', mark=50000.0, liq=45000.0)
        expected = (50000.0 - 45000.0) / 50000.0
        assert pos.distance_to_liq_pct == pytest.approx(expected)


# ---------------------------------------------------------------------------
# AdoptionEngine
# ---------------------------------------------------------------------------

class TestAdoptionEngine:

    def _engine(self, mock_exchange, mock_checkpoint, **cfg_kw) -> AdoptionEngine:
        cfg = AdoptionConfig(**cfg_kw) if cfg_kw else AdoptionConfig()
        return AdoptionEngine(mock_exchange, mock_checkpoint, config=cfg)

    @pytest.mark.asyncio
    async def test_adopt_normal_position(self, mock_exchange, mock_checkpoint):
        pos = _ep(upnl=50.0, notional=2000.0)  # PnL=+2.5% > -2%
        engine = self._engine(mock_exchange, mock_checkpoint)
        result = await engine._process_one(pos)
        assert result.decision == AdoptionDecision.ADOPT
        assert result.tp_price is not None
        assert result.sl_price is not None
        mock_checkpoint.save_open.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_when_loss_exceeds_threshold(self, mock_exchange, mock_checkpoint):
        pos = _ep(upnl=-300.0, notional=5000.0)  # PnL = -6% < -5%
        engine = self._engine(mock_exchange, mock_checkpoint, close_loss_pct=-0.05)
        result = await engine._process_one(pos)
        assert result.decision == AdoptionDecision.CLOSE_NOW
        mock_exchange.create_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_when_liq_imminent(self, mock_exchange, mock_checkpoint):
        pos = _ep(side='long', mark=50000.0, liq=49000.0)  # dist=2% < 8%
        engine = self._engine(mock_exchange, mock_checkpoint, min_liq_distance_pct=0.08)
        result = await engine._process_one(pos)
        assert result.decision == AdoptionDecision.CLOSE_NOW

    @pytest.mark.asyncio
    async def test_monitor_only_for_small_notional(self, mock_exchange, mock_checkpoint):
        pos = _ep(notional=3.0)  # < min_notional_adopt=5.0
        engine = self._engine(mock_exchange, mock_checkpoint, min_notional_adopt=5.0)
        result = await engine._process_one(pos)
        assert result.decision == AdoptionDecision.MONITOR_ONLY

    def test_calculate_exits_long(self, mock_exchange, mock_checkpoint):
        pos = _ep(side='long', entry=50000.0)
        engine = self._engine(mock_exchange, mock_checkpoint,
                              tp_target_pct=0.04, sl_max_loss_pct=0.03)
        tp, sl, trail = engine._calculate_exits(pos)
        assert tp == pytest.approx(50000.0 * 1.04)
        assert sl == pytest.approx(50000.0 * 0.97)

    def test_calculate_exits_short(self, mock_exchange, mock_checkpoint):
        pos = _ep(side='short', entry=50000.0)
        engine = self._engine(mock_exchange, mock_checkpoint,
                              tp_target_pct=0.04, sl_max_loss_pct=0.03)
        tp, sl, trail = engine._calculate_exits(pos)
        assert tp == pytest.approx(50000.0 * 0.96)
        assert sl == pytest.approx(50000.0 * 1.03)


# ---------------------------------------------------------------------------
# ProfitOptimizer
# ---------------------------------------------------------------------------

class TestProfitOptimizer:

    def _pos(self, side='long', entry=50000.0, qty=1.0,
              tp=52000.0, sl=48500.0, trail=0.015) -> TrackedPosition:
        return TrackedPosition(
            symbol='BTC/USDT:USDT', side=side, qty=qty,
            entry_price=entry, tp_price=tp, sl_price=sl,
            trailing_pct=trail,
        )

    def test_hold_in_normal_range(self):
        opt = ProfitOptimizer(AsyncMock())
        t = self._pos()
        action = opt._evaluate(t, 50500.0)
        assert action.action_type == ActionType.HOLD

    def test_sl_hit_long(self):
        opt = ProfitOptimizer(AsyncMock())
        t = self._pos(sl=48500.0)
        action = opt._evaluate(t, 48000.0)
        assert action.action_type == ActionType.FULL_CLOSE
        assert 'SL hit' in action.reason

    def test_tp_hit_long(self):
        opt = ProfitOptimizer(AsyncMock())
        t = self._pos(tp=52000.0)
        action = opt._evaluate(t, 52500.0)
        assert action.action_type == ActionType.FULL_CLOSE
        assert 'TP hit' in action.reason

    def test_sl_hit_short(self):
        opt = ProfitOptimizer(AsyncMock())
        t = self._pos(side='short', entry=50000.0, sl=51500.0, tp=48000.0)
        action = opt._evaluate(t, 52000.0)
        assert action.action_type == ActionType.FULL_CLOSE

    def test_break_even_move(self):
        opt = ProfitOptimizer(AsyncMock())
        t = self._pos(entry=50000.0, sl=48500.0)
        t.break_even_trigger_pct = 0.015
        action = opt._evaluate(t, 51000.0)  # +2% > 1.5% trigger
        assert action.action_type == ActionType.MOVE_SL
        assert t.sl_moved_to_be is True
        assert t.sl_price > 50000.0

    def test_profit_ladder_l1(self):
        opt = ProfitOptimizer(AsyncMock())
        t = self._pos(entry=50000.0, qty=1.0)
        t.ladder = [(0.02, 0.25)]
        t.ladder_executed = 0
        action = opt._evaluate(t, 51500.0)  # +3% > ladder 2%
        assert action.action_type == ActionType.PARTIAL_CLOSE
        assert action.close_qty == pytest.approx(1.0 * 0.25)
        assert t.ladder_executed == 1

    def test_trailing_stop_hit(self):
        opt = ProfitOptimizer(AsyncMock())
        t = self._pos(entry=50000.0, trail=0.015)
        t.trailing_activation_pct = 0.02
        t.peak_price = 52000.0  # peak deja setat
        action = opt._evaluate(t, 51200.0)  # < 52000 * (1-0.015) = 51220
        assert action.action_type == ActionType.FULL_CLOSE
        assert 'Trailing' in action.reason

    def test_register_tracked_position(self):
        opt = ProfitOptimizer(AsyncMock())
        pos = _ep(symbol='BTC/USDT:USDT', side='long', entry=50000.0, qty=0.1)
        result = AdoptionResult(
            position=pos, decision=AdoptionDecision.ADOPT,
            reason='test', tp_price=52000.0, sl_price=48500.0,
            trailing_pct=0.015,
        )
        opt.register(result, current_price=50000.0)
        assert opt.active_count == 1
        assert 'BTC/USDT:USDT' in opt._positions

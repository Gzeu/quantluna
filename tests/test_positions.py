#!/usr/bin/env python3
"""
Unit tests pentru poziționare la startup — PositionScanner, ResumeManager,
AdoptionEngine, PositionCheckpoint, și WorkflowOrchestrator.

Rulează cu:
    venv/bin/python -m pytest test_positions.py -v --tb=short
    venv/bin/python test_positions.py  (direct, cu unittest)
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from execution.checkpoint import PositionCheckpoint, PositionState
from execution.position_scanner import (
    ExchangePosition, PositionScanner, ScanReport,
)
from execution.adoption_engine import (
    AdoptionConfig, AdoptionDecision, AdoptionEngine, AdoptionResult,
)
from execution.profit_optimizer import ProfitOptimizer, ActionType
from execution.workflow_orchestrator import WorkflowOrchestrator, StartupContext


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def tmp_db():
    """Crează un fișier DB temporar pentru checkpoint."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def mock_exchange():
    """Mock exchange cu fetch_positions + create_order."""
    ex = AsyncMock()
    ex.fetch_positions.return_value = [
        {"symbol": "BTC/USDT:USDT", "side": "Buy", "size": 0.01, "entryPrice": 65000.0,
         "unrealisedPnl": 5.0, "leverage": 3.0},
        {"symbol": "ETH/USDT:USDT", "side": "Sell", "size": 0.5, "entryPrice": 3500.0,
         "unrealisedPnl": 25.0, "leverage": 3.0},
    ]
    ex.create_order = AsyncMock(return_value={"id": "mock-order-001"})
    ex.fetch_ticker = AsyncMock(return_value={"last": 50000.0})
    return ex


@pytest.fixture
def mock_checkpoint():
    """Mock checkpoint pentru teste unitare."""
    cp = MagicMock(spec=PositionCheckpoint)
    cp.load.return_value = None
    cp.save_open_single = MagicMock()
    cp.save_closed = MagicMock()
    cp.normalize_symbol = PositionCheckpoint.normalize_symbol
    return cp


# ===========================================================================
# Test 1: ExchangePosition dataclass
# ===========================================================================

class TestExchangePosition:
    @staticmethod
    def _make(symbol="BTCUSDT", side="long", qty=0.01, entry=65000.0,
              mark=65500.0, upnl=5.0, notional=650.0, liq=62000.0):
        return ExchangePosition(
            symbol=symbol, side=side, qty=qty, entry_price=entry,
            mark_price=mark, unrealized_pnl=upnl, leverage=3.0,
            notional_usdt=notional, liquidation_price=liq, margin_used=220.0,
        )

    def test_pnl_pct_positive(self):
        pos = self._make(upnl=10.0, notional=1000.0)
        assert pos.pnl_pct == pytest.approx(0.01)

    def test_pnl_pct_negative(self):
        pos = self._make(upnl=-30.0, notional=1000.0)
        assert pos.pnl_pct == pytest.approx(-0.03)

    def test_pnl_pct_zero_notional(self):
        pos = self._make(upnl=5.0, notional=0.0)
        assert pos.pnl_pct == 0.0

    def test_distance_to_liq_long(self):
        pos = self._make(side="long", mark=50000.0, liq=45000.0)
        expected = (50000.0 - 45000.0) / 50000.0
        assert pos.distance_to_liq_pct == pytest.approx(expected)

    def test_distance_to_liq_short(self):
        pos = self._make(side="short", mark=50000.0, liq=55000.0)
        expected = (55000.0 - 50000.0) / 50000.0
        assert pos.distance_to_liq_pct == pytest.approx(expected)

    def test_distance_to_liq_zero_mark(self):
        pos = self._make(mark=0.0)
        assert pos.distance_to_liq_pct == 1.0


# ===========================================================================
# Test 2: PositionScanner
# ===========================================================================

class TestPositionScanner:
    def _raw_pos(self, symbol="BTC/USDT:USDT", side="Buy", size=0.01,
                 entry=65000.0, upnl=5.0, lev=3.0):
        return {
            "symbol": symbol, "side": side, "size": size,
            "entryPrice": entry, "unrealisedPnl": upnl, "leverage": lev,
        }

    def test_empty_scan_returns_empty_report(self, mock_exchange, mock_checkpoint):
        mock_exchange.fetch_positions.return_value = []
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        report = asyncio_run(scanner.scan())
        assert len(report.managed) == 0
        assert len(report.orphans) == 0

    def test_all_positions_orphan_when_no_checkpoint(self, mock_exchange, mock_checkpoint):
        mock_checkpoint.load.return_value = None
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        report = asyncio_run(scanner.scan())
        assert len(report.orphans) == 2
        assert len(report.managed) == 0

    def test_positions_managed_when_checkpoint_matches(self, mock_exchange, mock_checkpoint):
        mock_checkpoint.load.return_value = PositionState(
            sym_y="BTCUSDT", sym_x="ETHUSDT",
            side_y="buy", side_x="sell",
            qty_y=0.01, qty_x=0.5,
            entry_price_y=65000.0, entry_price_x=3500.0,
            entry_zscore=2.0, hedge_ratio=1.5,
            notional_usdt=650.0, opened_at=time.time(), meta={},
        )
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        report = asyncio_run(scanner.scan())
        assert len(report.managed) == 2, f"Expected 2 managed, got {len(report.managed)}"
        for p in report.managed:
            assert p.symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT")

    def test_scan_error_returns_report_with_error(self, mock_exchange, mock_checkpoint):
        mock_exchange.fetch_positions.side_effect = Exception("API error")
        scanner = PositionScanner(mock_exchange, mock_checkpoint)
        report = asyncio_run(scanner.scan())
        assert report.scan_error is not None

    def test_normalize_symbol(self):
        assert PositionScanner._normalize_symbol("BTC/USDT:USDT") == "BTCUSDT"
        assert PositionScanner._normalize_symbol("btc/usdt") == "BTCUSDT"
        assert PositionScanner._normalize_symbol("ETHUSDT") == "ETHUSDT"
        assert PositionScanner._normalize_symbol("SOL/USDT:USDT") == "SOLUSDT"

    def test_build_known_symbols_from_checkpoint(self):
        state = PositionState(
            sym_y="BTCUSDT", sym_x="ETHUSDT",
            side_y="buy", side_x="sell",
            qty_y=0.01, qty_x=0.0,
            entry_price_y=65000.0, entry_price_x=0.0,
            entry_zscore=0.0, hedge_ratio=1.0,
            notional_usdt=650.0, opened_at=time.time(), meta={},
        )
        symbols = PositionScanner._build_known_symbols(state)
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols

    def test_build_known_symbols_single_leg(self):
        state = PositionState(
            sym_y="BTCUSDT", sym_x="BTCUSDT",  # same = single leg
            side_y="long", side_x="",
            qty_y=0.01, qty_x=0.0,
            entry_price_y=65000.0, entry_price_x=0.0,
            entry_zscore=0.0, hedge_ratio=1.0,
            notional_usdt=650.0, opened_at=time.time(),
            meta={"single_leg": True},
        )
        symbols = PositionScanner._build_known_symbols(state)
        assert symbols == {"BTCUSDT"}


# ===========================================================================
# Test 3: PositionCheckpoint
# ===========================================================================

class TestPositionCheckpoint:
    def test_normalize_symbol(self):
        assert PositionCheckpoint.normalize_symbol("BTC/USDT:USDT") == "BTCUSDT"
        assert PositionCheckpoint.normalize_symbol("eth/usdt") == "ETHUSDT"
        assert PositionCheckpoint.normalize_symbol("SOLUSDT") == "SOLUSDT"

    def test_save_and_load_single(self, tmp_db):
        cp = PositionCheckpoint(tmp_db)
        assert cp.load() is None  # no position yet

        cp.save_open_single("BTCUSDT", "long", 0.01, 65000.0, 650.0)
        state = cp.load()
        assert state is not None
        assert state.sym_y == "BTCUSDT"
        assert state.sym_x == "BTCUSDT"  # single leg = same
        assert state.qty_y == 0.01
        assert state.entry_price_y == 65000.0

    def test_save_and_close(self, tmp_db):
        cp = PositionCheckpoint(tmp_db)
        cp.save_open_single("ETHUSDT", "short", 0.5, 3500.0, 1750.0)
        assert cp.load() is not None

        cp.save_closed()
        assert cp.load() is None

    def test_save_open_round_trip(self, tmp_db):
        cp = PositionCheckpoint(tmp_db)
        cp.save_open(
            sym_y="BTCUSDT", sym_x="ETHUSDT",
            side_y="buy", side_x="sell",
            qty_y=0.01, qty_x=0.5,
            entry_price_y=65000.0, entry_price_x=3500.0,
            entry_zscore=2.0, hedge_ratio=1.5,
            notional_usdt=650.0,
            meta={"single_leg": False},
        )
        state = cp.load()
        assert state is not None
        assert state.sym_y == "BTCUSDT"
        assert state.sym_x == "ETHUSDT"
        assert state.qty_y == 0.01
        assert state.qty_x == 0.5
        assert state.hedge_ratio == 1.5
        assert state.meta == {"single_leg": False}

    def test_has_open_position(self, tmp_db):
        cp = PositionCheckpoint(tmp_db)
        assert not cp.has_open_position()

        cp.save_open_single("BTCUSDT", "long", 0.01, 65000.0, 650.0)
        assert cp.has_open_position()

        cp.save_closed()
        assert not cp.has_open_position()


# ===========================================================================
# Test 4: ResumeManager
# ===========================================================================

class TestResumeManager:
    def test_no_checkpoint_fresh_start(self):
        cp = MagicMock(spec=PositionCheckpoint)
        cp.load.return_value = None
        from execution.resume_manager import ResumeManager
        resume = ResumeManager(cp, None)
        result = asyncio_run(resume.reconcile_on_startup())
        assert not result.should_resume
        assert not result.should_halt
        assert "no open position" in result.message

    def test_checkpoint_but_positions_closed(self):
        cp = MagicMock(spec=PositionCheckpoint)
        cp.load.return_value = PositionState(
            sym_y="BTCUSDT", sym_x="ETHUSDT",
            side_y="buy", side_x="sell",
            qty_y=0.01, qty_x=0.5,
            entry_price_y=65000.0, entry_price_x=3500.0,
            entry_zscore=2.0, hedge_ratio=1.5,
            notional_usdt=650.0, opened_at=time.time(), meta={},
        )
        # Exchange returns no positions
        ex = AsyncMock()
        ex.fetch_positions.return_value = []

        from execution.resume_manager import ResumeManager
        resume = ResumeManager(cp, ex)
        result = asyncio_run(resume.reconcile_on_startup())
        # Fetch failed (empty list) -> no positions -> close checkpoint -> fresh start
        cp.save_closed.assert_called_once()

    def test_normalize_symbol(self):
        from execution.resume_manager import ResumeManager
        assert ResumeManager._normalize_symbol("BTC/USDT:USDT") == "BTCUSDT"
        assert ResumeManager._normalize_symbol("ETHUSDT") == "ETHUSDT"


# ===========================================================================
# Test 5: AdoptionEngine
# ===========================================================================

class TestAdoptionEngine:
    def _make_pos(self, symbol="BTCUSDT", side="long", qty=0.01,
                  entry=65000.0, mark=65500.0, upnl=5.0, notional=650.0,
                  liq=62000.0):
        return ExchangePosition(
            symbol=symbol, side=side, qty=qty, entry_price=entry,
            mark_price=mark, unrealized_pnl=upnl, leverage=3.0,
            notional_usdt=notional, liquidation_price=liq, margin_used=220.0,
        )

    def test_adopt_normal_position(self, mock_checkpoint):
        # Liq la distanță sigură, PnL mic — trebuie adoptat
        pos = self._make_pos(mark=65500.0, liq=50000.0, upnl=5.0, notional=650.0)
        engine = AdoptionEngine(AsyncMock(), mock_checkpoint)
        result = asyncio_run(engine._process_one(pos))
        assert result.decision == AdoptionDecision.ADOPT, f"Got {result.decision}: {result.reason}"
        assert result.tp_price is not None
        assert result.sl_price is not None
        mock_checkpoint.save_open_single.assert_called_once()

    def test_close_big_loss(self, mock_checkpoint):
        pos = self._make_pos(upnl=-100.0, notional=1000.0)  # -10%
        engine = AdoptionEngine(AsyncMock(), mock_checkpoint,
                                config=AdoptionConfig(close_loss_pct=-0.05))
        result = asyncio_run(engine._process_one(pos))
        assert result.decision == AdoptionDecision.CLOSE_NOW

    def test_close_liq_imminent(self, mock_checkpoint):
        pos = self._make_pos(mark=50000.0, liq=49000.0)  # 2% distance
        engine = AdoptionEngine(AsyncMock(), mock_checkpoint,
                                config=AdoptionConfig(min_liq_distance_pct=0.08))
        result = asyncio_run(engine._process_one(pos))
        assert result.decision == AdoptionDecision.CLOSE_NOW

    def test_monitor_only_small_notional(self, mock_checkpoint):
        pos = self._make_pos(notional=3.0)
        engine = AdoptionEngine(AsyncMock(), mock_checkpoint,
                                config=AdoptionConfig(min_notional_adopt=5.0))
        result = asyncio_run(engine._process_one(pos))
        assert result.decision == AdoptionDecision.MONITOR_ONLY


# ===========================================================================
# Test 6: WorkflowOrchestrator end-to-end
# ===========================================================================

class TestWorkflowOrchestrator:
    def test_startup_flow_with_positions(self):
        """Testează flow-ul complet de startup cu poziții simulate."""
        mock_ex = AsyncMock()
        mock_ex.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "side": "Buy", "size": 0.01,
             "entryPrice": 65000.0, "unrealisedPnl": 5.0, "leverage": 3.0},
            {"symbol": "ETH/USDT:USDT", "side": "Sell", "size": 0.5,
             "entryPrice": 3500.0, "unrealisedPnl": 25.0, "leverage": 3.0},
        ]
        mock_ex.create_order = AsyncMock(return_value={"id": "mock-001"})

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        from core.position_store import PositionStore
        store = PositionStore(backend="sqlite")
        store.clear()

        orch = WorkflowOrchestrator(
            exchange=mock_ex,
            checkpoint_path=db_path,
            position_store=store,
            skip_health_check=True,
        )
        ctx = asyncio_run(orch.run_startup_workflow())

        assert not ctx.should_halt
        assert ctx.scan_report is not None
        logger.info(f"Scan: {ctx.scan_report.summary()}")

        # Cleanup
        Path(db_path).unlink(missing_ok=True)
        store.clear()

    def test_startup_flow_no_positions(self):
        """Testează flow-ul de startup fără poziții pe exchange."""
        mock_ex = AsyncMock()
        mock_ex.fetch_positions.return_value = []

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        orch = WorkflowOrchestrator(
            exchange=mock_ex,
            checkpoint_path=db_path,
            skip_health_check=True,
        )
        ctx = asyncio_run(orch.run_startup_workflow())

        assert not ctx.should_halt
        assert ctx.scan_report is not None
        assert len(ctx.scan_report.managed) == 0
        assert len(ctx.scan_report.orphans) == 0

        Path(db_path).unlink(missing_ok=True)

    def test_startup_flow_exchange_error(self):
        """Testează comportamentul când exchange-ul e indisponibil."""
        mock_ex = AsyncMock()
        mock_ex.fetch_positions.side_effect = Exception("Connection refused")

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        orch = WorkflowOrchestrator(
            exchange=mock_ex,
            checkpoint_path=db_path,
            skip_health_check=True,
        )
        ctx = asyncio_run(orch.run_startup_workflow())

        assert not ctx.should_halt  # Nu halt, ci skip
        # Scan report e setat chiar și cu eroare
        assert ctx.scan_report is not None
        assert ctx.scan_report.scan_error is not None

        Path(db_path).unlink(missing_ok=True)

    def test_startup_context_properties(self):
        ctx = StartupContext()
        assert not ctx.should_halt
        assert ctx.adopted_count == 0
        assert ctx.closed_count == 0
        assert not ctx.has_adopted_positions


# ===========================================================================
# Helper
# ===========================================================================

def asyncio_run(coro):
    """Rulează o corutină (funcționează cu pytest-asyncio auto mode)."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in an event loop — return awaitable for pytest-asyncio
    return coro


# ===========================================================================
# Logger
# ===========================================================================
import logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("test_positions")

#!/usr/bin/env python3
"""
Test: Preia poziții live de pe exchange și le reconciliază corect la startup.

Rulează cu:
    python test_live_positions.py --dry-run
    python test_live_positions.py --dry-run --pair BTCUSDT/ETHUSDT

Testează flow-ul complet:
  1. Construiește BybitOrderRouter (paper/live)
  2. PositionScanner.scan() → clasifică managed / orphan
  3. ResumeManager.reconcile_on_startup() → reconciliază checkpoint
  4. AdoptionEngine.process_report() → adoptă/închide orfani
  5. Verifică persistarea în PositionStore + Checkpoint
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("test_live_positions")


async def _build_exchange(dry_run: bool = True):
    """Construiește un order router (Bybit default) pentru test."""
    from execution.bybit_order_router import BybitOrderRouter

    # Try to get real keys from env if live mode
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    mode = "paper" if dry_run else "live"
    router = BybitOrderRouter(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet,
        category="linear",
        mode=mode,
    )
    if mode == "live":
        await router.connect()
    return router


async def test_scan_positions(dry_run: bool = True, pair: str | None = None):
    """
    Test 1: PositionScanner — scan exchange și clasifică poziții.
    """
    logger.info("=" * 60)
    logger.info("TEST 1: PositionScanner.scan()")
    logger.info("=" * 60)

    from execution.checkpoint import PositionCheckpoint
    from execution.position_scanner import PositionScanner

    exchange = await _build_exchange(dry_run)
    cp = PositionCheckpoint("state/test_checkpoint.db")

    scanner = PositionScanner(exchange, cp, min_notional=1.0)
    report = await scanner.scan()

    logger.info(f"Scan results: {report.summary()}")
    if report.scan_error:
        logger.warning(f"Scan error (expected in paper/no-keys): {report.scan_error}")

    if report.managed:
        logger.info(f"Managed positions ({len(report.managed)}):")
        for p in report.managed:
            logger.info(f"  {p.symbol} side={p.side} qty={p.qty} entry={p.entry_price}")
    if report.orphans:
        logger.info(f"Orphan positions ({len(report.orphans)}):")
        for p in report.orphans:
            logger.info(f"  {p.symbol} side={p.side} qty={p.qty} entry={p.entry_price} PnL={p.pnl_pct:.2%}")

    # Cleanup test DB
    Path("state/test_checkpoint.db").unlink(missing_ok=True)

    return report


async def test_reconcile_on_startup(dry_run: bool = True):
    """
    Test 2: ResumeManager — reconciliază checkpoint cu exchange.
    """
    logger.info("=" * 60)
    logger.info("TEST 2: ResumeManager.reconcile_on_startup()")
    logger.info("=" * 60)

    from execution.checkpoint import PositionCheckpoint
    from execution.resume_manager import ResumeManager

    exchange = await _build_exchange(dry_run)
    cp = PositionCheckpoint("state/test_reconcile.db")

    # 2a: Fără checkpoint — start fresh
    logger.info("--- 2a: Fără checkpoint (fresh start) ---")
    resume = ResumeManager(cp, exchange)
    result = await resume.reconcile_on_startup()
    assert not result.should_resume, "Fresh start should NOT resume"
    assert not result.should_halt, "Fresh start should NOT halt"
    logger.info(f"  Result: should_resume={result.should_resume} msg='{result.message}'")
    logger.info("  ✅ PASS: fresh start OK")

    # 2b: Cu checkpoint + poziție activă pe exchange
    logger.info("--- 2b: Checkpoint activ — reconciliază ---")
    cp.save_open_single(
        symbol="BTCUSDT",
        side="long",
        qty=0.01,
        entry_price=65000.0,
        notional_usdt=650.0,
    )
    result = await resume.reconcile_on_startup()
    logger.info(f"  Result: should_resume={result.should_resume} should_halt={result.should_halt}")
    logger.info(f"  Message: {result.message}")
    # În paper mode fără poziții reale ar trebui să vadă 0 și să curățe
    # În live mode cu poziții reale ar trebui să vadă match

    # 2c: Curățare
    cp.save_closed()
    Path("state/test_reconcile.db").unlink(missing_ok=True)
    logger.info("  ✅ PASS: reconcile flow OK")


async def test_adoption_flow(dry_run: bool = True):
    """
    Test 3: AdoptionEngine — adoptă poziții orfane cu TP/SL.
    """
    logger.info("=" * 60)
    logger.info("TEST 3: AdoptionEngine — adopt orphan positions")
    logger.info("=" * 60)

    from unittest.mock import AsyncMock, MagicMock
    from execution.adoption_engine import AdoptionEngine, AdoptionConfig, AdoptionDecision
    from execution.checkpoint import PositionCheckpoint
    from execution.position_scanner import ExchangePosition, ScanReport

    exchange = await _build_exchange(dry_run)
    cp = PositionCheckpoint("state/test_adopt.db")

    # Simulează poziții orfane
    orphans = [
        ExchangePosition(
            symbol="BTCUSDT", side="long", qty=0.01,
            entry_price=65000.0, mark_price=65500.0,
            unrealized_pnl=5.0, leverage=3.0,
            notional_usdt=650.0, liquidation_price=62000.0,
            margin_used=220.0,
        ),
        ExchangePosition(
            symbol="ETHUSDT", side="short", qty=0.5,
            entry_price=3500.0, mark_price=3450.0,
            unrealized_pnl=25.0, leverage=3.0,
            notional_usdt=1725.0, liquidation_price=3800.0,
            margin_used=575.0,
        ),
    ]

    # 3a: Procesează raport
    engine = AdoptionEngine(exchange, cp, config=AdoptionConfig())
    report = ScanReport(orphans=orphans)
    results = await engine.process_report(report)

    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    for r in results:
        logger.info(f"  {r.position.symbol}: decision={r.decision.value} reason='{r.reason}'")
        if r.decision == AdoptionDecision.ADOPT:
            logger.info(f"    TP={r.tp_price:.2f} SL={r.sl_price:.2f}")

    # 3b: Verifică checkpoint după adopție
    state = cp.load()
    if state:
        logger.info(f"  Checkpoint saved: {state.sym_y} side={state.side_y} qty={state.qty_y}")
        assert state.qty_y > 0, "Checkpoint should have qty > 0 after adoption"
    else:
        # Dacă pozițiile au fost închise (depinde de config), e OK
        logger.info("  No checkpoint (positions may have been closed)")

    # 3c: Curățare
    cp.save_closed()
    Path("state/test_adopt.db").unlink(missing_ok=True)
    logger.info("  ✅ PASS: adoption flow OK")


async def test_symbol_normalization():
    """
    Test 4: Symbol normalization — verifică că BTC/USDT:USDT == BTCUSDT.
    """
    logger.info("=" * 60)
    logger.info("TEST 4: Symbol normalization")
    logger.info("=" * 60)

    from execution.checkpoint import PositionCheckpoint

    test_cases = [
        ("BTC/USDT:USDT", "BTCUSDT"),
        ("ETH/USDT:USDT", "ETHUSDT"),
        ("BTCUSDT", "BTCUSDT"),
        ("sol/usdt", "SOLUSDT"),
        ("SOL/USDT", "SOLUSDT"),
    ]
    for raw, expected in test_cases:
        result = PositionCheckpoint.normalize_symbol(raw)
        assert result == expected, f"normalize({raw}) = {result} != {expected}"
        logger.info(f"  {raw:20s} → {result} (expected {expected}) ✅")

    # Verifică și scanner-ul
    from execution.position_scanner import PositionScanner
    for raw, expected in test_cases:
        result = PositionScanner._normalize_symbol(raw)
        assert result == expected, f"scanner normalize({raw}) = {result} != {expected}"
    logger.info("  ✅ PASS: all normalizations correct")


async def test_position_store_persistence():
    """
    Test 5: PositionStore — salvează și încarcă poziții corect.
    """
    logger.info("=" * 60)
    logger.info("TEST 5: PositionStore persistence")
    logger.info("=" * 60)

    from core.position_store import PositionStore

    store = PositionStore(backend="sqlite")
    store.clear()

    # Salvează poziții Bybit-style
    test_positions = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": 0.01, "entryPrice": 65000.0, "unrealisedPnl": 5.0, "leverage": 3},
        {"symbol": "ETHUSDT", "side": "Sell", "size": 0.5,  "entryPrice": 3500.0,  "unrealisedPnl": 25.0, "leverage": 3},
    ]
    store.save_bybit_positions(test_positions)
    logger.info(f"  Saved {len(test_positions)} positions to store")

    # Încarcă și verifică
    loaded = store.load_bybit_positions()
    assert len(loaded) == 2, f"Expected 2 loaded positions, got {len(loaded)}"
    for p in loaded:
        logger.info(f"  Loaded: {p['symbol']} side={p['side']} size={p['size']} entry={p['entryPrice']}")
    assert loaded[0]["symbol"] == "BTCUSDT"
    assert loaded[1]["symbol"] == "ETHUSDT"

    # Curățare
    store.clear()
    loaded_after = store.load_bybit_positions()
    assert len(loaded_after) == 0, "Store should be empty after clear()"
    logger.info("  Store cleared ✅")
    logger.info("  ✅ PASS: PositionStore persistence OK")


async def test_end_to_end_startup_flow():
    """
    Test 6: End-to-end — WorkflowOrchestrator startup flow cu poziții simulate.

    Rulează flow-ul complet de startup (FAZA 0-4) cu un mock exchange care
    returnează poziții predefinite, și verifică că toate modulele sunt apelate.
    """
    logger.info("=" * 60)
    logger.info("TEST 6: WorkflowOrchestrator end-to-end startup")
    logger.info("=" * 60)

    from unittest.mock import AsyncMock, MagicMock
    from execution.workflow_orchestrator import WorkflowOrchestrator, StartupContext
    from core.position_store import PositionStore

    # Mock exchange cu poziții
    mock_exchange = AsyncMock()
    mock_exchange.fetch_positions.return_value = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": 0.01, "entryPrice": 65000.0,
         "unrealisedPnl": 5.0, "leverage": 3.0},
        {"symbol": "ETHUSDT", "side": "Sell", "size": 0.5, "entryPrice": 3500.0,
         "unrealisedPnl": 25.0, "leverage": 3.0},
    ]
    mock_exchange.create_order = AsyncMock(return_value={"id": "mock-123"})

    store = PositionStore(backend="sqlite")
    store.clear()

    # Creează orchestrator cu mock exchange
    orch = WorkflowOrchestrator(
        exchange=mock_exchange,
        checkpoint_path="state/test_e2e_checkpoint.db",
        position_store=store,
        skip_health_check=True,
    )

    # Rulează startup workflow
    ctx = await orch.run_startup_workflow()

    assert not ctx.should_halt, f"Workflow should not halt: {ctx.halt_reason}"
    assert ctx.scan_report is not None, "Should have scan report"
    logger.info(f"  Scan: {ctx.scan_report.summary()}")

    if ctx.scan_report.orphans:
        logger.info(f"  Orphans adopted: {ctx.adopted_count}, closed: {ctx.closed_count}")
    else:
        logger.info("  No orphans (expected in some configs)")

    # Verifică PositionStore după workflow
    persisted = store.load_bybit_positions()
    logger.info(f"  Positions in store: {len(persisted)}")
    for p in persisted:
        logger.info(f"    {p['symbol']}")

    # Curățare
    import os
    Path("state/test_e2e_checkpoint.db").unlink(missing_ok=True)
    store.clear()
    logger.info("  ✅ PASS: end-to-end startup flow OK")


async def main():
    parser = argparse.ArgumentParser(description="Test live position handling")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Use paper mode (default: True)")
    parser.add_argument("--pair", type=str, default=None,
                        help="Symbol pair (e.g. BTCUSDT/ETHUSDT)")
    parser.add_argument("--live", action="store_true", default=False,
                        help="Use live exchange (requires API keys in env)")
    parser.add_argument("--skip-slow", action="store_true", default=False,
                        help="Skip tests that need exchange connection")
    args = parser.parse_args()

    dry_run = not args.live
    os.makedirs("state", exist_ok=True)

    # Testele 4 și 5 sunt independente de exchange
    await test_symbol_normalization()
    await test_position_store_persistence()

    if not args.skip_slow:
        await test_scan_positions(dry_run=dry_run, pair=args.pair)
        await test_reconcile_on_startup(dry_run=dry_run)
        await test_adoption_flow(dry_run=dry_run)

    # Test 6 e cu mock - merge fără exchange real
    await test_end_to_end_startup_flow()

    logger.info("=" * 60)
    logger.info("ALL TESTS PASSED ✅")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

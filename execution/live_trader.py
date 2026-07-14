"""
QuantLuna — LiveTrader (Binance WebSocket)
Sprint 24 + Sprint 25 (BinanceOrderRouter integration)

Production-ready live trading loop:
  1. Connects to Binance WebSocket kline stream for sym_y + sym_x
  2. Aggregates ticks into closed bars
  3. Feeds bars into TrendRegimeDetector → AutoStrategySelector pipeline
  4. Executes orders via BinanceOrderRouter (paper or live)
  5. Publishes state updates to state_bus
  6. Exposes stop/emergency_stop controls

Modes:
  paper  — QUANTLUNA_LIVE_MODE=paper (default) — PaperAccount, no real orders
  live   — QUANTLUNA_LIVE_MODE=live           — BinanceOrderRouter (real orders)
  dry    — QUANTLUNA_LIVE_MODE=dry            — BinanceOrderRouter(dry_run=True)

Env vars:
  QUANTLUNA_LIVE_MODE          paper | live | dry  (default: paper)
  BINANCE_API_KEY              required for live / dry mode
  BINANCE_API_SECRET           required for live / dry mode
  BINANCE_TESTNET              true | false (default: false)
  BINANCE_WS_BASE              wss://stream.binance.com:9443
  QUANTLUNA_LIVE_MAX_POSITION  max USD position size (default: 100.0)
  QUANTLUNA_LIVE_STOP_LOSS_PCT stop-loss % from entry (default: 2.0)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.regime_detector import TrendRegimeDetector
from strategy.auto_selector import AutoStrategySelector
from strategy.base import MarketContext
from strategy.bb_mean_reversion import BollingerBandsMeanReversion
from strategy.funding_arb import FundingRateArbitrage
from strategy.zscore_momentum import ZScoreMomentum

logger = logging.getLogger(__name__)

_LIVE_MODE        = os.getenv("QUANTLUNA_LIVE_MODE", "paper").lower()
_MAX_POSITION_USD = float(os.getenv("QUANTLUNA_LIVE_MAX_POSITION", "100.0"))
_STOP_LOSS_PCT    = float(os.getenv("QUANTLUNA_LIVE_STOP_LOSS_PCT", "2.0")) / 100
_WS_BASE          = os.getenv("BINANCE_WS_BASE", "wss://stream.binance.com:9443")


class TraderState(str, Enum):
    IDLE      = "idle"
    RUNNING   = "running"
    STOPPING  = "stopping"
    STOPPED   = "stopped"
    ERROR     = "error"


@dataclass
class BarData:
    symbol:    str
    timestamp: datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float
    is_closed: bool = True


@dataclass
class LivePosition:
    symbol_y:   str
    symbol_x:   str
    side:       int
    entry_price_y: float = 0.0
    entry_price_x: float = 0.0
    qty_y:      float = 0.0
    qty_x:      float = 0.0
    entry_ts:   Optional[datetime] = None
    unrealised_pnl: float = 0.0


@dataclass
class LiveTraderStatus:
    state:            str
    mode:             str
    sym_y:            str
    sym_x:            str
    bar_freq:         str
    active_strategy:  str
    regime:           str
    position_side:    int
    unrealised_pnl:   float
    realised_pnl:     float
    n_trades:         int
    bars_processed:   int
    last_bar_ts:      Optional[str]
    scores:           Dict[str, float]
    switch_history:   List[Dict]
    error:            Optional[str] = None
    uptime_s:         float = 0.0


class LiveTrader:
    """
    Binance WebSocket live trading loop.

    Parameters
    ----------
    cfg            : StrategyConfig
    selector_id    : key in SelectorStore
    on_trade       : optional callback(trade_dict)
    on_bar         : optional callback(BarData, BarData)
    """

    def __init__(
        self,
        cfg,
        selector_id: str = "live",
        on_trade: Optional[Callable] = None,
        on_bar: Optional[Callable] = None,
    ) -> None:
        self.cfg         = cfg
        self.selector_id = selector_id
        self.on_trade    = on_trade
        self.on_bar      = on_bar
        self.mode        = _LIVE_MODE

        self.regime_detector = TrendRegimeDetector(
            window=getattr(cfg, "regime_window", 24),
            adx_window=getattr(cfg, "adx_window", 14),
            min_persistence=getattr(cfg, "regime_min_persistence", 3),
        )
        self.selector = AutoStrategySelector(
            strategies=[
                BollingerBandsMeanReversion(
                    window=max(getattr(cfg, "zscore_window", 20), 5),
                    n_std_entry=getattr(cfg, "zscore_entry", 2.0),
                ),
                ZScoreMomentum(entry_threshold=getattr(cfg, "zscore_entry", 1.5)),
                FundingRateArbitrage(entry_funding_annual=getattr(cfg, "funding_threshold_annual", 0.20)),
            ],
            hysteresis_bonus=0.10,
            min_score_threshold=0.30,
            switch_cooldown_bars=5,
        )

        # Order router (live + dry modes)
        if self.mode in ("live", "dry"):
            from execution.binance_order_router import BinanceOrderRouter
            self._router = BinanceOrderRouter(
                dry_run=(self.mode == "dry"),
            )
        else:
            self._router = None

        self._state       = TraderState.IDLE
        self._position    = LivePosition(symbol_y=cfg.sym_y, symbol_x=cfg.sym_x, side=0)
        self._bars_y:     List[BarData] = []
        self._bars_x:     List[BarData] = []
        self._spreads:    List[float]   = []
        self._realised_pnl: float = 0.0
        self._n_trades:   int = 0
        self._bars_processed: int = 0
        self._last_bar_ts: Optional[datetime] = None
        self._start_ts:   Optional[float] = None
        self._error:      Optional[str] = None
        self._stop_event  = asyncio.Event()
        self._tasks:      List[asyncio.Task] = []

        if self.mode == "paper":
            from execution.paper_account import PaperAccount
            self._paper = PaperAccount(capital_usdt=_MAX_POSITION_USD)
        else:
            self._paper = None

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._state == TraderState.RUNNING:
            logger.warning("LiveTrader already running")
            return
        self._stop_event.clear()
        self._state    = TraderState.RUNNING
        self._start_ts = time.monotonic()
        logger.info(f"LiveTrader starting | mode={self.mode} pair={self.cfg.sym_y}/{self.cfg.sym_x}")
        sym_y_lower = self.cfg.sym_y.lower()
        sym_x_lower = self.cfg.sym_x.lower()
        freq        = getattr(self.cfg, "bar_freq", "1h")
        self._tasks = [
            asyncio.create_task(self._ws_listener(sym_y_lower, freq, is_y=True),  name="ws_y"),
            asyncio.create_task(self._ws_listener(sym_x_lower, freq, is_y=False), name="ws_x"),
        ]

    async def stop(self) -> None:
        logger.info("LiveTrader stopping...")
        self._state = TraderState.STOPPING
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._state = TraderState.STOPPED

    async def emergency_stop(self) -> None:
        logger.warning("EMERGENCY STOP: flattening all positions")
        if self._position.side != 0:
            await self._flatten_position(reason="emergency_stop")
        await self.stop()

    def status(self) -> LiveTraderStatus:
        s = self.selector.scores_summary()
        uptime = round(time.monotonic() - self._start_ts, 1) if self._start_ts else 0.0
        return LiveTraderStatus(
            state=self._state.value, mode=self.mode,
            sym_y=self.cfg.sym_y, sym_x=self.cfg.sym_x,
            bar_freq=getattr(self.cfg, "bar_freq", "1h"),
            active_strategy=s.get("active_strategy") or "none",
            regime=self.regime_detector.current(),
            position_side=self._position.side,
            unrealised_pnl=round(self._position.unrealised_pnl, 4),
            realised_pnl=round(self._realised_pnl, 4),
            n_trades=self._n_trades,
            bars_processed=self._bars_processed,
            last_bar_ts=self._last_bar_ts.isoformat() if self._last_bar_ts else None,
            scores=s.get("scores", {}),
            switch_history=s.get("switch_history", []),
            error=self._error,
            uptime_s=uptime,
        )

    # ------------------------------------------------------------------
    # WebSocket listener
    # ------------------------------------------------------------------

    async def _ws_listener(self, symbol: str, freq: str, is_y: bool) -> None:
        url = f"{_WS_BASE}/ws/{symbol}@kline_{freq}"
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                import websockets
                async with websockets.connect(url, ping_interval=20) as ws:
                    backoff = 1.0
                    logger.info(f"WS connected: {url}")
                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            await self._handle_kline(json.loads(raw), is_y=is_y)
                        except Exception as e:
                            logger.warning(f"Kline parse error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._stop_event.is_set():
                    break
                logger.warning(f"WS error ({symbol}): {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _handle_kline(self, msg: Dict[str, Any], is_y: bool) -> None:
        k = msg.get("k", {})
        if not k.get("x", False):
            return
        bar = BarData(
            symbol=k["s"],
            timestamp=datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc),
            open=float(k["o"]), high=float(k["h"]),
            low=float(k["l"]),  close=float(k["c"]),
            volume=float(k["v"]),
        )
        if is_y:
            self._bars_y.append(bar)
        else:
            self._bars_x.append(bar)
        if self._bars_y and self._bars_x:
            await self._process_bar_pair(self._bars_y.pop(0), self._bars_x.pop(0))

    # ------------------------------------------------------------------
    # Bar processing pipeline
    # ------------------------------------------------------------------

    async def _process_bar_pair(self, bar_y: BarData, bar_x: BarData) -> None:
        spread = bar_y.close - bar_x.close
        self._spreads.append(spread)
        self._bars_processed += 1
        self._last_bar_ts = bar_y.timestamp

        if self.on_bar:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self.on_bar, bar_y, bar_x)
            except Exception:
                pass

        regime = self.regime_detector.update(
            price=spread,
            high=bar_y.high - bar_x.low,
            low=bar_y.low  - bar_x.high,
        )

        win = max(getattr(self.cfg, "zscore_window", 20), 5)
        if len(self._spreads) < win:
            return
        import numpy as np
        arr = np.asarray(self._spreads[-win:])
        zscore = float((spread - arr.mean()) / (arr.std() + 1e-10))

        vol_rank = 0.5
        if len(self._spreads) >= win * 3:
            full = np.asarray(self._spreads)
            recent_vol = float(np.std(np.diff(full[-win:])))
            all_vols = [float(np.std(np.diff(full[i:i+win]))) for i in range(0, len(full) - win, win // 2)]
            vol_rank = float(np.mean(np.asarray(all_vols) <= recent_vol))

        ctx = MarketContext(
            zscore=zscore, half_life_hours=getattr(self.cfg, "half_life_hours", 24.0),
            vol_rank=vol_rank, regime=regime, funding_annual=0.0,
            coint_pvalue=0.03, spread_autocorr=0.0, recent_win_rate=0.5,
            is_warm=len(self._spreads) >= win,
        )
        signal, active_name = self.selector.generate_one(ctx)

        try:
            from core.state_bus import publish
            publish("live_bar", {
                "ts": bar_y.timestamp.isoformat(), "spread": round(spread, 6),
                "zscore": round(zscore, 4), "regime": regime,
                "signal": signal, "active_strategy": active_name or "none",
                "selector_id": self.selector_id,
            })
        except Exception:
            pass

        await self._execute_signal(signal, bar_y, bar_x, zscore)

        if self._position.side != 0:
            self._position.unrealised_pnl = (
                (bar_y.close - self._position.entry_price_y) * self._position.qty_y * self._position.side
                - (bar_x.close - self._position.entry_price_x) * self._position.qty_x * self._position.side
            )
            entry_price = self._position.entry_price_y
            if entry_price > 0:
                loss_pct = abs(self._position.unrealised_pnl) / (entry_price * self._position.qty_y + 1e-10)
                if loss_pct > _STOP_LOSS_PCT and self._position.unrealised_pnl < 0:
                    logger.warning(f"Stop-loss triggered at {loss_pct:.2%}")
                    await self._flatten_position(reason="stop_loss")

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    async def _execute_signal(
        self, signal: int, bar_y: BarData, bar_x: BarData, zscore: float,
    ) -> None:
        current_side = self._position.side
        if signal == current_side:
            return
        if current_side != 0:
            await self._flatten_position(reason="signal_change")
        if signal == 0:
            return

        qty_y = _MAX_POSITION_USD / max(bar_y.close, 1e-10)
        qty_x = _MAX_POSITION_USD / max(bar_x.close, 1e-10)

        if self.mode == "paper" and self._paper:
            self._paper.open_position(
                side=signal, qty_y=qty_y, qty_x=qty_x,
                price_y=bar_y.close, price_x=bar_x.close,
            )
        else:
            await self._place_binance_orders(signal, qty_y, qty_x, bar_y.symbol, bar_x.symbol)

        self._position.side          = signal
        self._position.entry_price_y = bar_y.close
        self._position.entry_price_x = bar_x.close
        self._position.qty_y         = qty_y
        self._position.qty_x         = qty_x
        self._position.entry_ts      = bar_y.timestamp
        self._n_trades += 1

        trade = {
            "ts": bar_y.timestamp.isoformat(), "side": signal,
            "price_y": bar_y.close, "price_x": bar_x.close,
            "qty_y": round(qty_y, 6), "qty_x": round(qty_x, 6),
            "zscore": round(zscore, 4), "mode": self.mode,
        }
        logger.info(f"TRADE: {trade}")
        if self.on_trade:
            try:
                self.on_trade(trade)
            except Exception:
                pass

    async def _flatten_position(self, reason: str = "signal") -> None:
        if self._position.side == 0:
            return
        pnl = self._position.unrealised_pnl
        if self.mode == "paper" and self._paper:
            last_y = self._bars_y[-1].close if self._bars_y else self._position.entry_price_y
            last_x = self._bars_x[-1].close if self._bars_x else self._position.entry_price_x
            pnl = self._paper.close_position(price_y=last_y, price_x=last_x)
        elif self._router is not None:
            # Flatten live: reverse legs
            from execution.binance_order_router import OrderSide
            side_y = OrderSide.SELL if self._position.side == 1 else OrderSide.BUY
            side_x = OrderSide.BUY  if self._position.side == 1 else OrderSide.SELL
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._router.pair_market_orders(
                    sym_y=self._position.symbol_y, side_y=side_y, qty_y=self._position.qty_y,
                    sym_x=self._position.symbol_x, side_x=side_x, qty_x=self._position.qty_x,
                )
            )
        self._realised_pnl += pnl
        logger.info(f"FLATTEN [{reason}]: pnl={pnl:.4f} total_realised={self._realised_pnl:.4f}")
        self._position.side = 0
        self._position.unrealised_pnl = 0.0

    async def _place_binance_orders(
        self, side: int, qty_y: float, qty_x: float, sym_y: str, sym_x: str,
    ) -> None:
        """
        Sprint 25: delegates to BinanceOrderRouter with full retry + precision.
        """
        if self._router is None:
            logger.error("BinanceOrderRouter not initialised (mode=paper?)")
            return
        from execution.binance_order_router import OrderSide
        side_y = OrderSide.BUY  if side == 1 else OrderSide.SELL
        side_x = OrderSide.SELL if side == 1 else OrderSide.BUY
        loop = asyncio.get_event_loop()
        receipts = await loop.run_in_executor(
            None,
            lambda: self._router.pair_market_orders(
                sym_y=sym_y, side_y=side_y, qty_y=qty_y,
                sym_x=sym_x, side_x=side_x, qty_x=qty_x,
            )
        )
        r_y, r_x = receipts
        if r_y.status not in ("FILLED", "FILLED_DRY") or r_x.status not in ("FILLED", "FILLED_DRY"):
            logger.warning(f"Order fill warning: Y={r_y.status} X={r_x.status}")
        # Update position with actual fill prices
        if r_y.avg_price > 0:
            self._position.entry_price_y = r_y.avg_price
        if r_x.avg_price > 0:
            self._position.entry_price_x = r_x.avg_price
        if r_y.filled_qty > 0:
            self._position.qty_y = r_y.filled_qty
        if r_x.filled_qty > 0:
            self._position.qty_x = r_x.filled_qty

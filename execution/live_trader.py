"""
QuantLuna — Live Trading Loop

Orchestrates:
  1. WebSocket price feed (CCXT + websockets)
  2. Incremental Kalman Filter update
  3. Signal generation
  4. Order execution
  5. Position monitoring + exit logic
"""
import asyncio
import json
from typing import Dict, Optional
from loguru import logger
import pandas as pd

from config.settings import QuantLunaConfig
from core.spread import SpreadEngine
from core.kalman_filter import KalmanHedgeRatio
from strategy.signal import SignalGenerator, Signal
from risk.position_sizer import PositionSizer
from risk.portfolio_risk import PortfolioRisk
from execution.order_manager import OrderManager


class LiveTrader:
    """
    Main live trading orchestrator for a single pair.

    Parameters
    ----------
    sym_y, sym_x : trading symbols (e.g., 'ETH/USDT:USDT')
    cfg          : QuantLunaConfig
    """

    def __init__(
        self,
        sym_y: str,
        sym_x: str,
        cfg: QuantLunaConfig = None,
    ):
        self.sym_y = sym_y
        self.sym_x = sym_x
        self.cfg = cfg or QuantLunaConfig()

        # Core components
        kf = KalmanHedgeRatio(
            delta=self.cfg.kalman.delta,
            observation_noise=self.cfg.kalman.observation_noise,
        )
        spread_engine = SpreadEngine(kalman=kf)
        self.signal_gen = SignalGenerator(
            spread_engine=spread_engine, cfg=self.cfg.signal
        )
        self.sizer = PositionSizer(cfg=self.cfg.risk)
        self.portfolio = PortfolioRisk(
            capital_usdt=self.cfg.risk.max_capital_usdt
        )
        self.orders = OrderManager(
            paper_mode=(self.cfg.trading_mode == "paper"),
            maker_fee=self.cfg.execution.maker_fee,
            taker_fee=self.cfg.execution.taker_fee,
        )

        self._prices: Dict[str, float] = {}
        self._in_position = False
        self._running = False

    async def run(self):
        """Main event loop."""
        logger.info(f"QuantLuna LiveTrader starting: {self.sym_y} / {self.sym_x}")
        logger.info(f"Mode: {self.cfg.trading_mode.upper()}")
        self._running = True

        # In production: connect to WebSocket feed
        # Here we show the skeleton with a simulated tick loop
        try:
            await self._main_loop()
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
        finally:
            await self.orders.close()
            self._running = False

    async def _main_loop(self):
        """Simulated tick loop — replace with real WS feed in production."""
        import random
        base_y, base_x = 3000.0, 60000.0  # ETH/BTC example

        while self._running:
            # Simulate price tick
            base_y *= (1 + random.gauss(0, 0.0005))
            base_x *= (1 + random.gauss(0, 0.0003))
            ts = pd.Timestamp.now()

            await self._on_tick(base_y, base_x, ts)
            await asyncio.sleep(1)  # 1s tick

    async def _on_tick(self, price_y: float, price_x: float, ts: pd.Timestamp):
        """Process one price update."""
        self._prices[self.sym_y] = price_y
        self._prices[self.sym_x] = price_x

        sig = self.signal_gen.generate_live(price_y, price_x, ts=ts)

        if not self.portfolio.is_active:
            return

        if not self._in_position and sig.signal != Signal.EXIT and sig.confidence > 0.6:
            await self._open_position(sig, price_y, price_x, ts)
        elif self._in_position and sig.signal == Signal.EXIT:
            await self._close_position(sig, price_y, price_x, ts)

        # Log state every 60 ticks
        if ts.second == 0:
            logger.info(
                f"[{ts}] z={sig.zscore:.3f} | beta={sig.beta:.4f} | "
                f"signal={sig.signal.name} | conf={sig.confidence:.2f}"
            )

    async def _open_position(self, sig, price_y, price_x, ts):
        sizing = self.sizer.size(
            capital_usdt=self.cfg.risk.max_capital_usdt,
            beta=sig.beta,
            spread_series=pd.Series(self.signal_gen.engine._spreads[-100:]),
            price_y=price_y,
            price_x=price_x,
        )
        qty_y = sizing.qty_y / price_y
        qty_x = sizing.qty_x / price_x

        if sig.signal == Signal.LONG_SPREAD:
            side_y, side_x = "buy", "sell"
        else:
            side_y, side_x = "sell", "buy"

        try:
            await self.orders.execute_pair(
                self.sym_y, side_y, qty_y, price_y,
                self.sym_x, side_x, qty_x, price_x,
            )
            self._in_position = True
            logger.info(
                f"ENTRY {sig.signal.name}: z={sig.zscore:.3f}, "
                f"beta={sig.beta:.4f}, size={sizing.final_size:.0f} USDT"
            )
        except Exception as e:
            logger.error(f"Entry failed: {e}")

    async def _close_position(self, sig, price_y, price_x, ts):
        logger.info(f"EXIT triggered: z={sig.zscore:.3f}, reason={sig.reason}")
        self._in_position = False

"""
execution/action_executor.py  —  ActionExecutor

Extracted from bybit_live_runner.py (Sprint 28 SRP refactor).
Handles order placement for entry_long / entry_short / exit actions.

Usage::

    executor = ActionExecutor(
        sym_y="BTCUSDT",
        sym_x="ETHUSDT",
        base_qty=0.01,
        dry_run=True,
    )
    await executor.execute(action, order_router, order_manager, notifier_bus, bar)
"""
from __future__ import annotations

from loguru import logger


class ActionExecutor:
    """
    Stateless order executor.

    Translates a decision string (``'entry_long'``, ``'entry_short'``,
    ``'exit'``) into real or simulated exchange orders via ``order_router``.

    In dry-run mode no orders are placed; all actions are logged only.
    """

    def __init__(
        self,
        sym_y: str,
        sym_x: str,
        base_qty: float,
        dry_run: bool = True,
    ) -> None:
        self._sym_y    = sym_y
        self._sym_x    = sym_x
        self._base_qty = base_qty
        self._dry_run  = dry_run

    async def execute(
        self,
        action: str,
        order_router,
        order_manager,
        notifier_bus,
        bar,
    ) -> None:
        """
        Execute ``action`` against the exchange.

        Parameters
        ----------
        action:
            One of ``'entry_long'``, ``'entry_short'``, ``'exit'``.
        order_router:
            ``BybitOrderRouter`` or compatible mock.
        order_manager:
            ``OrderManager`` for state tracking.
        notifier_bus:
            ``NotifierBus`` for trade alerts (may be None).
        bar:
            Current bar object with ``.price_y``, ``.price_x``, ``.zscore``.
        """
        if self._dry_run:
            logger.info(
                "ActionExecutor [DRY RUN] {} | z={:.4f}",
                action.upper(), getattr(bar, "zscore", 0.0),
            )
            return

        try:
            from execution.bybit_order_router import OrderRequest, OrderSide, OrderType

            if action == "entry_long":
                await order_router.create_order(OrderRequest(
                    symbol=self._sym_y, side=OrderSide.BUY,
                    order_type=OrderType.MARKET, qty=self._base_qty, price=0.0,
                ))
                await order_router.create_order(OrderRequest(
                    symbol=self._sym_x, side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    qty=self._base_qty * bar.price_y / bar.price_x, price=0.0,
                ))
                order_manager.record_entry_long(
                    self._base_qty, bar.price_y, bar.price_x
                )
                logger.info(
                    "ActionExecutor ENTRY LONG | {} @{:.2f} | {} @{:.2f}",
                    self._sym_y, bar.price_y, self._sym_x, bar.price_x,
                )
                await self._notify(
                    notifier_bus,
                    f"\u2705 ENTRY LONG: {self._sym_y}/{self._sym_x} | z={bar.zscore:.4f}",
                )

            elif action == "entry_short":
                await order_router.create_order(OrderRequest(
                    symbol=self._sym_y, side=OrderSide.SELL,
                    order_type=OrderType.MARKET, qty=self._base_qty, price=0.0,
                ))
                await order_router.create_order(OrderRequest(
                    symbol=self._sym_x, side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    qty=self._base_qty * bar.price_y / bar.price_x, price=0.0,
                ))
                order_manager.record_entry_short(
                    self._base_qty, bar.price_y, bar.price_x
                )
                logger.info(
                    "ActionExecutor ENTRY SHORT | {} @{:.2f} | {} @{:.2f}",
                    self._sym_y, bar.price_y, self._sym_x, bar.price_x,
                )
                await self._notify(
                    notifier_bus,
                    f"\u2705 ENTRY SHORT: {self._sym_y}/{self._sym_x} | z={bar.zscore:.4f}",
                )

            elif action == "exit":
                pos = order_manager.current_position
                if pos:
                    await order_router.create_order(OrderRequest(
                        symbol=self._sym_y,
                        side=OrderSide.SELL if pos.y_side == "long" else OrderSide.BUY,
                        order_type=OrderType.MARKET, qty=abs(pos.y_qty), price=0.0,
                    ))
                    await order_router.create_order(OrderRequest(
                        symbol=self._sym_x,
                        side=OrderSide.BUY if pos.x_side == "short" else OrderSide.SELL,
                        order_type=OrderType.MARKET, qty=abs(pos.x_qty), price=0.0,
                    ))
                    order_manager.record_exit(bar.price_y, bar.price_x)
                    logger.info(
                        "ActionExecutor EXIT | PnL={:+.4f}",
                        order_manager.current_pnl,
                    )
                    await self._notify(
                        notifier_bus,
                        f"\u2705 EXIT: PnL={order_manager.current_pnl:.4f}",
                    )

        except Exception as exc:
            logger.error("ActionExecutor '{}' failed: {}", action, exc)
            try:
                from execution.circuit_breaker import CircuitBreaker
                CircuitBreaker.record_failure()
            except Exception:
                pass
            await self._notify(
                notifier_bus,
                f"\u274c ACTION FAILED: {action} | {exc}",
                level="error",
            )

    @staticmethod
    async def _notify(bus, message: str, level: str = "success") -> None:
        if not bus:
            return
        try:
            await bus.send_alert(message, level=level)
        except Exception:
            pass

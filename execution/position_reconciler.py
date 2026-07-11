"""
execution/position_reconciler.py — QuantLuna Position Reconciler v2

Phase 0.5: La startup scaneaza COMPLET contul Bybit:
  1. scan_wallet_balance() — balanta USDT (equity, available, uPnL total)
  2. scan_all_positions()  — TOATE pozitiile deschise (nu doar symbol_y/x)
  3. fetch()               — backwards compat: AdoptedPosition pt symbol_y + symbol_x

BootScanResult contine tot ce trebuie pentru:
  - inject in OrderManager (adopt_position)
  - notificare Telegram cu situatia completa
  - decizie: continua pozitie existenta sau start fresh

Telegram output la boot::
    ⚡ QuantLuna Boot Scan
    💰 Balanta: 1,243.50 USDT (disponibil: 890.00) uPnL: +12.34
    ⚠️ Pozitii deschise: 2
      🟢 BTCUSDT LONG 0.001000 @ 67432.00 uPnL: +8.20
      🔴 ETHUSDT SHORT 0.050000 @ 3210.00 uPnL: +4.14
    🔄 Adoptata: BTCUSDT LONG + ETHUSDT SHORT uPnL: +12.34
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AdoptedPosition:
    """Pozitie detectata pe platforma la startup (backwards compat)."""
    symbol_y: str
    symbol_x: str
    y_side: str        # 'long' | 'short' | 'none'
    x_side: str        # 'long' | 'short' | 'none'
    y_qty: float
    x_qty: float
    y_entry_price: float
    x_entry_price: float
    unrealised_pnl: float = 0.0
    source: str = "bybit_rest"   # 'bybit_rest' | 'checkpoint'

    @property
    def has_position(self) -> bool:
        return self.y_qty > 0 or self.x_qty > 0

    def __repr__(self) -> str:
        return (
            f"AdoptedPosition({self.symbol_y} {self.y_side} {self.y_qty:.6f} "
            f"@ {self.y_entry_price:.4f} | "
            f"{self.symbol_x} {self.x_side} {self.x_qty:.6f} "
            f"@ {self.x_entry_price:.4f} | "
            f"uPnL={self.unrealised_pnl:.4f} USDT | src={self.source})"
        )


@dataclass
class WalletBalance:
    """Balanta wallet USDT la momentul boot-ului."""
    equity: float = 0.0
    available: float = 0.0
    unrealised_pnl: float = 0.0
    wallet_balance: float = 0.0
    coin: str = "USDT"

    def __repr__(self) -> str:
        return (
            f"WalletBalance(equity={self.equity:.2f} USDT | "
            f"available={self.available:.2f} | "
            f"uPnL={self.unrealised_pnl:+.4f} | "
            f"wallet={self.wallet_balance:.2f})"
        )


@dataclass
class OpenPosition:
    """O pozitie deschisa detectata pe Bybit (orice simbol)."""
    symbol: str
    side: str          # 'long' | 'short'
    qty: float
    entry_price: float
    mark_price: float = 0.0
    unrealised_pnl: float = 0.0
    leverage: float = 1.0
    liquidation_price: float = 0.0

    def __repr__(self) -> str:
        return (
            f"OpenPosition({self.symbol} {self.side} qty={self.qty:.6f} "
            f"entry={self.entry_price:.4f} mark={self.mark_price:.4f} "
            f"uPnL={self.unrealised_pnl:+.4f} liq={self.liquidation_price:.4f})"
        )


@dataclass
class BootScanResult:
    """Rezultatul complet al scan-ului la boot."""
    wallet: Optional[WalletBalance] = None
    positions: list[OpenPosition] = field(default_factory=list)
    adopted: Optional[AdoptedPosition] = None
    scan_ok: bool = True
    error: str = ""

    @property
    def has_open_positions(self) -> bool:
        return len(self.positions) > 0

    @property
    def total_upnl(self) -> float:
        return sum(p.unrealised_pnl for p in self.positions)

    def log_summary(self) -> None:
        """Logeaza situatia completa la boot."""
        if not self.scan_ok:
            logger.error("BootScan: ESUAT — %s", self.error)
            return

        if self.wallet:
            logger.info(
                "BootScan BALANTA: equity=%.2f USDT | available=%.2f | "
                "uPnL=%+.4f | wallet=%.2f",
                self.wallet.equity,
                self.wallet.available,
                self.wallet.unrealised_pnl,
                self.wallet.wallet_balance,
            )
        else:
            logger.warning("BootScan: balanta indisponibila")

        if not self.positions:
            logger.info("BootScan POZITII: nicio pozitie deschisa — start fresh")
        else:
            logger.warning(
                "BootScan POZITII: %d pozitie(i) deschise detectate:",
                len(self.positions),
            )
            for pos in self.positions:
                logger.warning("  %s", pos)

        if self.adopted:
            logger.info("BootScan ADOPTATA: %s", self.adopted)

    def to_telegram_msg(self) -> str:
        """
        Formeaza mesajul Telegram pentru notificarea de boot.

        Format::
            ⚡ QuantLuna Boot Scan
            💰 Balanta: 1,243.50 USDT (disponibil: 890.00) uPnL: +12.34
            ⚠️ Pozitii deschise: 2
              🟢 BTCUSDT LONG 0.001000 @ 67432.00 uPnL: +8.20
              🔴 ETHUSDT SHORT 0.050000 @ 3210.00 uPnL: +4.14
            🔄 Adoptata: BTCUSDT LONG + ETHUSDT SHORT uPnL: +12.34
        """
        lines = ["\u26a1 *QuantLuna Boot Scan*"]

        # Balanta
        if self.wallet:
            lines.append(
                f"\U0001f4b0 *Balanta:* `{self.wallet.equity:,.2f} USDT` "
                f"(disponibil: `{self.wallet.available:,.2f}`) "
                f"uPnL: `{self.wallet.unrealised_pnl:+.2f}`"
            )
        else:
            lines.append("\u26a0\ufe0f Balanta: indisponibila")

        # Pozitii
        if not self.positions:
            lines.append("\u2705 *Pozitii:* nicio pozitie deschisa")
        else:
            lines.append(f"\u26a0\ufe0f *Pozitii deschise: {len(self.positions)}*")
            for pos in self.positions:
                icon = "\U0001f7e2" if pos.unrealised_pnl >= 0 else "\U0001f534"
                liq_str = f" liq=`{pos.liquidation_price:.2f}`" if pos.liquidation_price > 0 else ""
                lines.append(
                    f"  {icon} `{pos.symbol}` {pos.side.upper()} "
                    f"`{pos.qty:.6f}` @ `{pos.entry_price:.4f}`"
                    f" uPnL: `{pos.unrealised_pnl:+.2f}`"
                    f"{liq_str}"
                )

        # Pozitia adoptata y/x
        if self.adopted and self.adopted.has_position:
            lines.append(
                f"\U0001f504 *Adoptata:* "
                f"`{self.adopted.symbol_y}` {self.adopted.y_side.upper()} + "
                f"`{self.adopted.symbol_x}` {self.adopted.x_side.upper()} "
                f"uPnL: `{self.adopted.unrealised_pnl:+.2f}`"
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PositionReconciler v2
# ---------------------------------------------------------------------------

class PositionReconciler:
    """
    Boot scanner complet pentru Bybit.

    La fiecare pornire a runner-ului:
      1. Citeste balanta USDT (equity, available, uPnL)
      2. Scaneaza TOATE pozitiile deschise din cont
      3. Identifica si adopta pozitia specifica symbol_y + symbol_x

    Nu ridica exceptii — orice eroare e logata si runner continua.
    """

    def __init__(
        self,
        order_router: Any,
        symbol_y: str,
        symbol_x: str,
        category: str = "linear",
    ) -> None:
        self._router = order_router
        self._symbol_y = symbol_y
        self._symbol_x = symbol_x
        self._category = category

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def boot_scan(self) -> BootScanResult:
        """
        Scan complet la boot: balanta + toate pozitiile.
        Returneaza BootScanResult gata pentru notificare Telegram si adoptie.
        """
        result = BootScanResult()

        # 1. Balanta USDT
        try:
            result.wallet = await self.scan_wallet_balance()
        except Exception as exc:
            logger.warning("BootScan: wallet fetch failed: %s", exc)

        # 2. Toate pozitiile deschise
        try:
            result.positions = await self.scan_all_positions()
        except Exception as exc:
            logger.warning("BootScan: positions fetch failed: %s", exc)
            result.scan_ok = False
            result.error = str(exc)

        # 3. Extrage perechea y/x pentru adoptie
        try:
            result.adopted = self._extract_adopted(result.positions)
        except Exception as exc:
            logger.debug("BootScan: extract_adopted failed: %s", exc)

        result.log_summary()
        return result

    async def scan_wallet_balance(self) -> Optional[WalletBalance]:
        """Citeste balanta USDT din Bybit /v5/account/wallet-balance."""
        # Incearca UNIFIED, fallback CONTRACT
        for account_type in ("UNIFIED", "CONTRACT"):
            try:
                raw = await self._call_rest(
                    "get_wallet_balance",
                    accountType=account_type,
                )
                coins = (
                    raw.get("result", {})
                       .get("list", [{}])[0]
                       .get("coin", [])
                )
                usdt = next((c for c in coins if c.get("coin") == "USDT"), None)
                if usdt:
                    return WalletBalance(
                        equity=float(
                            usdt.get("equity")
                            or usdt.get("walletBalance")
                            or 0
                        ),
                        available=float(
                            usdt.get("availableToWithdraw")
                            or usdt.get("availableToBorrow")
                            or 0
                        ),
                        unrealised_pnl=float(usdt.get("unrealisedPnl") or 0),
                        wallet_balance=float(usdt.get("walletBalance") or 0),
                        coin="USDT",
                    )
            except Exception as exc:
                logger.debug("scan_wallet_balance [%s]: %s", account_type, exc)
                continue

        logger.warning("scan_wallet_balance: USDT coin negasit in niciun account type")
        return None

    async def scan_all_positions(self) -> list[OpenPosition]:
        """
        Returneaza TOATE pozitiile deschise (size > 0) din cont,
        indiferent de simbol.
        Foloseste settleCoin=USDT pentru a prinde toate linear perps.
        """
        try:
            raw = await self._call_rest(
                "get_positions",
                category=self._category,
                settleCoin="USDT",
            )
            items = raw.get("result", {}).get("list", [])
        except Exception as exc:
            logger.warning("scan_all_positions: %s", exc)
            return []

        positions: list[OpenPosition] = []
        for item in items:
            qty = abs(float(item.get("size") or 0))
            if qty == 0:
                continue
            positions.append(OpenPosition(
                symbol=item.get("symbol", ""),
                side=self._parse_side(item.get("side", "None")),
                qty=qty,
                entry_price=float(item.get("avgPrice") or 0),
                mark_price=float(item.get("markPrice") or 0),
                unrealised_pnl=float(item.get("unrealisedPnl") or 0),
                leverage=float(item.get("leverage") or 1),
                liquidation_price=float(item.get("liqPrice") or 0),
            ))

        if positions:
            logger.warning(
                "scan_all_positions: %d pozitie(i) deschise — %s",
                len(positions),
                [p.symbol for p in positions],
            )
        else:
            logger.info("scan_all_positions: cont curat, nicio pozitie deschisa")

        return positions

    async def fetch(self) -> Optional[AdoptedPosition]:
        """
        Backwards compat: returneaza AdoptedPosition pt symbol_y + symbol_x.
        Apelat din _reconcile_positions() in runner.
        Intern foloseste scan_all_positions() — nu mai face 2 call-uri separate.
        """
        try:
            positions = await self.scan_all_positions()
            return self._extract_adopted(positions)
        except Exception as exc:
            logger.warning("PositionReconciler.fetch() esuat: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_adopted(
        self, positions: list[OpenPosition]
    ) -> Optional[AdoptedPosition]:
        """
        Din lista completa de pozitii, extrage perechea symbol_y + symbol_x
        si construieste AdoptedPosition pentru OrderManager.
        """
        pos_y = next((p for p in positions if p.symbol == self._symbol_y), None)
        pos_x = next((p for p in positions if p.symbol == self._symbol_x), None)

        if pos_y is None and pos_x is None:
            return None

        return AdoptedPosition(
            symbol_y=self._symbol_y,
            symbol_x=self._symbol_x,
            y_side=pos_y.side if pos_y else "none",
            x_side=pos_x.side if pos_x else "none",
            y_qty=pos_y.qty if pos_y else 0.0,
            x_qty=pos_x.qty if pos_x else 0.0,
            y_entry_price=pos_y.entry_price if pos_y else 0.0,
            x_entry_price=pos_x.entry_price if pos_x else 0.0,
            unrealised_pnl=(
                (pos_y.unrealised_pnl if pos_y else 0.0)
                + (pos_x.unrealised_pnl if pos_x else 0.0)
            ),
            source="bybit_rest",
        )

    async def _call_rest(self, method: str, **kwargs) -> dict:
        """
        Apeleaza metoda pe session-ul pybit al order_router.
        Suporta atat session sincrona (pybit HTTP) cat si router async.
        """
        # 1. Router async nativ
        if hasattr(self._router, method):
            fn = getattr(self._router, method)
            if asyncio.iscoroutinefunction(fn):
                return await fn(**kwargs)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: fn(**kwargs))

        # 2. Pybit sync session (accesat prin _session sau session)
        session = (
            getattr(self._router, "_session", None)
            or getattr(self._router, "session", None)
        )
        if session and hasattr(session, method):
            fn = getattr(session, method)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: fn(**kwargs))

        logger.warning("_call_rest: metoda '%s' indisponibila pe router/session", method)
        return {}

    @staticmethod
    def _parse_side(side_str: str) -> str:
        s = (side_str or "").lower()
        if s == "buy":
            return "long"
        if s == "sell":
            return "short"
        return "none"

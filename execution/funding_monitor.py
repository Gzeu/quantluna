"""
QuantLuna — FundingMonitor Sprint 6

Async polling task pentru funding rates live pe ambele legs ale pair-ului.
Publică în StateBus la fiecare interval configurabil.

Design:
- Rulează ca asyncio.Task independent, lansat de LiveTrader.run()
- Polling CCXT fetch_funding_rate() — nu WebSocket (disponibilitate variabilă pe Bybit)
- Publică funding_y, funding_x, funding_net în StateSnapshot
- La eroare de fetch: păstrează ultima valoare valabilă + loghează warning
- Stop prin asyncio.CancelledError (task cancellation din LiveTrader)

Risc real:
- Bybit returnează funding rate la momentul curent, nu acumulat;
  anualizarea este o estimare (rate * 3 * 365) pentru perpetual cu funding la 8h.
- La schimbarea regimului de funding interval (Bybit USDT perp: 8h, unii contracte: 4h),
  multiplicatorul trebuie ajustat manual în FundingConfig.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import ccxt.async_support as ccxt
from loguru import logger


@dataclass
class FundingConfig:
    sym_y: str                    # e.g. "ETH/USDT:USDT"
    sym_x: str                    # e.g. "BTC/USDT:USDT"
    poll_interval_s: float = 60.0 # seconds between polls
    funding_periods_per_year: float = 3.0 * 365.0  # 3 x/day * 365
    exchange_id: str = "bybit"
    testnet: bool = False


class FundingMonitor:
    """
    Lansează un asyncio.Task care polling funding rates și publică în bus.

    Usage:
        monitor = FundingMonitor(cfg, exchange, bus)
        task = asyncio.create_task(monitor.run())
        # la shutdown:
        task.cancel()
    """

    def __init__(self, cfg: FundingConfig, exchange: ccxt.Exchange, bus) -> None:
        self.cfg = cfg
        self.exchange = exchange
        self.bus = bus
        self._last_y: float = 0.0
        self._last_x: float = 0.0

    async def run(self) -> None:
        """Main polling loop. Runs until CancelledError."""
        logger.info(
            f"FundingMonitor started — {self.cfg.sym_y} / {self.cfg.sym_x} "
            f"poll={self.cfg.poll_interval_s}s"
        )
        while True:
            try:
                await self._poll_and_publish()
            except asyncio.CancelledError:
                logger.info("FundingMonitor stopped (cancelled)")
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"FundingMonitor poll error: {exc} — using last known values")
            await asyncio.sleep(self.cfg.poll_interval_s)

    async def _poll_and_publish(self) -> None:
        """Fetch both legs and push to StateBus."""
        fy = await self._fetch_annualized(self.cfg.sym_y)
        fx = await self._fetch_annualized(self.cfg.sym_x)

        if fy is not None:
            self._last_y = fy
        if fx is not None:
            self._last_x = fx

        net = self._last_y - self._last_x

        # FIX-BUS: guard — bus poate fi None în primele secunde de inițializare
        if self.bus is None:
            logger.debug("FundingMonitor: bus not ready yet, skipping publish")
            return

        self.bus.update({
            "funding_y": self._last_y,
            "funding_x": self._last_x,
            "funding_net": net,
        })

        logger.debug(
            f"Funding — Y={self._last_y:.6f} X={self._last_x:.6f} "
            f"net={net:+.6f} (annualized)"
        )

    async def _fetch_annualized(self, symbol: str) -> Optional[float]:
        """
        Fetch current funding rate and annualize.
        Returns None on error (caller uses last known value).
        """
        try:
            data = await self.exchange.fetch_funding_rate(symbol)
            rate = float(data.get("fundingRate", 0.0) or 0.0)
            return rate * self.cfg.funding_periods_per_year
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"fetch_funding_rate({symbol}) failed: {exc}")
            return None


async def create_funding_monitor(
    cfg: FundingConfig,
    api_key: str,
    api_secret: str,
    bus,
) -> tuple["FundingMonitor", ccxt.Exchange]:
    """
    Factory helper — creează exchange CCXT async și FundingMonitor.
    Returnează (monitor, exchange) pentru ca exchange-ul să poată fi
    închis explicit la shutdown.

    Usage:
        monitor, exchange = await create_funding_monitor(cfg, key, secret, bus)
        task = asyncio.create_task(monitor.run())
        # la shutdown:
        task.cancel()
        await exchange.close()
    """
    exchange_cls = getattr(ccxt, cfg.exchange_id)
    exchange = exchange_cls({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "swap"},
    })
    if cfg.testnet:
        exchange.set_sandbox_mode(True)
    return FundingMonitor(cfg, exchange, bus), exchange

"""
execution/spot_wallet_scanner.py  -  QuantLuna Spot Wallet Scanner v1.0

Sprint S30 (2026-07-12):
  Scaneaza wallet-ul Spot Bybit si produce un SpotWalletReport.
  Analog cu PositionScanner (futures) dar pentru active spot.

  SpotWalletReport contine:
    - holdings: lista SpotHolding (toate activele cu valoare > min_usdt)
    - total_usdt: valoare totala estimata
    - free_usdt: USDT lichid disponibil
    - significant_assets: active cu valoare > threshold (candidati strategii)

Usage::

    router = SpotOrderRouter.from_env()
    scanner = SpotWalletScanner(router, min_usdt_value=5.0)
    report = await scanner.scan()
    print(report.summary())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from execution.spot_order_router import SpotOrderRouter, SpotHolding


@dataclass
class SpotWalletReport:
    holdings: List[SpotHolding] = field(default_factory=list)
    total_usdt_value: float = 0.0
    free_usdt: float = 0.0
    significant_threshold_usdt: float = 10.0

    @property
    def significant_assets(self) -> List[SpotHolding]:
        """Active cu valoare estimata > threshold (exclude USDT)."""
        return [
            h for h in self.holdings
            if h.asset != "USDT" and h.total > 0
        ]

    @property
    def has_significant_assets(self) -> bool:
        return bool(self.significant_assets)

    def summary(self) -> str:
        lines = [
            f"SpotWalletReport: {len(self.holdings)} assets",
            f"  Free USDT: {self.free_usdt:.2f}",
            f"  Total USDT value: {self.total_usdt_value:.2f}",
        ]
        for h in self.significant_assets:
            lines.append(
                f"  {h.asset}: {h.total:.6f} (avg_buy={h.avg_buy_price:.4f})"
            )
        return "\n".join(lines)

    def to_telegram_msg(self) -> str:
        if not self.holdings:
            return "📊 Spot wallet: gol"
        lines = [f"📊 *Spot Wallet Scan*"]
        lines.append(f"💵 Free USDT: `{self.free_usdt:.2f}`")
        if self.significant_assets:
            lines.append("📦 *Active spot:*")
            for h in self.significant_assets:
                lines.append(f"  • `{h.asset}`: {h.total:.6f}")
        return "\n".join(lines)


class SpotWalletScanner:
    """
    Scaneaza wallet-ul spot si construieste SpotWalletReport.

    Integrat in WorkflowOrchestrator FAZA 1.5 (dupa PositionScanner futures)
    cand `markets` include 'spot' in runner_cfg.
    """

    def __init__(
        self,
        router: SpotOrderRouter,
        min_usdt_value: float = 5.0,
        significant_threshold_usdt: float = 10.0,
        price_feed: Optional[object] = None,
    ) -> None:
        self._router = router
        self._min_usdt = min_usdt_value
        self._significant_threshold = significant_threshold_usdt
        self._price_feed = price_feed  # optional, pentru calc valoare live

    async def scan(self) -> SpotWalletReport:
        """Executa scanarea si returneaza raportul."""
        logger.info("[SpotWalletScanner] Incep scan spot wallet...")
        try:
            holdings = await self._router.fetch_holdings(
                min_usdt_value=self._min_usdt
            )
        except Exception as exc:
            logger.error("[SpotWalletScanner] fetch_holdings failed: {}", exc)
            return SpotWalletReport(
                significant_threshold_usdt=self._significant_threshold
            )

        free_usdt = 0.0
        total_usdt_value = 0.0

        enriched: List[SpotHolding] = []
        for h in holdings:
            if h.asset == "USDT":
                free_usdt = h.free
                total_usdt_value += h.total
                enriched.append(h)
                continue

            # Calculeaza valoare USDT daca avem price feed
            if self._price_feed is not None:
                try:
                    ticker = await self._router.fetch_ticker(h.symbol)
                    last_price = ticker.get("last", 0.0)
                    usd_val = h.total * last_price
                    h.unrealised_pnl = (
                        (last_price - h.avg_buy_price) * h.total
                        if h.avg_buy_price > 0 else 0.0
                    )
                    total_usdt_value += usd_val
                except Exception:
                    pass

            enriched.append(h)

        report = SpotWalletReport(
            holdings=enriched,
            total_usdt_value=total_usdt_value,
            free_usdt=free_usdt,
            significant_threshold_usdt=self._significant_threshold,
        )
        logger.info("[SpotWalletScanner] {}", report.summary())
        return report

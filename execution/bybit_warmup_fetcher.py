"""
execution/bybit_warmup_fetcher.py  —  QuantLuna Bybit Warmup Fetcher
Sprint S20 — 2026-07-11

Fetch-ează bare istorice din Bybit REST API (/v5/market/kline) pentru
am ambele simboluri și le injectează în SpreadMonitor înainte de
pornirea loop-ului WebSocket live.

Această abordare elimină așteptarea de 8+ ore pentru 100 bare de 5min
în timp real — datele istorice sunt disponibile în ~2-3 secunde.

Flux:
    runner.run()
      ├─ Phase 0 (NOU): BybitWarmupFetcher.fetch()
      │     ├─ REST GET /v5/market/kline (BTCUSDT, 100 bare)
      │     ├─ REST GET /v5/market/kline (ETHUSDT, 100 bare)
      │     ├─ Aliniere temporală a barelor (inner join pe timestamp)
      │     ├─ SpreadMonitor.update(price_y, price_x) × N bare
      │     └─ bus.publish("warmup_status", ...) la fiecare 10 bare
      └─ Phase 4: _run_loop() → WS live (warm-up deja complet)

Fallback:
    Dacă REST eșuează (network, auth, rate-limit) → runner continuă
    cu warm-up progresiv prin WS live (comportamentul anterior).

Usage:
    fetcher = BybitWarmupFetcher(
        symbol_y="BTCUSDT",
        symbol_x="ETHUSDT",
        interval=5,
        n_bars=100,
        testnet=False,
        category="linear",
    )
    n_injected = await fetcher.fetch(spread_monitor, state_bus)
    # n_injected == numărul de bare injectate (0 dacă REST a eșuat)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

from loguru import logger

if TYPE_CHECKING:
    from core.spread_monitor import SpreadMonitor
    from core.state_bus import StateBus


@dataclass
class _KlineBar:
    """Un singur bar OHLCV din răspunsul Bybit V5."""
    ts_ms: int      # timestamp open în milisecunde
    open:  float
    high:  float
    low:   float
    close: float
    volume: float


class BybitWarmupFetcher:
    """
    Fetch-ează bare istorice pentru warmup via Bybit REST v5.

    Parameters
    ----------
    symbol_y  : primul simbol (ex. "BTCUSDT")
    symbol_x  : al doilea simbol (ex. "ETHUSDT")
    interval  : interval kline în minute (ex. 5)
    n_bars    : număr de bare istorice de fetch-at (default 100)
    testnet   : True pentru Bybit testnet
    category  : "linear" | "inverse" | "spot" (default "linear")
    request_timeout : timeout HTTP în secunde (default 10)
    """

    _REST_MAINNET = "https://api.bybit.com"
    _REST_TESTNET = "https://api-testnet.bybit.com"
    _KLINE_ENDPOINT = "/v5/market/kline"
    _MAX_LIMIT = 1000  # limita maximă per request Bybit V5

    def __init__(
        self,
        symbol_y: str = "BTCUSDT",
        symbol_x: str = "ETHUSDT",
        interval: int = 5,
        n_bars: int = 100,
        testnet: bool = False,
        category: str = "linear",
        request_timeout: int = 10,
    ) -> None:
        self.symbol_y        = symbol_y
        self.symbol_x        = symbol_x
        self.interval        = interval
        self.n_bars          = min(n_bars, self._MAX_LIMIT)
        self.testnet         = testnet
        self.category        = category
        self.request_timeout = request_timeout
        self._base_url       = self._REST_TESTNET if testnet else self._REST_MAINNET

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch(
        self,
        spread_monitor: "SpreadMonitor",
        state_bus: Optional["StateBus"] = None,
    ) -> int:
        """
        Fetch bare istorice și injectează în spread_monitor.

        Returns
        -------
        int
            Numărul de bare injectate (0 dacă REST a eșuat).
        """
        logger.info(
            f"BybitWarmupFetcher: fetch {self.n_bars} bare "
            f"{self.symbol_y}/{self.symbol_x} interval={self.interval}m "
            f"({'testnet' if self.testnet else 'mainnet'})"
        )

        try:
            bars_y, bars_x = await asyncio.gather(
                self._fetch_klines(self.symbol_y),
                self._fetch_klines(self.symbol_x),
                return_exceptions=False,
            )
        except Exception as exc:
            logger.warning(
                f"BybitWarmupFetcher: REST fetch eșuat ({exc}) — "
                "warm-up progresiv via WS va fi folosit"
            )
            return 0

        if not bars_y or not bars_x:
            logger.warning(
                "BybitWarmupFetcher: răspuns gol de la Bybit REST — "
                "warm-up progresiv via WS va fi folosit"
            )
            return 0

        aligned = self._align_bars(bars_y, bars_x)
        if not aligned:
            logger.warning(
                f"BybitWarmupFetcher: 0 bare aliniate din "
                f"{len(bars_y)} Y + {len(bars_x)} X — warm-up WS"
            )
            return 0

        n_injected = self._inject(aligned, spread_monitor, state_bus)
        logger.info(
            f"BybitWarmupFetcher: ✅ {n_injected} bare injectate — "
            "warm-up REST complet, WS loop porneste gata"
        )
        return n_injected

    # ------------------------------------------------------------------
    # REST fetch
    # ------------------------------------------------------------------

    async def _fetch_klines(self, symbol: str) -> List[_KlineBar]:
        """
        GET /v5/market/kline pentru un simbol.
        Returnează lista de _KlineBar sortată crescător după timestamp.
        """
        import aiohttp

        url = f"{self._base_url}{self._KLINE_ENDPOINT}"
        params = {
            "category": self.category,
            "symbol":   symbol,
            "interval": str(self.interval),
            "limit":    str(self.n_bars),
        }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.request_timeout)
        ) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"Bybit REST {symbol}: HTTP {resp.status}"
                    )
                data = await resp.json()

        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            raise RuntimeError(
                f"Bybit REST {symbol}: retCode={ret_code} "
                f"msg={data.get('retMsg', 'unknown')}"
            )

        # Bybit V5 kline format:
        # result.list = [[startTime, open, high, low, close, volume, turnover], ...]
        # Returnat descrescător (newest first) → inversăm
        raw_list = data.get("result", {}).get("list", [])
        bars: List[_KlineBar] = []
        for row in reversed(raw_list):   # ascending order
            try:
                bars.append(_KlineBar(
                    ts_ms  = int(row[0]),
                    open   = float(row[1]),
                    high   = float(row[2]),
                    low    = float(row[3]),
                    close  = float(row[4]),
                    volume = float(row[5]),
                ))
            except (IndexError, ValueError, TypeError) as exc:
                logger.debug(f"BybitWarmupFetcher: skip row parse error: {exc}")
                continue

        logger.debug(
            f"BybitWarmupFetcher: {symbol} → {len(bars)} bare "
            f"(ts[0]={bars[0].ts_ms if bars else 'N/A'} "
            f"ts[-1]={bars[-1].ts_ms if bars else 'N/A'})"
        )
        return bars

    # ------------------------------------------------------------------
    # Aliniere temporală
    # ------------------------------------------------------------------

    def _align_bars(
        self,
        bars_y: List[_KlineBar],
        bars_x: List[_KlineBar],
    ) -> List[Tuple[_KlineBar, _KlineBar]]:
        """
        Inner join pe timestamp (ts_ms): returnează perechi (bar_y, bar_x)
        cu timestamp identic. Toleranță ±30s pentru desync mic de feed.
        """
        tolerance_ms = 30_000  # 30 secunde

        # Index bars_x după ts_ms pentru lookup O(1)
        index_x = {b.ts_ms: b for b in bars_x}

        aligned: List[Tuple[_KlineBar, _KlineBar]] = []
        for by in bars_y:
            # Match exact
            bx = index_x.get(by.ts_ms)
            if bx is None:
                # Căutare în fereastră de toleranță
                for ts_x, candidate in index_x.items():
                    if abs(ts_x - by.ts_ms) <= tolerance_ms:
                        bx = candidate
                        break
            if bx is not None:
                aligned.append((by, bx))

        logger.debug(
            f"BybitWarmupFetcher: align → {len(aligned)} perechi "
            f"din {len(bars_y)} Y + {len(bars_x)} X"
        )
        return aligned

    # ------------------------------------------------------------------
    # Injectare în SpreadMonitor + publish state_bus
    # ------------------------------------------------------------------

    def _inject(
        self,
        aligned: List[Tuple[_KlineBar, _KlineBar]],
        spread_monitor: "SpreadMonitor",
        state_bus: Optional["StateBus"],
    ) -> int:
        """
        Injectează perechile de bare în spread_monitor.update() și
        publică warmup_status în state_bus la fiecare 10 bare.

        Returns numărul de bare injectate cu succes.
        """
        n_required  = getattr(spread_monitor, "warmup_bars", self.n_bars)
        n_injected  = 0

        for by, bx in aligned:
            try:
                spread_monitor.update(by.close, bx.close)
                n_injected += 1
            except Exception as exc:
                logger.debug(
                    f"BybitWarmupFetcher: spread_monitor.update error @ bar {n_injected}: {exc}"
                )
                continue

            # Publică progres la fiecare 10 bare
            if state_bus is not None and n_injected % 10 == 0:
                pct = min(1.0, n_injected / max(n_required, 1))
                try:
                    state_bus.publish("warmup_status", {
                        "bars_done":     n_injected,
                        "bars_required": n_required,
                        "pct":           round(pct, 4),
                        "source":        "rest",
                        "ready":         pct >= 1.0,
                        "ts":            int(time.time() * 1000),
                    })
                except Exception as exc:
                    logger.debug(f"BybitWarmupFetcher: state_bus.publish failed: {exc}")

        # Publică status final
        if state_bus is not None and n_injected > 0:
            pct = min(1.0, n_injected / max(n_required, 1))
            try:
                state_bus.publish("warmup_status", {
                    "bars_done":     n_injected,
                    "bars_required": n_required,
                    "pct":           round(pct, 4),
                    "source":        "rest",
                    "ready":         pct >= 1.0,
                    "ts":            int(time.time() * 1000),
                })
            except Exception:
                pass

        return n_injected

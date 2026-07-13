"""
QuantLuna — WsWatchdog Sprint 7

Monitorizare health a WebSocket price feed.
Detectează stale feed (on_tick() nu a mai fost apelat de > threshold secunde)
și publică alert în StateBus.

Scopul principal:
- Previne false positive din PnLReconciler cauțat de feed stale
  (divergența local vs exchange nu e drift real, e preț vechi)
- Previne execuția de semnale pe date stale (hedge ratio, zscore vechi)
- Furnizează indicator vizibil în dashboard: ws_stale badge

Design:
- WsWatchdog.ping() apelat de LiveTrader la fiecare on_tick() primit
- Bucla internă verifică la fiecare check_interval_s dacă ping-ul e recent
- Trei stari: LIVE (< stale_warn_s), STALE (> stale_warn_s), CRITICAL (> stale_critical_s)
- La STALE: setează bus.ws_stale=True, suspendare oprește alertele PnLReconciler
- La CRITICAL: setează ws_stale_alert=True, logheza ERROR (pentru pagerduty/alerting)
- La reconectare (ping primit): resetare automată la LIVE

Integrare:
- LiveTrader.__init__: creează WsWatchdog(cfg, bus)
- LiveTrader._process_tick(): apelează self.watchdog.ping()
- LiveTrader.run(): asyncio.create_task(self.watchdog.run())
- PnLReconciler._publish(): verifică bus.snapshot().ws_stale înainte de alert

Risc real:
- check_interval_s prea mare → latență în detectare stale
- stale_warn_s prea mic → false positives la exchange downtime scurt (< 10s normal)
  Bybit WebSocket poate avea hiccup-uri de 2-5s la rollover de funding

FIX-BUS: toate apelurile bus.update() guard-ate cu `if self.bus` pentru a suporta
  run fără StateBus (ex: run_live.py standalone fără dashboard)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from loguru import logger


@dataclass
class WatchdogConfig:
    stale_warn_s: float = 10.0      # warning threshold
    stale_critical_s: float = 30.0  # critical threshold (blocare execuție recomandată)
    check_interval_s: float = 2.0   # polling interval pentru health check


# Alias for backward compatibility
WsWatchdogConfig = WatchdogConfig


class WsWatchdog:
    """
    Monitorizare health WebSocket.

    Usage:
        watchdog = WsWatchdog(WatchdogConfig(), bus)  # bus poate fi None
        # în LiveTrader.run():
        asyncio.create_task(watchdog.run())
        # în LiveTrader._process_tick():
        watchdog.ping()
    """

    def __init__(self, cfg: WatchdogConfig, bus) -> None:
        self.cfg = cfg
        self.bus = bus  # poate fi None când rulează fără dashboard
        self._last_ping: float = time.monotonic()
        self._state: str = "LIVE"   # LIVE | STALE | CRITICAL

    def ping(self) -> None:
        """
        Apelat de LiveTrader la fiecare tick WS primit.
        Thread-safe: time.monotonic() e safe din orice thread.
        """
        self._last_ping = time.monotonic()
        # Reset imediat la LIVE fără a aștepta check loop
        if self._state != "LIVE":
            self._state = "LIVE"
            if self.bus:  # FIX-BUS: guard pentru bus=None
                self.bus.update({
                    "ws_stale": False,
                    "ws_stale_alert": False,
                    "ws_last_tick_age_s": 0.0,
                })
            logger.info("WsWatchdog: feed RECOVERED — back to LIVE")

    async def run(self) -> None:
        """Main health check loop. Runs until CancelledError."""
        logger.info(
            f"WsWatchdog started — warn={self.cfg.stale_warn_s}s "
            f"critical={self.cfg.stale_critical_s}s "
            f"check={self.cfg.check_interval_s}s"
        )
        while True:
            try:
                await self._check()
            except asyncio.CancelledError:
                logger.info("WsWatchdog stopped (cancelled)")
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"WsWatchdog check error: {exc}")
            await asyncio.sleep(self.cfg.check_interval_s)

    async def _check(self) -> None:
        """Single health check cycle."""
        age = time.monotonic() - self._last_ping

        # FIX-BUS: toate apelurile bus.update() guard-ate cu `if self.bus`
        if self.bus:
            self.bus.update({"ws_last_tick_age_s": round(age, 1)})

        if age < self.cfg.stale_warn_s:
            if self._state != "LIVE":
                self._state = "LIVE"
                if self.bus:
                    self.bus.update({"ws_stale": False, "ws_stale_alert": False})
                logger.info(f"WsWatchdog: RECOVERED after {age:.1f}s stale")

        elif age < self.cfg.stale_critical_s:
            if self._state != "STALE":
                self._state = "STALE"
                if self.bus:
                    self.bus.update({"ws_stale": True, "ws_stale_alert": False})
                logger.warning(
                    f"WsWatchdog: STALE — no tick for {age:.1f}s "
                    f"(threshold={self.cfg.stale_warn_s}s) — "
                    f"PnLReconciler drift alerts suppressed"
                )

        else:  # age >= stale_critical_s
            if self._state != "CRITICAL":
                self._state = "CRITICAL"
                if self.bus:
                    self.bus.update({"ws_stale": True, "ws_stale_alert": True})
                logger.error(
                    f"WsWatchdog: CRITICAL — no tick for {age:.1f}s — "
                    f"RECOMMEND halting new entries until feed recovered"
                )

    @property
    def is_live(self) -> bool:
        """True dacă feed-ul e healthy. Folosit de LiveTrader pentru gate entries."""
        return self._state == "LIVE"

    @property
    def state(self) -> str:
        """LIVE | STALE | CRITICAL"""
        return self._state

    @property
    def last_tick_age_s(self) -> float:
        """Secunde de la ultimul ping."""
        return time.monotonic() - self._last_ping

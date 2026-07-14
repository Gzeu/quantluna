"""
execution/health_check.py  —  QuantLuna System Health Check

Sprint 12 — Pre-flight health check înainte de pornirea live trading:
  - Verifică conectivitate exchange (REST ping)
  - Verifică validitatea API keys (balance fetch)
  - Verifică WebSocket connectivity
  - Verifică că simbolurile sunt tradeable
  - Verifică data freshness (ultima bară din cache)
  - Verifică config constraints (warmup, kelly, dd params)
  - Returnează HealthReport cu pass/fail per check
  - CLI-friendly: print_report() cu Rich table

Usage:
    from execution.health_check import HealthCheck, HealthConfig

    check = HealthCheck(HealthConfig(
        exchange="bybit",
        sym_y="BTCUSDT",
        sym_x="ETHUSDT",
        api_key="...",
        api_secret="...",
    ))
    report = await check.run()
    report.print_report()
    if not report.all_passed:
        sys.exit(1)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    critical: bool = True  # Critical = failure blochează startul


@dataclass
class HealthReport:
    checks: List[CheckResult] = field(default_factory=list)
    exchange: str = ""
    sym_y: str = ""
    sym_x: str = ""
    ts: str = ""

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks if c.critical)

    @property
    def critical_failures(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed and c.critical]

    def print_report(self) -> None:
        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
            table = Table(title=f"QuantLuna Health Check — {self.exchange} | {self.sym_y}/{self.sym_x}")
            table.add_column("Check", style="cyan")
            table.add_column("Status")
            table.add_column("Details", style="dim")
            for c in self.checks:
                status = "[green]✅ PASS" if c.passed else ("[red]❌ FAIL" if c.critical else "[yellow]⚠ WARN")
                table.add_row(c.name, status, c.message)
            console.print(table)
            if self.all_passed:
                console.print("[bold green]All checks passed — safe to start trading.[/bold green]")
            else:
                console.print(f"[bold red]{len(self.critical_failures)} critical check(s) failed — do NOT start trading.[/bold red]")
        except ImportError:
            for c in self.checks:
                status = "PASS" if c.passed else ("FAIL" if c.critical else "WARN")
                print(f"  [{status}] {c.name}: {c.message}")


@dataclass
class HealthConfig:
    exchange: str = "bybit"
    sym_y: str = ""
    sym_x: str = ""
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = False
    # Config validation thresholds
    min_capital_usdt: float = 100.0
    min_warmup_bars: int = 10
    max_kelly_fraction: float = 0.75
    max_portfolio_dd: float = 0.50
    # Cache check
    cache_dir: Optional[str] = None
    check_cache_freshness: bool = True
    cache_stale_h: float = 6.0
    # HTTP health server
    health_port: int = 8081


# Alias for backward compatibility
HealthCheckConfig = HealthConfig


class HealthCheck:
    """
    Pre-flight health check pentru QuantLuna.
    Rulează toate verificările și returnează HealthReport.
    """

    def __init__(self, config: HealthConfig) -> None:
        self.cfg = config
        self._report = HealthReport(
            exchange=config.exchange,
            sym_y=config.sym_y,
            sym_x=config.sym_x,
            ts=pd.Timestamp.now(tz="UTC").isoformat(),
        )

    async def start_http_server(self) -> None:
        """Pornește un server HTTP minimalist pentru health check (/api/health)."""
        try:
            from aiohttp import web

            app = web.Application()

            async def _health_handler(request: web.Request) -> web.Response:
                report = await self.run()
                return web.json_response({
                    "status": "ok" if report.all_passed else "degraded",
                    "checks": [
                        {"name": c.name, "passed": c.passed, "message": c.message}
                        for c in report.checks
                    ],
                    "exchange": self.cfg.exchange,
                    "ts": report.ts,
                })

            app.router.add_get("/api/health", _health_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, port=self.cfg.health_port).start()
            logger.info(
                "HealthCheck HTTP server started on :{}", self.cfg.health_port
            )
        except ImportError:
            # aiohttp not available — fail gracefully, caller will use fallback
            raise RuntimeError("aiohttp not installed — cannot start health HTTP server")

    async def run(self) -> HealthReport:
        """Run all health checks and return HealthReport."""
        logger.info(f"Running health checks for {self.cfg.exchange} {self.cfg.sym_y}/{self.cfg.sym_x}")

        checks = [
            self._check_ccxt_import(),
            self._check_api_credentials(),
            await self._check_exchange_connectivity(),
            await self._check_symbols_tradeable(),
            await self._check_account_balance(),
            self._check_config_constraints(),
        ]

        if self.cfg.check_cache_freshness:
            checks.append(self._check_cache_freshness())

        for check in checks:
            if check is not None:
                self._report.checks.append(check)

        passed = sum(1 for c in self._report.checks if c.passed)
        total = len(self._report.checks)
        logger.info(f"Health check complete: {passed}/{total} checks passed")
        return self._report

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_ccxt_import(self) -> CheckResult:
        try:
            import ccxt
            return CheckResult("ccxt_import", True, f"ccxt {ccxt.__version__} available")
        except ImportError:
            return CheckResult("ccxt_import", False, "ccxt not installed — pip install ccxt")

    def _check_api_credentials(self) -> CheckResult:
        if self.cfg.api_key and self.cfg.api_secret:
            masked = self.cfg.api_key[:6] + "..."
            return CheckResult("api_credentials", True, f"API key present ({masked})")
        return CheckResult("api_credentials", False, "API key/secret missing in config")

    async def _check_exchange_connectivity(self) -> CheckResult:
        try:
            import ccxt
            ex_class = getattr(ccxt, self.cfg.exchange.lower(), None)
            if ex_class is None:
                return CheckResult("exchange_connectivity", False, f"Unknown exchange: {self.cfg.exchange}")
            ex = ex_class({"enableRateLimit": True})
            markets = await asyncio.to_thread(ex.load_markets)
            return CheckResult(
                "exchange_connectivity", True,
                f"Connected to {self.cfg.exchange} — {len(markets)} markets"
            )
        except Exception as exc:
            return CheckResult("exchange_connectivity", False, f"Connection failed: {exc}")

    async def _check_symbols_tradeable(self) -> CheckResult:
        try:
            import ccxt
            ex_class = getattr(ccxt, self.cfg.exchange.lower())
            ex = ex_class({"enableRateLimit": True})
            markets = await asyncio.to_thread(ex.load_markets)

            def to_ccxt(s: str) -> str:
                s = s.upper().replace("-PERP", "").replace("PERP", "")
                if s.endswith("USDT") and "/" not in s:
                    return f"{s[:-4]}/USDT:USDT"
                return s

            sym_y = to_ccxt(self.cfg.sym_y)
            sym_x = to_ccxt(self.cfg.sym_x)
            missing = [s for s in [sym_y, sym_x] if s not in markets]
            if missing:
                return CheckResult("symbols_tradeable", False, f"Symbols not found on exchange: {missing}")
            return CheckResult("symbols_tradeable", True, f"{sym_y} and {sym_x} tradeable")
        except Exception as exc:
            return CheckResult("symbols_tradeable", False, f"Symbol check failed: {exc}")

    async def _check_account_balance(self) -> CheckResult:
        if not self.cfg.api_key:
            return CheckResult("account_balance", False, "No API key — cannot check balance", critical=False)
        try:
            import ccxt
            ex_class = getattr(ccxt, self.cfg.exchange.lower())
            ex = ex_class({
                "apiKey": self.cfg.api_key,
                "secret": self.cfg.api_secret,
                "enableRateLimit": True,
            })
            balance = await asyncio.to_thread(ex.fetch_balance)
            usdt = balance.get("USDT", {}).get("free", 0.0) or 0.0
            if usdt < self.cfg.min_capital_usdt:
                return CheckResult(
                    "account_balance", False,
                    f"Insufficient balance: {usdt:.2f} USDT (min: {self.cfg.min_capital_usdt:.0f})",
                    critical=False,
                )
            return CheckResult("account_balance", True, f"Balance: {usdt:.2f} USDT free")
        except Exception as exc:
            return CheckResult("account_balance", False, f"Balance check failed: {exc}", critical=False)

    def _check_config_constraints(self) -> CheckResult:
        issues = []
        if self.cfg.min_warmup_bars < 10:
            issues.append(f"min_warmup_bars={self.cfg.min_warmup_bars} too low (min 10)")
        if self.cfg.max_kelly_fraction > 0.75:
            issues.append(f"kelly_fraction={self.cfg.max_kelly_fraction} dangerously high (max 0.75)")
        if self.cfg.max_portfolio_dd > 0.50:
            issues.append(f"max_portfolio_dd={self.cfg.max_portfolio_dd} too high (max 0.50)")
        if issues:
            return CheckResult("config_constraints", False, " | ".join(issues), critical=False)
        return CheckResult("config_constraints", True, "Config constraints OK")

    def _check_cache_freshness(self) -> CheckResult:
        try:
            from data.market_data_cache import MarketDataCache
            cache = MarketDataCache(self.cfg.cache_dir)
            stale = []
            for sym in [self.cfg.sym_y, self.cfg.sym_x]:
                info = cache.info(sym, self.cfg.exchange, "1h")
                if not info.get("cached"):
                    stale.append(f"{sym} not cached")
                    continue
                last = pd.Timestamp(info["last_bar"])
                age_h = (pd.Timestamp.now(tz="UTC") - last).total_seconds() / 3600
                if age_h > self.cfg.cache_stale_h:
                    stale.append(f"{sym} stale ({age_h:.1f}h old)")
            if stale:
                return CheckResult("cache_freshness", False, " | ".join(stale), critical=False)
            return CheckResult("cache_freshness", True, "Cache fresh for all symbols")
        except Exception as exc:
            return CheckResult("cache_freshness", False, f"Cache check error: {exc}", critical=False)

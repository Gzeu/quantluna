"""
core/config_validator.py — validare completa a configuratiei la startup.

Verifica toate variabilele de mediu si parametrii critici inainte de a
porni trading-ul. Afiseaza erori clare cu sugestii de remediere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"ERRORS ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  ✘ {e}")
        if self.warnings:
            lines.append(f"WARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        if self.valid and not self.warnings:
            lines.append("✓ Configuration valid.")
        return "\n".join(lines)


class ConfigValidator:
    """
    Valideaza variabilele de mediu si parametrii de config.

    Usage::

        validator = ConfigValidator(exchange="bybit", mode="live")
        result = validator.validate()
        if not result.valid:
            print(result.summary())
            sys.exit(1)
    """

    REQUIRED_LIVE_BYBIT = [
        ("BYBIT_API_KEY", "Bybit API key pentru live trading"),
        ("BYBIT_API_SECRET", "Bybit API secret pentru live trading"),
    ]
    REQUIRED_BINANCE = [
        ("BINANCE_API_KEY", "Binance API key"),
        ("BINANCE_API_SECRET", "Binance API secret"),
    ]
    OPTIONAL_NOTIFICATIONS = [
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DISCORD_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "EMAIL_USER",
    ]

    def __init__(
        self,
        exchange: str = "bybit",
        mode: str = "paper",
        capital_usdt: float = 1000.0,
        max_drawdown_pct: float = 0.20,
    ) -> None:
        self._exchange = exchange.lower()
        self._mode = mode.lower()
        self._capital = capital_usdt
        self._max_dd = max_drawdown_pct

    def validate(self) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

        if self._mode == "live":
            reqs = (
                self.REQUIRED_LIVE_BYBIT
                if self._exchange == "bybit"
                else self.REQUIRED_BINANCE
            )
            for key, desc in reqs:
                val = os.getenv(key, "")
                if not val:
                    errors.append(f"{key} nu este setat — {desc}")
                elif len(val) < 10:
                    errors.append(f"{key} pare invalid (prea scurt)")

        if self._capital < 100:
            errors.append(f"capital_usdt={self._capital} prea mic (minim recomandat: 100 USDT)")
        elif self._capital < 500:
            warnings.append(f"capital_usdt={self._capital} este mic, risc ridicat de slippage relativ")

        if self._max_dd <= 0 or self._max_dd > 1:
            errors.append(f"max_drawdown_pct={self._max_dd} invalid (trebuie 0 < valoare <= 1)")
        elif self._max_dd > 0.30:
            warnings.append(f"max_drawdown_pct={self._max_dd * 100:.0f}% este mare, risc ridicat")

        notif_count = sum(1 for k in self.OPTIONAL_NOTIFICATIONS if os.getenv(k, ""))
        if notif_count == 0:
            warnings.append("Niciun canal de notificare configurat (Telegram/Discord/Slack/Email)")

        dry_run = os.getenv("DRY_RUN", "true").lower()
        if self._mode == "live" and dry_run in ("true", "1", "yes"):
            warnings.append("DRY_RUN=true dar mode=live — comenzile NU vor fi trimise la exchange")

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

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

    Pentru validare completa cu parametrii de trading::

        result = validator.validate_trading_params(
            entry_zscore=cfg.entry_zscore,
            exit_zscore=cfg.exit_zscore,
            base_qty=cfg.base_qty,
            warmup_bars=cfg.warmup_bars,
            kalman_window=cfg.kalman_window,
            max_drawdown_pct=cfg.max_drawdown_pct,
        )
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

    def validate_trading_params(
        self,
        entry_zscore: float,
        exit_zscore: float,
        base_qty: float,
        warmup_bars: int,
        kalman_window: int,
        max_drawdown_pct: float,
    ) -> ValidationResult:
        """Valideaza parametrii de trading din BybitLiveRunnerConfig.

        Aceasta metoda completeaza validarea din __post_init__ cu warnings
        suplimentare pentru valori legale dar riscante, si poate fi apelata
        independent pentru logging in main.py.

        Args:
            entry_zscore: Pragul de intrare in pozitie.
            exit_zscore: Pragul de iesire din pozitie.
            base_qty: Cantitatea de baza per ordin.
            warmup_bars: Numarul minim de bare pentru Kalman warmup.
            kalman_window: Fereastra rolling pentru Kalman filter.
            max_drawdown_pct: Drawdown maxim permis (in procente, ex: 10.0).

        Returns:
            ValidationResult cu erori si warnings.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # Warnings pentru valori legale dar riscante
        if entry_zscore < 1.0:
            warnings.append(
                f"ENTRY_ZSCORE={entry_zscore} este mic (< 1.0) — "
                "semnale prea frecvente, risc over-trading."
            )
        if entry_zscore > 4.0:
            warnings.append(
                f"ENTRY_ZSCORE={entry_zscore} este mare (> 4.0) — "
                "putine semnale, poate rata oportunitati."
            )
        if exit_zscore > 1.5:
            warnings.append(
                f"EXIT_ZSCORE={exit_zscore} este mare (> 1.5) — "
                "pozitiile se tin mult pana se inchid."
            )
        if warmup_bars < 50:
            warnings.append(
                f"WARMUP_BARS={warmup_bars} mic (< 50) — "
                "Kalman filter mai putin stabil in primele bare."
            )
        if kalman_window > 500:
            warnings.append(
                f"KALMAN_WINDOW={kalman_window} mare (> 500) — "
                "reactie lenta la schimbari de regim."
            )
        if max_drawdown_pct > 30:
            warnings.append(
                f"MAX_DRAWDOWN_PCT={max_drawdown_pct}% este mare (> 30%) — risc ridicat."
            )
        if base_qty > 1.0:
            warnings.append(
                f"BASE_QTY={base_qty} este mare (> 1.0) — "
                "verificati ca aveti lichiditate suficienta."
            )

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

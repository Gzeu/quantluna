"""
config/prod_guard.py  —  QuantLuna Production Guard

Importat la startup în main.py şi live_trader.py.
Blochează execuția live dacă:
  - QUANTLUNA_ENV != 'production' şi DRY_RUN=false
  - Capital sub MIN_CAPITAL_FLOOR_USDT
  - EMERGENCY_CLOSE_ALL=true
  - MAX_LEVERAGE > 10

Usage:
    from config.prod_guard import assert_production_safe
    assert_production_safe()  # ridică RuntimeError dacă ceva e greşit
"""
from __future__ import annotations

import os
from typing import Optional


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


class ProductionGuardError(RuntimeError):
    """Ridicată când configurația e nesigură pentru mainnet."""
    pass


def assert_production_safe(
    capital_usdt: Optional[float] = None,
    dry_run_override: Optional[bool] = None,
) -> None:
    """
    Verifică gărzile de siguranță la runtime.
    Apelat la fiecare startup al LiveTrader.

    Args:
        capital_usdt: capitalul efectiv folosit (override env)
        dry_run_override: dacă e setat, suprascrie DRY_RUN din env

    Ridică:
        ProductionGuardError cu mesaj descriptiv dacă vreun guard e decłanșat.
    """
    errors: list[str] = []

    ql_env  = _env("QUANTLUNA_ENV", "development")
    dry_run = dry_run_override if dry_run_override is not None else _env_bool("DRY_RUN", True)

    # Guard 1: env consistency
    if not dry_run and ql_env != "production":
        errors.append(
            f"DRY_RUN=false dar QUANTLUNA_ENV='{ql_env}' (aşteptat 'production'). "
            "Setează QUANTLUNA_ENV=production explicit."
        )

    # Guard 2: capital floor
    cap = capital_usdt if capital_usdt is not None else _env_float("CAPITAL_USDT", 200.0)
    min_floor = _env_float("MIN_CAPITAL_FLOOR_USDT", 50.0)
    if cap < min_floor:
        errors.append(
            f"Capital {cap:.2f} USDT < MIN_CAPITAL_FLOOR_USDT {min_floor:.2f} USDT. "
            "Bot haltat automat — risc de cont golit."
        )

    # Guard 3: emergency close-all sanity check at startup
    if _env_bool("EMERGENCY_CLOSE_ALL"):
        # Nu e un error — e intentional; dar logăm clar
        raise ProductionGuardError(
            "EMERGENCY_CLOSE_ALL=true — botul va închide toate pozițiile și se va opri. "
            "Dacă NU vrei asta, setează EMERGENCY_CLOSE_ALL=false și restartează."
        )

    # Guard 4: leverage hard cap
    max_lev = _env_float("MAX_LEVERAGE", 2.0)
    if max_lev > 10.0:
        errors.append(
            f"MAX_LEVERAGE={max_lev}x depăşeşte hard cap 10x. "
            "Reduceți în .env înainte de start."
        )

    # Guard 5: capital ceiling
    max_cap = _env_float("MAX_CAPITAL_USDT", 500.0)
    if cap > max_cap:
        errors.append(
            f"Capital {cap:.2f} > MAX_CAPITAL_USDT {max_cap:.2f}. "
            "Ajustează MAX_CAPITAL_USDT sau reduceți capitalul."
        )

    if errors:
        msg = "ProductionGuard — {} erori critice:\n".format(len(errors))
        for i, e in enumerate(errors, 1):
            msg += f"  [{i}] {e}\n"
        raise ProductionGuardError(msg.strip())


def get_effective_capital(balance_usdt: float) -> float:
    """
    Returnează capitalul efectiv de folosit, respectând toate plafoanele.

    Logic:
        effective = min(CAPITAL_USDT, balance_usdt * 0.95)  # 5% buffer
        effective = min(effective, MAX_CAPITAL_USDT)
        effective = max(effective, MIN_CAPITAL_FLOOR_USDT)  # dacă sub floor → halt

    Raises:
        ProductionGuardError dacă balance insuficient.
    """
    capital_target = _env_float("CAPITAL_USDT", 200.0)
    max_capital    = _env_float("MAX_CAPITAL_USDT", 500.0)
    min_floor      = _env_float("MIN_CAPITAL_FLOOR_USDT", 50.0)

    available = balance_usdt * 0.95  # 5% buffer pentru fees
    effective = min(capital_target, available, max_capital)

    if effective < min_floor:
        raise ProductionGuardError(
            f"Capital efectiv {effective:.2f} USDT < floor {min_floor:.2f} USDT. "
            f"(Balance: {balance_usdt:.2f}, target: {capital_target:.2f}). "
            "Bot haltat — reîncarcă contul sau scădeți MIN_CAPITAL_FLOOR_USDT."
        )

    return effective

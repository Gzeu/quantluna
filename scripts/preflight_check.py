"""
scripts/preflight_check.py  —  QuantLuna Production Pre-flight Check

Verifică toate condițiile necesare înainte de a porni pe mainnet.
Rulează: python scripts/preflight_check.py

Checks:
  1.  .env îincărcat şi variabilele critice prezente
  2.  QUANTLUNA_ENV == 'production'
  3.  BINANCE_API_KEY / SECRET ne-goale
  4.  Capital încădrat în [MIN_CAPITAL_FLOOR, MAX_CAPITAL]
  5.  MAX_LEVERAGE ≤10 (hard safety)
  6.  MAX_DRAWDOWN_HALT_PCT ≤0.20 (nu mai mult de 20%)
  7.  Conectivitate Binance Futures (ping)
  8.  Balance USDT ≥ CAPITAL_USDT
  9.  Pereche sym_y/sym_x există pe exchange (symbol valid)
  10. DRY_RUN flag explicit setat
  11. TELEGRAM config prezent dacă NOTIFY_ON_HALT=true
  12. quantluna_jobs.db accesibil (SQLite write test)
  13. Log dir există şi e writable
  14. EMERGENCY_CLOSE_ALL != true (ar închide tot la start)

Ieșire:
  Exit 0 = toate checks OK, safe to start
  Exit 1 = cel puțin un check critic a eșuat
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional — env vars pot fi setate direct

# ============================================================
# Helpers
# ============================================================

PASS  = "\033[92m✓\033[0m"
FAIL  = "\033[91m✗\033[0m"
WARN  = "\033[93m⚠\033[0m"

failed_checks: list[str] = []
warning_checks: list[str] = []


def check(name: str, condition: bool, fatal: bool = True, hint: str = "") -> bool:
    if condition:
        print(f"  {PASS}  {name}")
    else:
        icon = FAIL if fatal else WARN
        print(f"  {icon}  {name}" + (f" — {hint}" if hint else ""))
        if fatal:
            failed_checks.append(name)
        else:
            warning_checks.append(name)
    return condition


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(env(key, str(default)))
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, str(default)).lower() in ("true", "1", "yes")


# ============================================================
# Checks
# ============================================================

print("\n🚀  QuantLuna Pre-flight Check — Mainnet Production")
print("=" * 52)

# 1. QUANTLUNA_ENV
print("\n[1] Environment")
ql_env = env("QUANTLUNA_ENV")
check("QUANTLUNA_ENV=production", ql_env == "production",
      hint=f"current: '{ql_env}'")

# 2. BINANCE keys
print("\n[2] Binance API Keys")
api_key    = env("BINANCE_API_KEY")
api_secret = env("BINANCE_API_SECRET")
check("BINANCE_API_KEY present",    bool(api_key)    and api_key    != "<FILL_ME>",
      hint="set in .env")
check("BINANCE_API_SECRET present", bool(api_secret) and api_secret != "<FILL_ME>",
      hint="set in .env")
check("BINANCE_TESTNET=false",
      env("BINANCE_TESTNET", "false").lower() == "false",
      fatal=False, hint="you are on testnet — ok for rehearsal but not mainnet")

# 3. Capital validation
print("\n[3] Capital")
capital     = env_float("CAPITAL_USDT",          200.0)
max_capital = env_float("MAX_CAPITAL_USDT",       500.0)
min_floor   = env_float("MIN_CAPITAL_FLOOR_USDT",  50.0)
check("CAPITAL_USDT ≥ 100 USDT", capital >= 100.0,
      hint=f"current: {capital} USDT (sub minim, fees vor mânca PnL)")
check("CAPITAL_USDT ≤ MAX_CAPITAL_USDT", capital <= max_capital,
      hint=f"{capital} > {max_capital}")
check("MIN_CAPITAL_FLOOR_USDT ≥ 30", min_floor >= 30.0,
      hint="floor prea mic — risc de cont golit complet")

# 4. Risk parameters
print("\n[4] Risk Parameters")
max_lev     = env_float("MAX_LEVERAGE",        2.0)
kelly       = env_float("KELLY_FRACTION",      0.15)
vol_target  = env_float("VOL_TARGET",          0.008)
dd_halt     = env_float("MAX_DRAWDOWN_HALT_PCT", 0.08)
pos_pct     = env_float("MAX_POSITION_PCT",    0.30)

check("MAX_LEVERAGE ≤ 3.0 (recomandat pentru început)",
      max_lev <= 3.0, fatal=False,
      hint=f"current: {max_lev}x — risc ridicat")
check("MAX_LEVERAGE ≤ 10.0 (hard safety)",
      max_lev <= 10.0,
      hint=f"current: {max_lev}x — PERICULOS")
check("KELLY_FRACTION ≤ 0.25",
      kelly <= 0.25, fatal=False,
      hint=f"current: {kelly} — Kelly prea agresiv")
check("VOL_TARGET ≤ 0.02 (2% zilnic)",
      vol_target <= 0.02, fatal=False,
      hint=f"current: {vol_target}")
check("MAX_DRAWDOWN_HALT_PCT ≤ 0.20",
      dd_halt <= 0.20,
      hint=f"current: {dd_halt} — halt prea târziu")
check("MAX_POSITION_PCT ≤ 0.50",
      pos_pct <= 0.50,
      hint=f"current: {pos_pct}")

# 5. DRY_RUN
print("\n[5] Execution Mode")
dry_run = env_bool("DRY_RUN", True)
check("DRY_RUN este explicit setat", env("DRY_RUN") != "",
      hint="DRY_RUN trebuie setat explicit la false pentru live")
# Warn dacă DRY_RUN=true — nu e fatal, dar useful
if dry_run:
    check("DRY_RUN=false (live mode)", False, fatal=False,
          hint="Bot rulează în paper mode — ok pentru test")
else:
    check("DRY_RUN=false (live mode)", True)

# 6. Kill-switch guard
print("\n[6] Kill-switch")
check("EMERGENCY_CLOSE_ALL != true",
      not env_bool("EMERGENCY_CLOSE_ALL"),
      hint="EMERGENCY_CLOSE_ALL=true ar închide toate pozițiile la start!")

# 7. Telegram notifications
print("\n[7] Notifications")
not_halt = env_bool("NOTIFY_ON_HALT", True)
tg_token = env("TELEGRAM_BOT_TOKEN")
tg_chat  = env("TELEGRAM_CHAT_ID")
if not_halt:
    check("TELEGRAM_BOT_TOKEN prezent",
          bool(tg_token) and tg_token != "<FILL_ME>",
          fatal=False, hint="halts nu vor fi notificate")
    check("TELEGRAM_CHAT_ID prezent",
          bool(tg_chat),
          fatal=False, hint="halts nu vor fi notificate")
else:
    print(f"  {WARN}  NOTIFY_ON_HALT=false — recomandat să activezi alertele")
    warning_checks.append("NOTIFY_ON_HALT=false")

# 8. Binance connectivity (optional — necesită ccxt)
print("\n[8] Binance Connectivity")
try:
    import ccxt
    exchange = ccxt.binanceusdm({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "future"},
    })
    # Ping — nu necesită autentificare
    exchange.load_markets()
    check("Binance Futures markets loaded", True)

    # Balance check
    try:
        balance = exchange.fetch_balance({"type": "future"})
        usdt_free = float(balance.get("USDT", {}).get("free", 0))
        check(f"Balance USDT ({usdt_free:.2f}) ≥ CAPITAL ({capital:.2f})",
              usdt_free >= capital,
              hint=f"balance insuficient: {usdt_free:.2f} USDT")
    except ccxt.AuthenticationError:
        check("Balance fetch (API auth)", False,
              hint="API key invalid sau IP nu e în whitelist")
    except Exception as e:
        check("Balance fetch", False, fatal=False, hint=str(e))

    # Symbol validation
    sym_y = env("SYM_Y", "BTCUSDT")
    sym_x = env("SYM_X", "ETHUSDT")
    markets = exchange.markets
    sym_y_ok = sym_y in markets or sym_y.replace("USDT", "/USDT:USDT") in markets
    sym_x_ok = sym_x in markets or sym_x.replace("USDT", "/USDT:USDT") in markets
    check(f"Symbol {sym_y} valid pe Binance Futures", sym_y_ok,
          hint="verifică SYM_Y în .env")
    check(f"Symbol {sym_x} valid pe Binance Futures", sym_x_ok,
          hint="verifică SYM_X în .env")

except ImportError:
    print(f"  {WARN}  ccxt not installed — skip connectivity checks")
    warning_checks.append("ccxt not installed")
except Exception as e:
    check("Binance connectivity", False, fatal=False, hint=str(e))

# 9. SQLite DB writable
print("\n[9] Persistence")
try:
    db_path = Path("quantluna_jobs.db")
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS _preflight_test (x INTEGER)")
    conn.execute("DROP TABLE _preflight_test")
    conn.close()
    check("quantluna_jobs.db writable", True)
except Exception as e:
    check("quantluna_jobs.db writable", False, hint=str(e))

# 10. Log dir
print("\n[10] Logging")
log_file = Path(env("LOG_FILE", "logs/quantluna.log"))
log_dir  = log_file.parent
try:
    log_dir.mkdir(parents=True, exist_ok=True)
    test_file = log_dir / ".write_test"
    test_file.write_text("ok")
    test_file.unlink()
    check(f"Log dir '{log_dir}' writable", True)
except Exception as e:
    check(f"Log dir '{log_dir}' writable", False, hint=str(e))

# ============================================================
# Summary
# ============================================================

print("\n" + "=" * 52)
if not failed_checks and not warning_checks:
    print(f"{PASS}  Toate checks OK — SAFE TO START MAINNET \U0001f7e2")
    sys.exit(0)
elif not failed_checks:
    print(f"{WARN}  {len(warning_checks)} warning(s) — revizuieşte înainte de start:")
    for w in warning_checks:
        print(f"     • {w}")
    print("\n  Bot poate porni, dar rezolvă warning-urile.")
    sys.exit(0)
else:
    print(f"{FAIL}  {len(failed_checks)} check(s) CRITIC(E) eşuate:")
    for f in failed_checks:
        print(f"     • {f}")
    if warning_checks:
        print(f"\n{WARN}  + {len(warning_checks)} warning(s)")
    print("\n  NU porni botul până nu rezolvi toate erorile critice.")
    sys.exit(1)

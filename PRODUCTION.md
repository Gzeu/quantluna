# QuantLuna — Production Readiness Guide

> **⚠' MAINNET REAL MONEY** — citește tot înainte de a seta `QUANTLUNA_ENV=production`.

## Checklist pre-launch (minimal capital)

### 1. Infrastructură
- [ ] Server dedicat (VPS/cloud) cu uptime SLA ≥99.5% — NU laptop personal
- [ ] Timezone server = UTC (`timedatectl set-timezone UTC`)
- [ ] Docker + docker-compose instalat (`docker compose version`)
- [ ] Portul 8000 (API) NU e expus public — reverse proxy cu auth sau VPN
- [ ] `.env` cu permisiuni 600 (`chmod 600 .env`)
- [ ] `quantluna_jobs.db` pe volum persistent (nu în container ephemeral)

### 2. Binance API keys
- [ ] API key creat pe cont Binance real (Futures enabled)
- [ ] **Permissions setate: Futures Trading ONLY** — NU Withdrawal, NU Spot
- [ ] IP whitelist activat pe API key (IP-ul serverului tău)
- [ ] Keys în `.env`, NU în cod sau git
- [ ] Test conectivitate: `python scripts/check_connectivity.py`

### 3. Capital minim recomandat
| Pereche | Capital minim | Notă |
|---------|--------------|------|
| BTC/ETH | 200 USDT | sub notional minim Binance riscă reject |
| SOL/BNB | 150 USDT | |
| orice pereche | 100 USDT | limită absolută — sub asta fees mănâncă PnL |

> Capital recomandat pentru prima săptămână: **200–500 USDT**.
> Setează `MAX_CAPITAL_USDT=500` în `.env` ca hard ceiling.

### 4. Risk Guards — valori obligatorii pentru mainnet minimal
```env
# .env pentru mainnet minimal capital
QUANTLUNA_ENV=production
DRY_RUN=false

# Capital
CAPITAL_USDT=200
MAX_CAPITAL_USDT=500
MIN_CAPITAL_FLOOR_USDT=50    # halt automat dacă balanța scade sub 50 USDT

# Risk
MAX_LEVERAGE=2.0             # NICIODATĂ mai mare de 3x pentru început
KELLY_FRACTION=0.15          # 15% Kelly — conservator
VOL_TARGET=0.008             # 0.8% volatility target zilnic
MAX_DRAWDOWN_HALT_PCT=0.08   # halt la -8% drawdown pe zi
MAX_POSITION_PCT=0.30        # max 30% capital pe o singură poziție

# Execution
SLIPPAGE_PCT=0.0008          # 0.08% slippage estimate mainnet
FEE_RATE=0.00045             # 0.045% taker fee Binance Futures

# Kill-switch
EMERGENCY_CLOSE_ALL=false    # setează true și restart pentru a închide tot
```

### 5. Validare pre-flight
```bash
# 1. Verifică conectivitate și balance
python scripts/preflight_check.py

# 2. Rulează 1 oră în paper mode pe mainnet data (feed real, orders simulate)
QUANTLUNA_ENV=production DRY_RUN=true python main.py --mode paper --duration 3600

# 3. Verifică logs — nu trebuie erori CCXT sau WebSocket drops
tail -f logs/quantluna.log | grep -E '(ERROR|WARNING|HALT)'

# 4. Pornește live cu capital minim
QUANTLUNA_ENV=production DRY_RUN=false python main.py --mode live
```

### 6. Monitoring obligatoriu
- [ ] Telegram alerts configurate (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` în `.env`)
- [ ] Health check endpoint activ: `GET /health` returnează 200
- [ ] Cron job de watchdog: `*/5 * * * * curl -f http://localhost:8000/health || systemctl restart quantluna`
- [ ] Backup zilnic `quantluna_jobs.db`: `0 3 * * * cp quantluna_jobs.db backups/jobs_$(date +%Y%m%d).db`

### 7. Procedură de urgency
```bash
# STOP IMEDIAT toate pozițiile deschise:
curl -X POST http://localhost:8000/api/emergency/close-all

# sau direct din .env:
# EMERGENCY_CLOSE_ALL=true + restart container
```

## Ce NU face QuantLuna pe mainnet minimal
- NU folositți perechi cu spread mare (ex: LUNA, meme coins)
- NU porniți fără cel puțin 48h paper trading pe mainnet data
- NU lăsați leverage > 3x pentru primul luna
- NU ignorați alertele Telegram de HALT
- NU ștergeți `quantluna_jobs.db` — conține istoricul joburilor active

## Upgrade path după prima lună
1. Dacă Sharpe > 1.0 pe 30 de zile live → poți mări `CAPITAL_USDT` cu 50%
2. Dacă max drawdown live < 5% → poți ridica `KELLY_FRACTION` la 0.20
3. Dacă n_trades > 100 fără erori de execuție → consideră a doua pereche

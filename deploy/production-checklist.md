# QuantLuna — Production Deployment Checklist

Completeaza **toate** checkboxurile inainte de a porni live trading cu bani reali.

---

## 1. Pregatire Server

- [ ] Server Linux (Ubuntu 22.04+ recomandat), minim 2 vCPU / 4 GB RAM
- [ ] Docker `>= 24.x` si Docker Compose `>= 2.x` instalate
- [ ] Timezone setat corect (`timedatectl set-timezone UTC`)
- [ ] NTP activ (`systemctl status systemd-timesyncd`)
- [ ] Firewall: porturi 8000 (dashboard) si 8081 (health) expuse **doar** catre IP-uri de incredere

## 2. Credentiale si Secrets

- [ ] `cp .env.example .env` pe server
- [ ] `BYBIT_API_KEY` si `BYBIT_API_SECRET` completate cu chei **read + trade** (fara withdraw)
- [ ] `BYBIT_TESTNET=false` confirmat
- [ ] `DRY_RUN=false` confirmat (dupa cel putin 48h paper trading reusit)
- [ ] Notificari Telegram configurate (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
- [ ] `.env` are permisiuni restrictive: `chmod 600 .env`
- [ ] `.env` nu este in git: `grep '\.env$' .gitignore` confirmat

## 3. Validare Configuratie

- [ ] `SYMBOL_Y` si `SYMBOL_X` confirmate cointegrate (backtest recent cu p-value < 0.05)
- [ ] `ENTRY_ZSCORE` si `EXIT_ZSCORE` calibrate din optimizare (`best_params.json`)
- [ ] `BASE_QTY` calculat relativ la capital disponibil (max 2% risc per trade recomandat)
- [ ] `MAX_DRAWDOWN_PCT` setat la max 10% (circuit breaker)
- [ ] `MAX_CONSEC_LOSSES` setat la max 3
- [ ] `WARMUP_BARS` >= 100 (200 recomandat)
- [ ] `REST_WARMUP_ENABLED=true` (asigura warm-up rapid la restart)
- [ ] `INTERVAL` corespunde timeframe-ului din backtest

## 4. Testare Pre-Deploy

- [ ] `docker compose --profile paper up --build` ruleaza fara erori
- [ ] Paper trader porneste si logeaza `First WS bar` cu price_y si price_x nenule
- [ ] Health endpoint raspunde: `curl http://localhost:8081/api/health` returneaza 200
- [ ] Dashboard accesibil: `curl http://localhost:8000/health` returneaza 200
- [ ] Circuit breaker functioneaza (test manual: seteaza zscore mare artificial)
- [ ] Notificare Telegram primita la start (`⚡ QuantLuna Started`)
- [ ] Paper trading minim **48 de ore** fara crash-uri
- [ ] `docker compose --profile paper logs` fara erori repetate

## 5. Monitorizare Live

- [ ] Logs in timp real: `docker compose --profile live logs -f quantluna-live`
- [ ] Health check activ: `watch -n 10 "curl -s http://localhost:8081/api/health"`
- [ ] Alerte Telegram configurate pentru:
  - [ ] Entry / Exit trade
  - [ ] Circuit breaker OPEN
  - [ ] Erori WS reconectare
- [ ] Backup state zilnic: `state/position_checkpoint.db`
- [ ] Log rotation configurata (`logs/` max 30 zile)
- [ ] `docker stats quantluna-live` — memory usage stabil

## 6. Procedura de Start Live

```bash
# 1. Verifica .env
cat .env | grep -E 'DRY_RUN|BYBIT_TESTNET|SYMBOL|INTERVAL'

# 2. Build si pornire
docker compose --profile live up -d --build

# 3. Urmareste logurile (primele 5 minute sunt critice)
docker compose --profile live logs -f quantluna-live

# 4. Verifica health
curl http://localhost:8081/api/health

# 5. Confirma primul bar WS (price_y si price_x nenule)
docker compose --profile live logs quantluna-live | grep 'First WS bar'
```

## 7. Oprire Gratiosa (Emergency Stop)

```bash
# Oprire normala — asteapta stop_grace_period=30s pentru inchiderea pozitiilor
docker compose --profile live stop

# Oprire imediata (risc: pozitii pot ramane deschise!)
docker compose --profile live kill

# Verifica pozitii ramase deschise pe Bybit
# -> https://www.bybit.com/unified/position
```

## 8. Post-Deploy (dupa 24h)

- [ ] Review loguri pentru erori sau warnings repetate
- [ ] Verifica PnL reconciler: `state/` are checkpoint actualizat
- [ ] Verifica ca WS reconecteaza corect dupa pierdere conexiune
- [ ] Memory usage stabil: `docker stats quantluna-live`
- [ ] Verifica ca `restart: always` a functionat dupa reboot server (daca a fost)

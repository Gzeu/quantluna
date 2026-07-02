# QuantLuna Dashboard

Next.js 14 + React 18 + TypeScript 5 trading dashboard pentru QuantLuna pairs trading engine.

## Stack

- **Next.js 14.2.5** — App Router, SSR disabled pe componente client
- **Tailwind CSS 3.4.6** — custom neon palette (bg/neon/alert/text)
- **Recharts 2.12.7** — LineChart, AreaChart, Treemap
- **Zustand 4.5.4** — global trading state store
- **Framer Motion 11.3.0** — AnimatePresence pe ArbitragePanel rows
- **Lightweight Charts 4.2.0** — placeholder pentru viitor candlestick panel
- **date-fns 3.6.0** — timestamp formatting
- **lucide-react 0.400** — icons

## Pornire rapidă

```bash
cd dashboard
npm install
npm run dev
# → http://localhost:3000
```

Backend FastAPI (opțional):
```bash
pip install fastapi uvicorn
uvicorn dashboard.server:app --reload --port 8000
```

Dacă serverul nu e pornit, toate componentele funcționează cu **mock data** (fallback graceful, fără crash).

## Variabile de mediu

```env
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/feed
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## New Components

### `dashboard/types.ts`
Toate interfețele TypeScript exportate centralizat. Fără `any` (strict mode).
Include: `WsMessage`, `TradingState`, `PairState`, `MarketData`, `LogEntry`, `ArbOpportunity`, `DailyPnlPoint`, enums de tip (`VolatilityRegime`, `LogLevel`, `SpreadHealth`, `PositionSide`, `WsStatus`).

### `dashboard/lib/formatters.ts`
Funcții pure de formatare:
- `formatPrice(value, decimals?)` → `"$43,210.57"`
- `formatPnl(value)` → `{ text: "+$142.50", positive: true }`
- `formatPercent(value, decimals?, showSign?)` → `"+3.12%"`
- `formatVolume(value)` → `"1.23M"`
- `formatDuration(seconds)` → `"1h 2m 5s"`

### `dashboard/hooks/useWebSocket.ts`
Hook generic cu reconectare exponential backoff (1s → 2s → 4s → max 30s), heartbeat ping/pong la 15s. Expune `{ lastMessage, status, send, reconnect }`. URL configurat din `NEXT_PUBLIC_WS_URL`.

### `dashboard/hooks/useTradingStore.ts`
Zustand store global cu mock data offline. Acțiuni: `updateFromWsFeed(msg)` dispatch pe tip (`balance`, `pairs`, `markets`, `regime`, `ws_status`, `log`, `arb`), `addLogEntry`, `clearLog`.

### `dashboard/components/BalanceTracker.tsx`
Panel cu:
- Cifra totală animată count-up (rAF cubic ease)
- Color flash verde/roșu pe schimbare uPnL (300ms)
- Sparkline Recharts LineChart equity curve (50 puncte)
- 3 rânduri: Available / Margin Used / uPnL
- Badge Daily PnL cu procent față de start-of-day

### `dashboard/components/ArbitragePanel.tsx`
Tabel live oportunități arbitraj cu:
- Sort automat descendent după `spreadPct`
- Background magenta la `spreadPct > 0.03%`
- TTL countdown per rând cu roșu + pulse sub 5s
- Buton `▶ TRADE` cu toast DOM (fără dependențe)
- Animație slide-in pe rând nou (Framer Motion `AnimatePresence`)
- Empty state cu mesaj gri

### `dashboard/components/ExecutionLog.tsx`
Log performant CSS-only scroll cu:
- Max 1000 entries (rotație FIFO în store)
- Format `[HH:MM:SS.mmm] [LEVEL] [MODULE] message`
- Culori per nivel (`INFO`=muted, `BUY`=verde, `SELL`=roșu, `WARN`=amber, etc.)
- Filter bar: checkboxes per level + search realtime
- Highlight automat `CIRCUIT_BREAKER` / `ORPHAN` → `bg-red/20`
- Auto-scroll toggle + detecție scroll manual
- Export CSV (Ctrl+E sau buton)

### `dashboard/components/SpreadMonitorPanel.tsx`
Visualizare spread/z-score cu:
- Z-score mare (`text-5xl font-mono`) cu culori dinamice: gri → albastru → galben → roșu pulsator
- AreaChart Recharts cu gradient shading și threshold lines la ±0.5 / ±2.0
- Health badge (`HEALTHY` / `DEGRADED` / `STALE`)
- Half-life display cu tooltip
- Pair selector dropdown

### `dashboard/components/MarketHeatmap.tsx`
Recharts Treemap 50 simboluri cu:
- Interpolate color roșu→negru→verde pe baza `change24h`
- CustomContent per celulă (simbol + %) cu `font-mono`
- Tooltip custom cu preț, volum formatat (K/M/B), funding rate
- Toggle `by Change%` ↔ `by Volume` pentru sizing celulă

### `dashboard/components/RegimeHeader.tsx`
Header fix 44px cu:
- Logo `⟁ QUANTLUNA v0.30` cu `glow-green`
- Regime badge pill dinamic (LOW/NORMAL/HIGH/EXTREME, EXTREME=animate-pulse)
- Circuit Breaker: `CB OPEN` + countdown sau `CB ✓`
- 3 WS orbs (Bybit / Binance / OKX) cu dot verde/roșu
- UTC clock (actualizat la 1s)
- Paper/Live toggle cu confirm dialog + localStorage
- Keyboard hints (`Ctrl+P Pause | Ctrl+E Export | Esc Dismiss`)

### `dashboard/app/page.tsx`
Layout master CSS Grid:
```
"header header header"
"sidebar center  right"
"sidebar log     log"
```
Coloane: 240px / 1fr / 340px — Rânduri: 44px / 1fr / 200px.
Initializează WebSocket o singură dată, hidratează store din REST la mount, shortcuts globale Ctrl+1..5 / Ctrl+P / Ctrl+E.

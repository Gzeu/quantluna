import { create } from 'zustand'
import type {
  TradingState,
  WsMessage,
  LogEntry,
  PairState,
  MarketData,
  ArbOpportunity,
  DailyPnlPoint,
  VolatilityRegime,
  WsStatusMap,
} from '../types'

const MAX_LOG_ENTRIES = 1000
const MAX_DAILY_PNL_POINTS = 288

// ── Store — zero initial state, all data comes from live API ─────────────────
export const useTradingStore = create<TradingState>((set, get) => ({
  // Balance — zeros until /api/balance responds
  totalBalance:     0,
  availableBalance: 0,
  unrealizedPnl:    0,
  realizedPnl:      0,
  dailyPnlHistory:  [] as DailyPnlPoint[],

  // Pairs / markets — empty until WS feed or REST hydration
  pairs:   [] as PairState[],
  markets: [] as MarketData[],

  volatilityRegime:       'NORMAL' as VolatilityRegime,
  circuitBreakerOpen:     false,
  circuitBreakerCooldown: 0,
  wsStatus: { bybit: false, binance: false, okx: false } as WsStatusMap,

  logEntries:       [] as LogEntry[],
  arbOpportunities: [] as ArbOpportunity[],

  // ── REST hydration — called once on mount from page.tsx ──────────────────
  hydrateFromRest: async () => {
    const base = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
    try {
      const [balRes, marketsRes, pairsRes, riskRes, logRes] = await Promise.allSettled([
        fetch(`${base}/api/balance`).then(r => r.json()),
        fetch(`${base}/api/markets`).then(r => r.json()),
        fetch(`${base}/api/pairs`).then(r => r.json()),
        fetch(`${base}/api/risk`).then(r => r.json()),
        fetch(`${base}/api/log`).then(r => r.json()),
      ])

      if (balRes.status === 'fulfilled' && balRes.value) {
        const b = balRes.value
        set({
          totalBalance:     b.totalBalance     ?? 0,
          availableBalance: b.availableBalance ?? 0,
          unrealizedPnl:    b.unrealizedPnl    ?? 0,
          realizedPnl:      b.realizedPnl      ?? 0,
        })
      }
      if (marketsRes.status === 'fulfilled' && Array.isArray(marketsRes.value) && marketsRes.value.length > 0) {
        set({ markets: marketsRes.value as MarketData[] })
      }
      if (pairsRes.status === 'fulfilled' && Array.isArray(pairsRes.value)) {
        set({ pairs: pairsRes.value as PairState[] })
      }
      if (riskRes.status === 'fulfilled' && riskRes.value) {
        const r = riskRes.value
        set({
          volatilityRegime:       r.regime       as VolatilityRegime,
          circuitBreakerOpen:     r.cb_open      ?? false,
          circuitBreakerCooldown: r.cb_cooldown  ?? 0,
        })
      }
      if (logRes.status === 'fulfilled' && Array.isArray(logRes.value)) {
        set({ logEntries: logRes.value as LogEntry[] })
      }
    } catch (e) {
      console.warn('[useTradingStore] REST hydration failed:', e)
    }
  },

  // ── WS feed handler ───────────────────────────────────────────────────────
  updateFromWsFeed: (msg: WsMessage) => {
    const { type, payload } = msg
    switch (type) {
      case 'balance': {
        const p = payload as Partial<TradingState>
        set({
          totalBalance:     p.totalBalance     ?? get().totalBalance,
          availableBalance: p.availableBalance ?? get().availableBalance,
          unrealizedPnl:    p.unrealizedPnl    ?? get().unrealizedPnl,
          realizedPnl:      p.realizedPnl      ?? get().realizedPnl,
        })
        const hist = [
          ...get().dailyPnlHistory,
          { ts: msg.ts, value: (p.unrealizedPnl ?? 0) + (p.realizedPnl ?? 0) },
        ]
        set({ dailyPnlHistory: hist.slice(-MAX_DAILY_PNL_POINTS) })
        break
      }
      case 'pairs':
        set({ pairs: payload as PairState[] })
        break
      case 'markets': {
        const m = payload as MarketData[]
        if (Array.isArray(m) && m.length > 0) set({ markets: m })
        break
      }
      case 'regime': {
        const p = payload as { regime: VolatilityRegime; cb_open: boolean; cb_cooldown: number }
        set({
          volatilityRegime:       p.regime      as VolatilityRegime,
          circuitBreakerOpen:     p.cb_open,
          circuitBreakerCooldown: p.cb_cooldown,
        })
        break
      }
      case 'ws_status':
        set({ wsStatus: payload as WsStatusMap })
        break
      case 'log': {
        const entries = Array.isArray(payload)
          ? (payload as LogEntry[])
          : [payload as LogEntry]
        const stamped = entries.map((e) => ({ ...e, ts: e.ts || msg.ts }))
        const merged = [...stamped, ...get().logEntries].slice(0, MAX_LOG_ENTRIES)
        set({ logEntries: merged })
        break
      }
      case 'arb':
        set({ arbOpportunities: payload as ArbOpportunity[] })
        break
      default:
        break
    }
  },

  addLogEntry: (entry: LogEntry) => {
    const entries = [entry, ...get().logEntries].slice(0, MAX_LOG_ENTRIES)
    set({ logEntries: entries })
  },

  clearLog: () => set({ logEntries: [] }),
}))

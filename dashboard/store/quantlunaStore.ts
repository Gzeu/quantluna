/**
 * QuantLuna Global State Store (Zustand) — S37 metrics expansion
 * Adăugat: TradeStats, wins/losses în PnLData, setTradeStats action.
 */
import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'
import type { RiskMetrics, PairBreakdown } from '../types/dashboard'

export type Regime      = 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME'
export type SpreadHealth = 'HEALTHY' | 'DEGRADED' | 'STALE'
export type LogLevel    = 'INFO' | 'BUY' | 'SELL' | 'WARN' | 'ARB' | 'ERROR' | 'RISK' | 'SYS'

export interface SpreadData {
  z: number; spread: number; halfLife: number
  kalmanP: number; health: SpreadHealth; timestamp: number
}

export interface RegimeData {
  regime: Regime; cbOpen: boolean; cbCountdown: number
  wsOk: boolean; bybitOk: boolean; binanceOk: boolean; okxOk: boolean
  latencyMs: number
}

export interface PnLData {
  total:        number
  available:    number
  margin:       number
  unrealized:   number
  dailyPnl:     number
  dailyPct:     number
  wins:         number
  losses:       number
  totalTrades:  number
  equityHistory: { t: number; v: number }[]
}

export interface TradeStats {
  wins:                   number
  losses:                 number
  total_trades:           number
  win_rate:               number
  avg_win_usd:            number
  avg_loss_usd:           number
  profit_factor:          number
  max_drawdown:           number
  max_consecutive_wins:   number
  max_consecutive_losses: number
  current_streak:         number
  pair_breakdown:         PairBreakdown[]
}

export interface PairData {
  pair: string; z: number; halfLife: number; spread: number
  spreadDelta: number; pnl: number; position: 'LONG' | 'SHORT' | 'FLAT'
}

export interface ArbOpportunity {
  id: string; pair: string; bybit: number; binance: number; okx?: number
  spreadPct: number; ttl: number; ttlMax: number; detectedAt: number
}

export interface MarketSymbol {
  symbol: string; price: number; change24h: number
  volume24h: number; funding: number
}

export interface LogEntry {
  id: number; ts: string; level: LogLevel; module: string; msg: string
}

export interface PnLPoint { t: number; v: number; pair: string }

interface QuantLunaState {
  spread:       SpreadData | null
  regime:       RegimeData | null
  pnl:          PnLData | null
  tradeStats:   TradeStats | null
  pairs:        PairData[]
  arb:          ArbOpportunity[]
  markets:      MarketSymbol[]
  logs:         LogEntry[]
  pnlSeries:    Record<string, PnLPoint[]>
  candleSymbol: string

  isLive:       boolean
  isPaused:     boolean
  activeModal:  string | null
  logFilters:   Set<LogLevel>
  logSearch:    string
  logAutoScroll: boolean

  setSpread:       (d: SpreadData)          => void
  setRegime:       (d: RegimeData)          => void
  setPnl:          (d: PnLData)             => void
  setTradeStats:   (d: TradeStats)          => void
  setPairs:        (p: PairData[])          => void
  setArb:          (a: ArbOpportunity[])    => void
  setMarkets:      (m: MarketSymbol[])      => void
  addLog:          (e: Omit<LogEntry,'id'>) => void
  pushPnlPoint:    (pair: string, v: number, t: number) => void
  setCandleSymbol: (s: string)              => void
  toggleLive:      ()                       => void
  togglePause:     ()                       => void
  setModal:        (m: string | null)       => void
  toggleLogFilter: (l: LogLevel)            => void
  setLogSearch:    (s: string)              => void
  setLogAutoScroll:(v: boolean)             => void
  clearLogs:       ()                       => void
}

let _logIdCounter = 0

export const useQuantLunaStore = create<QuantLunaState>()(
  subscribeWithSelector((set) => ({
    spread: null, regime: null, pnl: null, tradeStats: null,
    pairs: [], arb: [], markets: [], logs: [], pnlSeries: {},
    candleSymbol: 'BTC/USDT',
    isLive: false, isPaused: false, activeModal: null,
    logFilters: new Set(['INFO','BUY','SELL','WARN','ARB','ERROR','RISK','SYS']),
    logSearch: '', logAutoScroll: true,

    setSpread:     (d) => set({ spread: d }),
    setRegime:     (d) => set({ regime: d }),
    setPnl:        (d) => set({ pnl: d }),
    setTradeStats: (d) => set({ tradeStats: d }),
    setPairs:      (p) => set({ pairs: p }),
    setArb:        (a) => set({ arb: a }),
    setMarkets:    (m) => set({ markets: m }),

    addLog: (e) => set((s) => {
      const entry = { ...e, id: ++_logIdCounter }
      const logs = [...s.logs, entry]
      return { logs: logs.length > 5000 ? logs.slice(-4000) : logs }
    }),

    pushPnlPoint: (pair, v, t) => set((s) => {
      const existing = s.pnlSeries[pair] ?? []
      const next = [...existing, { t, v, pair }]
      return {
        pnlSeries: {
          ...s.pnlSeries,
          [pair]: next.length > 500 ? next.slice(-400) : next,
        }
      }
    }),

    setCandleSymbol:  (s) => set({ candleSymbol: s }),
    toggleLive:       ()  => set((s) => ({ isLive: !s.isLive })),
    togglePause:      ()  => set((s) => ({ isPaused: !s.isPaused })),
    setModal:         (m) => set({ activeModal: m }),
    toggleLogFilter:  (l) => set((s) => {
      const next = new Set(s.logFilters)
      next.has(l) ? next.delete(l) : next.add(l)
      return { logFilters: next }
    }),
    setLogSearch:     (s) => set({ logSearch: s }),
    setLogAutoScroll: (v) => set({ logAutoScroll: v }),
    clearLogs:        ()  => set({ logs: [] }),
  }))
)

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
const MAX_DAILY_PNL_POINTS = 288 // 24h × 5min intervals

// ── Mock data for standalone / server-offline mode ───────────────────────────
const MOCK_PAIRS: PairState[] = [
  { symbol: 'BTC/ETH', zscore: 1.82, spread: 0.0412, halfLife: 18.3, position: 'LONG', pnl: 142.5, spreadHealth: 'HEALTHY' },
  { symbol: 'SOL/AVAX', zscore: -2.31, spread: -0.0871, halfLife: 8.1, position: 'SHORT', pnl: -23.1, spreadHealth: 'DEGRADED' },
  { symbol: 'BNB/MATIC', zscore: 0.44, spread: 0.0089, halfLife: 24.0, position: 'FLAT', pnl: 0, spreadHealth: 'HEALTHY' },
]

const MOCK_MARKETS: MarketData[] = Array.from({ length: 20 }, (_, i) => ({
  symbol: ['BTC', 'ETH', 'SOL', 'BNB', 'AVAX', 'MATIC', 'DOT', 'ADA', 'LINK', 'UNI',
           'ATOM', 'NEAR', 'FTM', 'ALGO', 'XRP', 'LTC', 'DOGE', 'SHIB', 'ARB', 'OP'][i],
  price: Math.random() * 50000 + 100,
  change24h: (Math.random() - 0.5) * 10,
  volume24h: Math.random() * 1e9,
  fundingRate: (Math.random() - 0.5) * 0.002,
}))

const MOCK_LOG: LogEntry[] = [
  { ts: Date.now() - 3000, level: 'INFO', module: 'SignalGen', message: 'Kalman filter warmed up on BTC/ETH' },
  { ts: Date.now() - 2000, level: 'BUY',  module: 'Executor',  message: 'LONG_SPREAD BTC/ETH z=1.82 qty=0.05' },
  { ts: Date.now() - 1000, level: 'WARN', module: 'RiskMgr',   message: 'Volatility regime elevated → HIGH' },
  { ts: Date.now(),         level: 'ARB',  module: 'ArbScanner',message: 'Opportunity BTC Bybit/Binance spread=0.041%' },
]

const MOCK_ARB: ArbOpportunity[] = [
  { pair: 'BTC-USDT', bybitPrice: 43215.5, binancePrice: 43197.2, spreadPct: 0.042, detectedAt: Date.now(), ttlSeconds: 12 },
  { pair: 'ETH-USDT', bybitPrice: 2310.88, binancePrice: 2309.12, spreadPct: 0.076, detectedAt: Date.now(), ttlSeconds: 7 },
]

// ── Store ─────────────────────────────────────────────────────────────────────
export const useTradingStore = create<TradingState>((set, get) => ({
  // Balance — mock defaults
  totalBalance: 10_432.87,
  availableBalance: 7_215.44,
  unrealizedPnl: 119.43,
  realizedPnl: 312.00,
  dailyPnlHistory: Array.from({ length: 50 }, (_, i) => ({
    ts: Date.now() - (50 - i) * 60_000,
    value: 200 + Math.sin(i / 5) * 80 + i * 3,
  })) as DailyPnlPoint[],

  pairs: MOCK_PAIRS,
  markets: MOCK_MARKETS,

  volatilityRegime: 'NORMAL' as VolatilityRegime,
  circuitBreakerOpen: false,
  circuitBreakerCooldown: 0,
  wsStatus: { bybit: false, binance: false, okx: false } as WsStatusMap,

  logEntries: MOCK_LOG,
  arbOpportunities: MOCK_ARB,

  // ── Actions ──────────────────────────────────────────────────────────────
  updateFromWsFeed: (msg: WsMessage) => {
    const { type, payload } = msg
    switch (type) {
      case 'balance': {
        const p = payload as Partial<TradingState>
        set({
          totalBalance: p.totalBalance ?? get().totalBalance,
          availableBalance: p.availableBalance ?? get().availableBalance,
          unrealizedPnl: p.unrealizedPnl ?? get().unrealizedPnl,
          realizedPnl: p.realizedPnl ?? get().realizedPnl,
        })
        // Append PnL history point
        const hist = [...get().dailyPnlHistory, { ts: msg.ts, value: get().unrealizedPnl + get().realizedPnl }]
        set({ dailyPnlHistory: hist.slice(-MAX_DAILY_PNL_POINTS) })
        break
      }
      case 'pairs':
        set({ pairs: payload as PairState[] })
        break
      case 'markets':
        set({ markets: payload as MarketData[] })
        break
      case 'regime': {
        const p = payload as { regime: VolatilityRegime; cb_open: boolean; cb_cooldown: number }
        set({
          volatilityRegime: p.regime,
          circuitBreakerOpen: p.cb_open,
          circuitBreakerCooldown: p.cb_cooldown,
        })
        break
      }
      case 'ws_status':
        set({ wsStatus: payload as WsStatusMap })
        break
      case 'log': {
        const entry = payload as LogEntry
        get().addLogEntry(entry)
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

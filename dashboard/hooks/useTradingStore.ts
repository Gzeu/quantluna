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

// ── Static mock data (NO Math.random() — safe for SSR) ───────────────────────
const MOCK_PAIRS: PairState[] = [
  { symbol: 'BTC/ETH',   zscore:  1.82, spread:  0.0412,  halfLife: 18.3, position: 'LONG',  pnl:  142.5, spreadHealth: 'HEALTHY'  },
  { symbol: 'SOL/AVAX',  zscore: -2.31, spread: -0.0871,  halfLife:  8.1, position: 'SHORT', pnl:  -23.1, spreadHealth: 'DEGRADED' },
  { symbol: 'BNB/MATIC', zscore:  0.44, spread:  0.0089,  halfLife: 24.0, position: 'FLAT',  pnl:    0.0, spreadHealth: 'HEALTHY'  },
]

// Fixed prices — will be replaced immediately by REST hydration on mount
const MOCK_MARKETS: MarketData[] = [
  { symbol: 'BTC',   price: 43215.50, change24h:  2.34, volume24h: 28_500_000_000, fundingRate:  0.00012 },
  { symbol: 'ETH',   price:  2310.88, change24h: -1.12, volume24h: 12_300_000_000, fundingRate: -0.00008 },
  { symbol: 'SOL',   price:    98.42, change24h:  5.67, volume24h:  3_200_000_000, fundingRate:  0.00021 },
  { symbol: 'BNB',   price:   312.55, change24h:  0.89, volume24h:  1_800_000_000, fundingRate:  0.00005 },
  { symbol: 'AVAX',  price:    27.31, change24h: -3.45, volume24h:    950_000_000, fundingRate: -0.00015 },
  { symbol: 'MATIC', price:    0.582, change24h:  1.23, volume24h:    620_000_000, fundingRate:  0.00008 },
  { symbol: 'DOT',   price:     6.12, change24h: -0.67, volume24h:    380_000_000, fundingRate:  0.00003 },
  { symbol: 'ADA',   price:    0.445, change24h:  3.21, volume24h:    510_000_000, fundingRate:  0.00011 },
  { symbol: 'LINK',  price:    14.82, change24h:  4.56, volume24h:    720_000_000, fundingRate:  0.00017 },
  { symbol: 'UNI',   price:     7.34, change24h: -2.11, volume24h:    280_000_000, fundingRate: -0.00009 },
  { symbol: 'ATOM',  price:     8.91, change24h:  1.78, volume24h:    190_000_000, fundingRate:  0.00006 },
  { symbol: 'NEAR',  price:     5.23, change24h:  6.12, volume24h:    340_000_000, fundingRate:  0.00019 },
  { symbol: 'FTM',   price:    0.782, change24h: -4.23, volume24h:    260_000_000, fundingRate: -0.00022 },
  { symbol: 'ALGO',  price:    0.182, change24h:  0.45, volume24h:    140_000_000, fundingRate:  0.00002 },
  { symbol: 'XRP',   price:    0.523, change24h:  2.89, volume24h:  1_100_000_000, fundingRate:  0.00010 },
  { symbol: 'LTC',   price:    72.45, change24h: -1.34, volume24h:    430_000_000, fundingRate:  0.00001 },
  { symbol: 'DOGE',  price:    0.082, change24h:  7.82, volume24h:    890_000_000, fundingRate:  0.00025 },
  { symbol: 'SHIB',  price: 0.0000098, change24h: -5.67, volume24h:   310_000_000, fundingRate: -0.00018 },
  { symbol: 'ARB',   price:    0.812, change24h:  3.45, volume24h:    220_000_000, fundingRate:  0.00014 },
  { symbol: 'OP',    price:     1.67, change24h: -2.78, volume24h:    180_000_000, fundingRate: -0.00011 },
]

const MOCK_LOG: LogEntry[] = [
  { ts: 0, level: 'INFO', module: 'SignalGen',  message: 'Kalman filter warmed up on BTC/ETH' },
  { ts: 0, level: 'BUY',  module: 'Executor',   message: 'LONG_SPREAD BTC/ETH z=1.82 qty=0.05' },
  { ts: 0, level: 'WARN', module: 'RiskMgr',    message: 'Volatility regime elevated → HIGH' },
  { ts: 0, level: 'ARB',  module: 'ArbScanner', message: 'Opportunity BTC Bybit/Binance spread=0.041%' },
]

const MOCK_ARB: ArbOpportunity[] = [
  { pair: 'BTC-USDT', bybitPrice: 43215.5, binancePrice: 43197.2, spreadPct: 0.00042, detectedAt: 0, ttlSeconds: 12 },
  { pair: 'ETH-USDT', bybitPrice:  2310.88, binancePrice:  2309.12, spreadPct: 0.00076, detectedAt: 0, ttlSeconds: 7  },
]

// Fixed sparkline — deterministic sine wave, no random
const MOCK_PNL_HISTORY: DailyPnlPoint[] = Array.from({ length: 50 }, (_, i) => ({
  ts: 0,
  value: 200 + Math.sin(i / 5) * 80 + i * 3,
}))

// ── Store ─────────────────────────────────────────────────────────────────────
export const useTradingStore = create<TradingState>((set, get) => ({
  totalBalance:      10_432.87,
  availableBalance:   7_215.44,
  unrealizedPnl:        119.43,
  realizedPnl:          312.00,
  dailyPnlHistory: MOCK_PNL_HISTORY,

  pairs:   MOCK_PAIRS,
  markets: MOCK_MARKETS,

  volatilityRegime:        'NORMAL' as VolatilityRegime,
  circuitBreakerOpen:      false,
  circuitBreakerCooldown:  0,
  wsStatus: { bybit: false, binance: false, okx: false } as WsStatusMap,

  logEntries:       MOCK_LOG,
  arbOpportunities: MOCK_ARB,

  // ── Actions ────────────────────────────────────────────────────────────────
  updateFromWsFeed: (msg: WsMessage) => {
    const { type, payload } = msg
    switch (type) {
      case 'balance': {
        const p = payload as Partial<TradingState>
        set({
          totalBalance:      p.totalBalance      ?? get().totalBalance,
          availableBalance:  p.availableBalance  ?? get().availableBalance,
          unrealizedPnl:     p.unrealizedPnl     ?? get().unrealizedPnl,
          realizedPnl:       p.realizedPnl       ?? get().realizedPnl,
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
      case 'markets':
        set({ markets: payload as MarketData[] })
        break
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
        // payload can be a single entry or an array (initial hydration)
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

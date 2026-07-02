/**
 * QuantLuna Dashboard — Shared TypeScript Interfaces
 * All interfaces exported from this single file.
 * No `any` types allowed (TypeScript strict mode).
 */

// ─── WebSocket ─────────────────────────────────────────────────────────────

export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

export interface WsMessage<T = unknown> {
  type: string
  payload: T
  ts: number
}

// ─── Balance ────────────────────────────────────────────────────────────────

export interface DailyPnlPoint {
  ts: number
  value: number
}

// ─── Pairs / Spread ─────────────────────────────────────────────────────────

export type SpreadHealth = 'HEALTHY' | 'DEGRADED' | 'STALE'
export type PositionSide = 'LONG' | 'SHORT' | 'FLAT'

export interface PairState {
  symbol: string
  zscore: number
  spread: number
  halfLife: number
  position: PositionSide
  pnl: number
  spreadHealth: SpreadHealth
}

// ─── Markets ────────────────────────────────────────────────────────────────

export interface MarketData {
  symbol: string
  price: number
  change24h: number
  volume24h: number
  fundingRate: number
}

// ─── Regime ─────────────────────────────────────────────────────────────────

export type VolatilityRegime = 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME'

export interface WsStatusMap {
  bybit: boolean
  binance: boolean
  okx: boolean
}

// ─── Execution Log ──────────────────────────────────────────────────────────

export type LogLevel = 'INFO' | 'BUY' | 'SELL' | 'WARN' | 'ARB' | 'ERROR' | 'RISK' | 'SYS'

export interface LogEntry {
  ts: number
  level: LogLevel
  module: string
  message: string
}

// ─── Arbitrage ───────────────────────────────────────────────────────────────

export interface ArbOpportunity {
  pair: string
  bybitPrice: number
  binancePrice: number
  spreadPct: number
  detectedAt: number
  ttlSeconds: number
}

// ─── Trading Store ────────────────────────────────────────────────────────────

export interface TradingState {
  // Balance
  totalBalance: number
  availableBalance: number
  unrealizedPnl: number
  realizedPnl: number
  dailyPnlHistory: DailyPnlPoint[]

  // Pairs
  pairs: PairState[]

  // Markets
  markets: MarketData[]

  // Regime
  volatilityRegime: VolatilityRegime
  circuitBreakerOpen: boolean
  circuitBreakerCooldown: number
  wsStatus: WsStatusMap

  // Log
  logEntries: LogEntry[]

  // Arbitrage
  arbOpportunities: ArbOpportunity[]

  // Actions
  updateFromWsFeed: (msg: WsMessage) => void
  addLogEntry: (entry: LogEntry) => void
  clearLog: () => void
}

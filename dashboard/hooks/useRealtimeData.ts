/**
 * dashboard/hooks/useRealtimeData.ts — QuantLuna Real-time Data Hook
 * Sprint S20 — 2026-07-11
 *
 * Conectare WebSocket cu:
 *   - Auto-reconnect cu exponential backoff (max 30s)
 *   - Buffer circular de 500 bare pentru grafice
 *   - SSE fallback automat dacă WS eșuează de 3 ori
 *   - Export: bars[], latestBar, isWarmingUp, warmupPct, isConnected, connectionType
 *
 * Backward compatible: componentele existente pot folosi /api/status
 * pentru polling dacă isConnected === false.
 *
 * Folosire:
 *   const { latestBar, bars, isWarmingUp, warmupPct, isConnected } = useRealtimeData()
 *   const { latestBar } = useRealtimeData({ url: '/ws/live', maxBars: 200 })
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface BarPayload {
  ts: number
  symbol_y: string
  symbol_x: string
  price_y: number
  price_x: number
  spread: number
  zscore: number
  zscore_abs: number
  vol_regime: string
  warmup_pct: number
  warmup_done: boolean
  circuit_open: boolean
  active_strategy: string
  pnl: number
  dry_run: boolean
  bar_count: number
  _server_ts?: number
  _type?: string
}

export interface WarmupStatus {
  bars_done: number
  bars_required: number
  pct: number
  coint_pvalue: number
  half_life_h: number
  regime: string
  ready: boolean
}

export interface UseRealtimeDataOptions {
  url?: string
  sseUrl?: string
  maxBars?: number
  reconnectBaseMs?: number
  reconnectMaxMs?: number
  maxWsFails?: number
}

export interface UseRealtimeDataResult {
  bars: BarPayload[]
  latestBar: BarPayload | null
  isWarmingUp: boolean
  warmupPct: number
  isConnected: boolean
  connectionType: 'websocket' | 'sse' | 'offline'
  circuitOpen: boolean
  activeStrategy: string
  pnl: number
  zscore: number
  volRegime: string
}

const DEFAULT_WS_URL = '/ws/live'
const DEFAULT_SSE_URL = '/api/stream'
const DEFAULT_MAX_BARS = 500
const DEFAULT_RECONNECT_BASE_MS = 1_000
const DEFAULT_RECONNECT_MAX_MS = 30_000
const DEFAULT_MAX_WS_FAILS = 3

export function useRealtimeData(
  options: UseRealtimeDataOptions = {}
): UseRealtimeDataResult {
  const {
    url = DEFAULT_WS_URL,
    sseUrl = DEFAULT_SSE_URL,
    maxBars = DEFAULT_MAX_BARS,
    reconnectBaseMs = DEFAULT_RECONNECT_BASE_MS,
    reconnectMaxMs = DEFAULT_RECONNECT_MAX_MS,
    maxWsFails = DEFAULT_MAX_WS_FAILS,
  } = options

  const [bars, setBars] = useState<BarPayload[]>([])
  const [latestBar, setLatestBar] = useState<BarPayload | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [connectionType, setConnectionType] = useState<'websocket' | 'sse' | 'offline'>('offline')

  const wsRef = useRef<WebSocket | null>(null)
  const sseRef = useRef<EventSource | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsFails = useRef(0)
  const reconnectDelay = useRef(reconnectBaseMs)
  const mountedRef = useRef(true)

  const appendBar = useCallback((bar: BarPayload) => {
    if (!mountedRef.current) return
    setBars(prev => {
      const next = [...prev, bar]
      return next.length > maxBars ? next.slice(next.length - maxBars) : next
    })
    setLatestBar(bar)
  }, [maxBars])

  const appendHistory = useCallback((history: BarPayload[]) => {
    if (!mountedRef.current) return
    setBars(prev => {
      const combined = [...prev, ...history]
      return combined.length > maxBars
        ? combined.slice(combined.length - maxBars)
        : combined
    })
    if (history.length > 0) {
      setLatestBar(history[history.length - 1])
    }
  }, [maxBars])

  // ─── SSE fallback ────────────────────────────────────────────────────────
  const startSSE = useCallback(() => {
    if (!mountedRef.current) return
    if (sseRef.current) {
      sseRef.current.close()
      sseRef.current = null
    }

    const host = window.location.origin.replace('3000', '8000').replace('3001', '8000')
    const sse = new EventSource(`${host}${sseUrl}`)
    sseRef.current = sse

    sse.onopen = () => {
      if (!mountedRef.current) return
      setIsConnected(true)
      setConnectionType('sse')
      reconnectDelay.current = reconnectBaseMs
    }

    sse.onmessage = (event) => {
      if (!mountedRef.current) return
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'heartbeat') return
        appendBar(data as BarPayload)
      } catch {
        // ignora parse errors
      }
    }

    sse.onerror = () => {
      if (!mountedRef.current) return
      setIsConnected(false)
      setConnectionType('offline')
      sse.close()
      sseRef.current = null
    }
  }, [sseUrl, reconnectBaseMs, appendBar])

  // ─── WebSocket principal ─────────────────────────────────────────────────
  const connectWS = useCallback(() => {
    if (!mountedRef.current) return
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.hostname
    const port = '8000'
    const wsUrl = `${protocol}//${host}:${port}${url}`

    let ws: WebSocket
    try {
      ws = new WebSocket(wsUrl)
    } catch {
      wsFails.current += 1
      if (wsFails.current >= maxWsFails) startSSE()
      return
    }
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      setIsConnected(true)
      setConnectionType('websocket')
      wsFails.current = 0
      reconnectDelay.current = reconnectBaseMs
    }

    ws.onmessage = (event) => {
      if (!mountedRef.current) return
      try {
        const data = JSON.parse(event.data)
        // Mesaj history la prima conectare
        if (data.type === 'history' && Array.isArray(data.bars)) {
          appendHistory(data.bars as BarPayload[])
          return
        }
        if (data._type === 'snapshot_fallback') {
          // Snapshot fallback — nu adăuga în bars, doar latestBar
          setLatestBar(data as BarPayload)
          return
        }
        appendBar(data as BarPayload)
      } catch {
        // ignora
      }
    }

    ws.onclose = (event) => {
      if (!mountedRef.current) return
      setIsConnected(false)
      setConnectionType('offline')
      wsRef.current = null
      wsFails.current += 1

      if (wsFails.current >= maxWsFails) {
        // Fallback la SSE după 3 eșecuri WS consecutive
        startSSE()
        return
      }

      // Reconnect cu exponential backoff
      const delay = Math.min(reconnectDelay.current, reconnectMaxMs)
      reconnectDelay.current = Math.min(delay * 2, reconnectMaxMs)
      reconnectTimerRef.current = setTimeout(connectWS, delay)
    }

    ws.onerror = () => {
      if (!mountedRef.current) return
      // onclose va fi apelat după onerror, reconectare acolo
    }
  }, [url, maxWsFails, reconnectBaseMs, reconnectMaxMs, appendBar, appendHistory, startSSE])

  // ─── Mount / Unmount ─────────────────────────────────────────────────────
  useEffect(() => {
    mountedRef.current = true
    connectWS()

    return () => {
      mountedRef.current = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      if (sseRef.current) {
        sseRef.current.close()
        sseRef.current = null
      }
    }
  }, [connectWS])

  // ─── Valori derivate ─────────────────────────────────────────────────────
  const isWarmingUp = latestBar ? !latestBar.warmup_done : true
  const warmupPct = latestBar?.warmup_pct ?? 0
  const circuitOpen = latestBar?.circuit_open ?? false
  const activeStrategy = latestBar?.active_strategy ?? 'kalman'
  const pnl = latestBar?.pnl ?? 0
  const zscore = latestBar?.zscore ?? 0
  const volRegime = latestBar?.vol_regime ?? 'UNKNOWN'

  return {
    bars,
    latestBar,
    isWarmingUp,
    warmupPct,
    isConnected,
    connectionType,
    circuitOpen,
    activeStrategy,
    pnl,
    zscore,
    volRegime,
  }
}

export default useRealtimeData

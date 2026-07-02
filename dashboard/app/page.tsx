'use client'

import { useCallback, useEffect, useRef } from 'react'
import RegimeHeader from '../components/RegimeHeader'
import BalanceTracker from '../components/BalanceTracker'
import ArbitragePanel from '../components/ArbitragePanel'
import ExecutionLog from '../components/ExecutionLog'
import SpreadMonitorPanel from '../components/SpreadMonitorPanel'
import MarketHeatmap from '../components/MarketHeatmap'
import { useWebSocket } from '../hooks/useWebSocket'
import { useTradingStore } from '../hooks/useTradingStore'
import { formatPrice, formatPercent } from '../lib/formatters'
import type { WsMessage } from '../types'

export default function DashboardPage() {
  const updateFromWsFeed  = useTradingStore((s) => s.updateFromWsFeed)
  const hydrateFromRest   = useTradingStore((s) => s.hydrateFromRest)
  const pairs             = useTradingStore((s) => s.pairs)
  const markets           = useTradingStore((s) => s.markets)

  const handleWsMessage = useCallback(
    (msg: WsMessage) => updateFromWsFeed(msg),
    [updateFromWsFeed],
  )

  const { status: wsStatus, reconnect } = useWebSocket(handleWsMessage)

  const sidebarRef  = useRef<HTMLDivElement>(null)
  const centerRef   = useRef<HTMLDivElement>(null)
  const rightRef    = useRef<HTMLDivElement>(null)
  const logRef      = useRef<HTMLDivElement>(null)
  const heatmapRef  = useRef<HTMLDivElement>(null)
  const pausedRef   = useRef(false)

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 'p')  { e.preventDefault(); pausedRef.current = !pausedRef.current }
      if (e.ctrlKey && e.key === '1')  sidebarRef.current?.focus()
      if (e.ctrlKey && e.key === '2')  centerRef.current?.focus()
      if (e.ctrlKey && e.key === '3')  rightRef.current?.focus()
      if (e.ctrlKey && e.key === '4')  logRef.current?.focus()
      if (e.ctrlKey && e.key === '5')  heatmapRef.current?.focus()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // REST hydration on mount — immediate + retry after 4s
  // (exchange init via ccxt can take 2-3s on first startup)
  useEffect(() => {
    hydrateFromRest()
    const t = setTimeout(() => hydrateFromRest(), 4000)
    return () => clearTimeout(t)
  }, [hydrateFromRest])

  return (
    <div
      className="h-screen w-screen overflow-hidden bg-bg-primary text-text-primary"
      style={{
        display: 'grid',
        gridTemplateAreas: '"header header header" "sidebar center right" "sidebar log log"',
        gridTemplateColumns: '240px 1fr 340px',
        gridTemplateRows: '44px 1fr 200px',
      }}
    >
      {/* HEADER */}
      <div style={{ gridArea: 'header' }}><RegimeHeader /></div>

      {/* SIDEBAR */}
      <div
        ref={sidebarRef} tabIndex={-1}
        style={{ gridArea: 'sidebar' }}
        className="flex flex-col gap-2 overflow-y-auto border-r border-bg-border p-2 focus:outline-none"
      >
        <p className="px-1 font-mono text-[10px] uppercase tracking-widest text-text-muted">Pairs</p>
        {pairs.length === 0 && (
          <p className="px-3 py-2 font-mono text-[10px] text-text-muted italic">No active positions</p>
        )}
        {pairs.map((p) => (
          <div key={p.symbol} className="rounded border border-bg-border bg-bg-panel px-3 py-2">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs font-semibold text-text-primary">{p.symbol}</span>
              <span className={`font-mono text-[10px] font-bold ${
                p.position === 'LONG' ? 'text-neon-green' :
                p.position === 'SHORT' ? 'text-alert-danger' : 'text-text-muted'
              }`}>{p.position}</span>
            </div>
            <div className="mt-1 flex items-center justify-between">
              <span className="font-mono text-[10px] text-text-muted">z={p.zscore.toFixed(2)}</span>
              <span className={`font-mono text-[10px] ${
                p.pnl >= 0 ? 'text-neon-green' : 'text-alert-danger'
              }`}>{p.pnl >= 0 ? '+' : ''}{formatPrice(p.pnl)}</span>
            </div>
          </div>
        ))}

        <p className="mt-2 px-1 font-mono text-[10px] uppercase tracking-widest text-text-muted">Markets</p>
        {markets.length === 0 && (
          <p className="px-3 py-1 font-mono text-[10px] text-text-muted italic">Loading…</p>
        )}
        {markets.slice(0, 15).map((m) => (
          <div key={m.symbol} className="flex items-center justify-between px-3 py-1 rounded border border-bg-border bg-bg-panel">
            <span className="font-mono text-[10px] text-text-primary font-semibold">{m.symbol}</span>
            <span className={`font-mono text-[10px] tabular-nums ${
              m.change24h >= 0 ? 'text-neon-green' : 'text-alert-danger'
            }`}>{formatPercent(m.change24h)}</span>
          </div>
        ))}
      </div>

      {/* CENTER */}
      <div
        ref={centerRef} tabIndex={-1}
        style={{ gridArea: 'center' }}
        className="flex flex-col gap-2 overflow-hidden p-2 focus:outline-none"
      >
        <div className="h-[30%] min-h-0"><SpreadMonitorPanel /></div>
        <div ref={heatmapRef} tabIndex={-1} className="flex-1 min-h-0 focus:outline-none">
          <MarketHeatmap />
        </div>
      </div>

      {/* RIGHT */}
      <div
        ref={rightRef} tabIndex={-1}
        style={{ gridArea: 'right' }}
        className="flex flex-col gap-2 overflow-y-auto border-l border-bg-border p-2 focus:outline-none"
      >
        <BalanceTracker />
        <ArbitragePanel />
        {wsStatus !== 'connected' && (
          <button
            onClick={reconnect}
            className="mt-auto rounded border border-alert-warn bg-alert-warn/10 px-3 py-1.5 font-mono text-xs text-alert-warn hover:bg-alert-warn/20 transition-colors"
          >
            WS {wsStatus.toUpperCase()} — click to reconnect
          </button>
        )}
      </div>

      {/* LOG */}
      <div
        ref={logRef} tabIndex={-1}
        style={{ gridArea: 'log' }}
        className="overflow-hidden border-t border-bg-border p-2 focus:outline-none"
      >
        <ExecutionLog />
      </div>
    </div>
  )
}

'use client'

import { useCallback, useEffect, useState } from 'react'
import { Bell } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useTradingStore } from '../hooks/useTradingStore'
import type { ArbOpportunity } from '../types'
import { formatPrice, formatPercent } from '../lib/formatters'

function TtlCountdown({ detectedAt, ttlSeconds }: { detectedAt: number; ttlSeconds: number }) {
  const [remaining, setRemaining] = useState(() => Math.max(0, ttlSeconds - (Date.now() - detectedAt) / 1000))
  useEffect(() => {
    const id = setInterval(() => setRemaining(Math.max(0, ttlSeconds - (Date.now() - detectedAt) / 1000)), 250)
    return () => clearInterval(id)
  }, [detectedAt, ttlSeconds])
  return (
    <span className={`font-mono text-xs tabular-nums ${
      remaining < 5 ? 'text-alert-danger font-bold animate-pulse' : 'text-text-muted'
    }`}>{remaining.toFixed(1)}s</span>
  )
}

export default function ArbitragePanel() {
  const arbOpportunities = useTradingStore((s) => s.arbOpportunities)
  const sorted = [...arbOpportunities].sort((a, b) => b.spreadPct - a.spreadPct)

  const handleTrade = useCallback((opp: ArbOpportunity) => {
    const msg = `▶ TRADE triggered: ${opp.pair} spread ${formatPercent(opp.spreadPct * 100, 3)}`
    const toast = document.createElement('div')
    toast.textContent = msg
    toast.className = 'fixed bottom-6 right-6 z-50 rounded-md border border-neon-magenta bg-bg-panel px-4 py-2 font-mono text-sm text-neon-magenta shadow-lg'
    document.body.appendChild(toast)
    setTimeout(() => toast.remove(), 3000)
  }, [])

  return (
    <div className="flex flex-col rounded-lg border border-bg-border bg-bg-panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-bg-border px-4 py-2">
        <span className="font-mono text-xs uppercase tracking-widest text-text-muted">Arbitrage Opportunities</span>
        <Bell size={14} className="text-text-muted" />
      </div>
      {sorted.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8 text-text-muted text-sm">
          No arbitrage opportunities detected
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border text-text-muted font-mono uppercase">
                {['Pair', 'Bybit $', 'Binance $', 'Spread %', 'TTL', 'Action'].map((h) => (
                  <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false}>
                {sorted.map((opp) => (
                  <motion.tr
                    key={`${opp.pair}-${opp.detectedAt}`}
                    initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
                    className={`border-b border-bg-border font-mono ${
                      opp.spreadPct > 0.0003 ? 'border-l-2 border-l-neon-magenta bg-neon-magenta/10' : ''
                    }`}
                  >
                    <td className="px-3 py-2 text-text-primary font-semibold">{opp.pair}</td>
                    <td className="px-3 py-2 tabular-nums">{formatPrice(opp.bybitPrice)}</td>
                    <td className="px-3 py-2 tabular-nums">{formatPrice(opp.binancePrice)}</td>
                    <td className="px-3 py-2 tabular-nums text-neon-green font-bold">{formatPercent(opp.spreadPct * 100, 3)}</td>
                    <td className="px-3 py-2"><TtlCountdown detectedAt={opp.detectedAt} ttlSeconds={opp.ttlSeconds} /></td>
                    <td className="px-3 py-2">
                      <button onClick={() => handleTrade(opp)}
                        className="rounded bg-neon-green/20 px-2 py-0.5 font-mono text-xs text-neon-green hover:bg-neon-green/40 transition-colors">
                        ▶ TRADE
                      </button>
                    </td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

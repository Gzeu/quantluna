'use client'

import { useMemo, useState } from 'react'
import { AreaChart, Area, ResponsiveContainer, ReferenceLine, Tooltip, XAxis } from 'recharts'
import { useTradingStore } from '../hooks/useTradingStore'
import type { SpreadHealth } from '../types'

const HEALTH_STYLES: Record<SpreadHealth, string> = {
  HEALTHY:  'bg-neon-green/20 text-neon-green border-neon-green/40',
  DEGRADED: 'bg-alert-warn/20 text-alert-warn border-alert-warn/40',
  STALE:    'bg-alert-danger/20 text-alert-danger border-alert-danger/40',
}

function zColor(z: number) {
  const a = Math.abs(z)
  if (a > 2.5) return 'text-alert-danger animate-pulse'
  if (a > 1.5) return 'text-alert-warn'
  if (a > 0.5) return 'text-neon-blue'
  return 'text-text-muted'
}
function zStroke(z: number) {
  const a = Math.abs(z)
  if (a > 2.5) return '#FF2244'
  if (a > 1.5) return '#FFAA00'
  if (a > 0.5) return '#0088FF'
  return '#666688'
}

export default function SpreadMonitorPanel() {
  const pairs = useTradingStore((s) => s.pairs)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const pair = pairs[selectedIdx] ?? pairs[0]

  const sparkData = useMemo(() => {
    if (!pair) return []
    const z = pair.zscore
    return Array.from({ length: 80 }, (_, i) => ({
      i,
      z: z * Math.sin((i / 15) + (z > 0 ? 1 : -1)) + (Math.random() - 0.5) * 0.3,
    }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pair?.symbol, pair?.zscore])

  if (!pair) return (
    <div className="flex items-center justify-center h-full text-text-muted font-mono text-sm">No pairs available</div>
  )

  const stroke = zStroke(pair.zscore)

  return (
    <div className="flex flex-col h-full rounded-lg border border-bg-border bg-bg-panel p-4 gap-3">
      <div className="flex items-center justify-between">
        <select value={selectedIdx} onChange={(e) => setSelectedIdx(Number(e.target.value))}
          className="bg-bg-primary border border-bg-border rounded px-2 py-1 font-mono text-xs text-text-primary focus:outline-none focus:border-neon-blue">
          {pairs.map((p, i) => <option key={p.symbol} value={i}>{p.symbol}</option>)}
        </select>
        <span className={`rounded border px-2 py-0.5 font-mono text-[10px] font-semibold ${HEALTH_STYLES[pair.spreadHealth]}`}>
          {pair.spreadHealth}
        </span>
      </div>
      <div className="flex items-end gap-4">
        <p className={`font-mono text-5xl font-bold tabular-nums ${zColor(pair.zscore)}`}>
          {pair.zscore >= 0 ? '+' : ''}{pair.zscore.toFixed(3)}
        </p>
        <div className="mb-1 flex flex-col gap-0.5">
          <span className="text-xs text-text-muted font-mono">z-score</span>
          <span className="text-xs font-mono text-text-primary" title="Estimated mean reversion half-life">HL: {pair.halfLife.toFixed(1)}h</span>
        </div>
      </div>
      <div className="flex-1 min-h-[80px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={sparkData} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="zGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={stroke} stopOpacity={0.25} />
                <stop offset="95%" stopColor={stroke} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <XAxis dataKey="i" hide />
            <Area type="monotone" dataKey="z" stroke={stroke} strokeWidth={1.5}
              fill="url(#zGrad)" dot={false} isAnimationActive={false} />
            {[-2.0, -0.5, 0.5, 2.0].map((v) => (
              <ReferenceLine key={v} y={v}
                stroke={Math.abs(v) >= 2.0 ? '#FF2244' : '#1A1A3E'}
                strokeDasharray="4 2" strokeWidth={1} />
            ))}
            <Tooltip
              contentStyle={{ background: '#0D0D1A', border: '1px solid #1A1A3E', borderRadius: 4 }}
              formatter={(v: number) => [v.toFixed(4), 'z-score']}
              labelStyle={{ display: 'none' }} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex items-center justify-between border-t border-bg-border pt-2">
        <div>
          <p className="text-[10px] text-text-muted font-mono">Spread</p>
          <p className="font-mono text-sm tabular-nums text-text-primary">{pair.spread.toFixed(6)}</p>
        </div>
        <div className="text-right">
          <p className="text-[10px] text-text-muted font-mono">Position</p>
          <p className={`font-mono text-sm font-bold ${
            pair.position === 'LONG' ? 'text-neon-green' :
            pair.position === 'SHORT' ? 'text-alert-danger' : 'text-text-muted'
          }`}>{pair.position}</p>
        </div>
      </div>
    </div>
  )
}

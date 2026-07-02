'use client'

import { useEffect, useRef, useState } from 'react'
import { LineChart, Line, ResponsiveContainer, Tooltip } from 'recharts'
import { useTradingStore } from '../hooks/useTradingStore'
import { formatPrice, formatPnl, formatPercent } from '../lib/formatters'

function useCountUp(target: number, duration = 600): number {
  const [display, setDisplay] = useState(target)
  const prevRef = useRef(target)
  useEffect(() => {
    const start = prevRef.current
    const diff = target - start
    if (Math.abs(diff) < 0.01) return
    const startTime = performance.now()
    let raf: number
    const step = (now: number) => {
      const elapsed = now - startTime
      const progress = Math.min(elapsed / duration, 1)
      const ease = 1 - Math.pow(1 - progress, 3)
      setDisplay(start + diff * ease)
      if (progress < 1) raf = requestAnimationFrame(step)
      else { prevRef.current = target; setDisplay(target) }
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])
  return display
}

export default function BalanceTracker() {
  const totalBalance     = useTradingStore((s) => s.totalBalance)
  const availableBalance = useTradingStore((s) => s.availableBalance)
  const unrealizedPnl    = useTradingStore((s) => s.unrealizedPnl)
  const dailyPnlHistory  = useTradingStore((s) => s.dailyPnlHistory)

  const displayBalance = useCountUp(totalBalance)

  const [flashClass, setFlashClass] = useState('')
  const prevPnl = useRef(unrealizedPnl)
  useEffect(() => {
    if (unrealizedPnl === prevPnl.current) return
    setFlashClass(unrealizedPnl > prevPnl.current ? 'flash-profit' : 'flash-loss')
    prevPnl.current = unrealizedPnl
    const t = setTimeout(() => setFlashClass(''), 350)
    return () => clearTimeout(t)
  }, [unrealizedPnl])

  const startOfDayBalance = dailyPnlHistory[0]?.value ?? totalBalance
  const dailyChange       = totalBalance - startOfDayBalance
  const dailyChangePct    = startOfDayBalance > 0 ? (dailyChange / startOfDayBalance) * 100 : 0
  const pnlFormatted      = formatPnl(unrealizedPnl)
  const dailyFormatted    = formatPnl(dailyChange)
  const marginUsed        = totalBalance - availableBalance
  const sparkData         = dailyPnlHistory.map((p) => ({ v: p.value }))

  return (
    <div className={`relative rounded-lg border border-bg-border bg-bg-panel p-4 transition-colors duration-300 ${
      flashClass === 'flash-profit' ? 'bg-neon-green/10' :
      flashClass === 'flash-loss'   ? 'bg-alert-danger/10' : ''
    }`}>
      <p className="mb-1 text-xs font-mono text-text-muted uppercase tracking-widest">Total Balance</p>
      <p className="font-mono text-3xl font-bold text-text-primary tabular-nums">
        {formatPrice(displayBalance)}
      </p>
      <div className="mt-2 flex items-center gap-2">
        <span className={`rounded-sm px-2 py-0.5 font-mono text-xs font-semibold ${
          dailyChange >= 0 ? 'bg-neon-green/20 text-neon-green' : 'bg-alert-danger/20 text-alert-danger'
        }`}>
          {dailyFormatted.text} ({formatPercent(dailyChangePct)})
        </span>
        <span className="text-xs text-text-muted font-mono">today</span>
      </div>
      <div className="mt-3 h-[60px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={sparkData}>
            <Line type="monotone" dataKey="v" stroke="#00FF88" strokeWidth={1.5} dot={false} />
            <Tooltip
              contentStyle={{ background: '#0D0D1A', border: '1px solid #1A1A3E', borderRadius: 4 }}
              labelStyle={{ display: 'none' }}
              formatter={(v: number) => [formatPrice(v), 'Equity']}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 border-t border-bg-border pt-3">
        {[
          { label: 'Available',   value: formatPrice(availableBalance) },
          { label: 'Margin Used', value: formatPrice(marginUsed) },
          { label: 'uPnL', value: pnlFormatted.text, color: unrealizedPnl >= 0 ? 'text-neon-green' : 'text-alert-danger' },
        ].map(({ label, value, color }) => (
          <div key={label} className="text-center">
            <p className="text-xs text-text-muted font-mono">{label}</p>
            <p className={`font-mono text-sm font-semibold tabular-nums ${color ?? 'text-text-primary'}`}>{value}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

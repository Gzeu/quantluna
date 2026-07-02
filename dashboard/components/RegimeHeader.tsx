'use client'

import { useEffect, useState } from 'react'
import { useTradingStore } from '../hooks/useTradingStore'
import type { VolatilityRegime } from '../types'

const REGIME_STYLES: Record<VolatilityRegime, string> = {
  LOW:     'bg-neon-green/20 text-neon-green border-neon-green/40',
  NORMAL:  'bg-neon-blue/20 text-neon-blue border-neon-blue/40',
  HIGH:    'bg-alert-warn/20 text-alert-warn border-alert-warn/40',
  EXTREME: 'bg-alert-danger/20 text-alert-danger border-alert-danger/40 animate-pulse',
}

function UtcClock() {
  const [time, setTime] = useState('')
  useEffect(() => {
    const tick = () =>
      setTime(new Date().toUTCString().split(' ').slice(4, 5)[0] + ' UTC')
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="font-mono text-xs text-text-muted tabular-nums">{time}</span>
}

function WsOrb({ label, connected }: { label: string; connected: boolean }) {
  return (
    <span className="flex items-center gap-1 font-mono text-[10px] text-text-muted">
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${
          connected ? 'bg-neon-green' : 'bg-alert-danger'
        }`}
      />
      {label}
    </span>
  )
}

export default function RegimeHeader() {
  const { volatilityRegime, circuitBreakerOpen, circuitBreakerCooldown, wsStatus } =
    useTradingStore()

  const [isLive, setIsLive] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem('ql_mode') === 'live'
  })

  const toggleMode = () => {
    const next = !isLive
    if (next && !window.confirm('Switch to LIVE trading mode? This will execute real orders.')) return
    setIsLive(next)
    localStorage.setItem('ql_mode', next ? 'live' : 'paper')
  }

  return (
    <header className="flex h-11 items-center justify-between border-b border-bg-border bg-bg-panel px-4 gap-3 shrink-0">
      {/* Logo */}
      <span className="font-mono text-sm font-bold text-neon-green tracking-wider glow-green whitespace-nowrap">
        ⟁ QUANTLUNA v0.30
      </span>

      {/* Regime badge */}
      <span className={`rounded-full border px-3 py-0.5 font-mono text-[10px] font-semibold ${
        REGIME_STYLES[volatilityRegime]
      }`}>
        {volatilityRegime}
      </span>

      {/* Circuit breaker */}
      {circuitBreakerOpen ? (
        <span className="flex items-center gap-1.5 rounded border border-alert-danger bg-alert-danger/20 px-2 py-0.5 font-mono text-[10px] text-alert-danger animate-pulse">
          CB OPEN
          {circuitBreakerCooldown > 0 && (
            <span className="tabular-nums">{circuitBreakerCooldown}s</span>
          )}
        </span>
      ) : (
        <span className="rounded border border-neon-green/40 bg-neon-green/10 px-2 py-0.5 font-mono text-[10px] text-neon-green">
          CB ✓
        </span>
      )}

      {/* WS orbs */}
      <div className="flex items-center gap-3">
        <WsOrb label="Bybit" connected={wsStatus.bybit} />
        <WsOrb label="Binance" connected={wsStatus.binance} />
        <WsOrb label="OKX" connected={wsStatus.okx} />
      </div>

      {/* UTC Clock */}
      <UtcClock />

      {/* Paper/Live toggle */}
      <button
        onClick={toggleMode}
        className={`rounded border font-mono text-[10px] px-2 py-0.5 transition-colors ${
          isLive
            ? 'border-alert-danger bg-alert-danger/20 text-alert-danger'
            : 'border-text-muted text-text-muted hover:border-neon-green hover:text-neon-green'
        }`}
      >
        {isLive ? '● LIVE' : '○ PAPER'}
      </button>

      {/* Keyboard hints — hidden on small screens */}
      <span className="hidden lg:block text-[9px] font-mono text-text-muted whitespace-nowrap">
        Ctrl+P Pause&nbsp;|&nbsp;Ctrl+E Export&nbsp;|&nbsp;Esc Dismiss
      </span>
    </header>
  )
}

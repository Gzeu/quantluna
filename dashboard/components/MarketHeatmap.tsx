'use client'
import { useState } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'
import type { MarketSymbol } from '../store/quantlunaStore'

function pctToColor(pct: number): string {
  if (pct === 0) return '#333355'
  const intensity = Math.min(Math.abs(pct) / 5, 1)
  if (pct > 0) {
    const g = Math.round(80 + 175 * intensity)
    const b = Math.round(60 + 76 * intensity)
    return `rgba(0, ${g}, ${b}, 0.85)`
  } else {
    const r = Math.round(80 + 175 * intensity)
    return `rgba(${r}, 0, 30, 0.85)`
  }
}

function HeatCell({ sym, onClick }: { sym: MarketSymbol; onClick: (s: string) => void }) {
  const [hover, setHover] = useState(false)
  const bg   = pctToColor(sym.change24h)
  const pct  = sym.change24h
  const col  = pct >= 0 ? '#00FF88' : '#FF2244'

  return (
    <div
      onClick={() => onClick(sym.symbol)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={`${sym.symbol}\n$${sym.price > 1 ? sym.price.toFixed(2) : sym.price.toFixed(5)}\n24h: ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%\nVol: $${(sym.volume24h/1e6).toFixed(0)}M\nFunding: ${sym.funding.toFixed(4)}%`}
      style={{
        background: bg,
        border: hover ? '1px solid #0088FF' : '1px solid rgba(26,26,62,0.4)',
        borderRadius: 2,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        padding: '1px 0',
        transition: 'border-color 0.1s',
        boxShadow: hover ? '0 0 6px rgba(0,136,255,0.4)' : 'none',
        overflow: 'hidden',
      }}
    >
      <span style={{ fontSize: 8, color: '#E0E0F0', fontWeight: 700, lineHeight: 1.2 }}>
        {sym.symbol.slice(0, 4)}
      </span>
      <span className="mono" style={{ fontSize: 7, color: col, lineHeight: 1.1 }}>
        {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
      </span>
    </div>
  )
}

export default function MarketHeatmap() {
  const markets        = useQuantLunaStore(s => s.markets)
  const setCandleSymbol = useQuantLunaStore(s => s.setCandleSymbol)

  const top50 = markets.slice(0, 50)

  return (
    <div className="ql-panel flex flex-col overflow-hidden">
      <div className="ql-panel-title">
        MARKET HEATMAP ── 50 symbols (click = load chart)
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(10, 1fr)',
          gridTemplateRows: 'repeat(5, 1fr)',
          gap: 2,
          padding: '4px 6px',
          flex: 1,
          overflow: 'hidden',
        }}
      >
        {top50.map(sym => (
          <HeatCell
            key={sym.symbol}
            sym={sym}
            onClick={(s) => setCandleSymbol(`${s}/USDT`)}
          />
        ))}
      </div>
    </div>
  )
}

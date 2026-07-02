'use client'

import { useCallback, useState } from 'react'
import { Treemap, ResponsiveContainer, Tooltip } from 'recharts'
import { useTradingStore } from '../hooks/useTradingStore'
import { formatPrice, formatPercent, formatVolume } from '../lib/formatters'
import type { MarketData } from '../types'

type SizeMode = 'change' | 'volume'

function interpolateColor(value: number, min = -5, max = 5): string {
  const clamped = Math.max(min, Math.min(max, value))
  const t = (clamped - min) / (max - min) // 0..1
  if (t < 0.5) {
    // red → black
    const r = Math.round(255 * (t * 2))
    return `rgb(${255 - r}, ${Math.round(r * 34 / 255)}, ${Math.round(r * 68 / 255)})`
  } else {
    // black → green
    const g = Math.round(255 * ((t - 0.5) * 2))
    return `rgb(0, ${g}, ${Math.round(g * 136 / 255)})`
  }
}

interface CustomContentProps {
  x?: number
  y?: number
  width?: number
  height?: number
  name?: string
  change24h?: number
}

function CustomContent(props: CustomContentProps) {
  const { x = 0, y = 0, width = 0, height = 0, name = '', change24h = 0 } = props
  const fill = interpolateColor(change24h)
  if (width < 20 || height < 16) return null
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} stroke="#08080F" strokeWidth={1} />
      {width > 40 && height > 24 && (
        <>
          <text x={x + width / 2} y={y + height / 2 - 6} textAnchor="middle" fill="#E0E0F0" fontSize={10} fontFamily="monospace" fontWeight={600}>
            {name}
          </text>
          <text x={x + width / 2} y={y + height / 2 + 8} textAnchor="middle" fill={change24h >= 0 ? '#00FF88' : '#FF2244'} fontSize={9} fontFamily="monospace">
            {formatPercent(change24h)}
          </text>
        </>
      )}
    </g>
  )
}

export default function MarketHeatmap() {
  const { markets } = useTradingStore()
  const [sizeMode, setSizeMode] = useState<SizeMode>('change')

  const data = markets.map((m: MarketData) => ({
    name: m.symbol,
    size: sizeMode === 'volume' ? Math.max(m.volume24h, 1) : Math.max(Math.abs(m.change24h), 0.01),
    change24h: m.change24h,
    price: m.price,
    volume24h: m.volume24h,
    fundingRate: m.fundingRate,
  }))

  const CustomTooltip = useCallback(({ active, payload }: { active?: boolean; payload?: Array<{ payload: typeof data[0] }> }) => {
    if (!active || !payload?.[0]) return null
    const d = payload[0].payload
    return (
      <div className="rounded border border-bg-border bg-bg-panel p-2 font-mono text-xs shadow-lg">
        <p className="font-bold text-text-primary">{d.name}</p>
        <p className="text-text-muted">Price: <span className="text-text-primary">{formatPrice(d.price)}</span></p>
        <p className="text-text-muted">24h: <span className={d.change24h >= 0 ? 'text-neon-green' : 'text-alert-danger'}>{formatPercent(d.change24h)}</span></p>
        <p className="text-text-muted">Vol: <span className="text-text-primary">{formatVolume(d.volume24h)}</span></p>
        <p className="text-text-muted">Fund: <span className="text-text-primary">{formatPercent(d.fundingRate * 100, 4)}</span></p>
      </div>
    )
  }, [])

  return (
    <div className="flex flex-col h-full rounded-lg border border-bg-border bg-bg-panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-bg-border px-4 py-2">
        <span className="font-mono text-xs uppercase tracking-widest text-text-muted">Market Heatmap</span>
        <button
          onClick={() => setSizeMode((m) => m === 'change' ? 'volume' : 'change')}
          className="font-mono text-[10px] rounded border border-bg-border px-2 py-0.5 text-text-muted hover:text-text-primary hover:border-neon-blue transition-colors"
        >
          {sizeMode === 'change' ? 'by Change%' : 'by Volume'}
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <Treemap
            data={data}
            dataKey="size"
            aspectRatio={4 / 3}
            content={<CustomContent />}
          >
            <Tooltip content={<CustomTooltip />} />
          </Treemap>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

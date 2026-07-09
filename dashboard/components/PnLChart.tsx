'use client'
import { useRef, useEffect } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'

const SERIES_COLORS: Record<string, string> = {
  'BTC/ETH': '#00FF88',
  'SOL/BNB': '#0088FF',
}

export default function PnLChart() {
  const pnlSeries = useQuantLunaStore(s => s.pnlSeries)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const w   = canvas.width
    const h   = canvas.height
    ctx.clearRect(0, 0, w, h)

    // Gather all points
    const allVals = Object.values(pnlSeries).flatMap(pts => pts.map(p => p.v))
    if (allVals.length < 2) return
    const mn  = Math.min(...allVals, 0)
    const mx  = Math.max(...allVals, 0)
    const rng = mx - mn || 1
    const ty  = (v: number) => h - ((v - mn) / rng) * (h - 4) - 2
    const zeroY = ty(0)

    // Grid
    ctx.strokeStyle = '#1A1A3E'
    ctx.lineWidth   = 0.5
    for (let i = 1; i < 4; i++) {
      ctx.beginPath()
      ctx.moveTo(0, (h / 4) * i)
      ctx.lineTo(w, (h / 4) * i)
      ctx.stroke()
    }

    // Zero line
    ctx.setLineDash([4, 4])
    ctx.strokeStyle = '#444466'
    ctx.lineWidth   = 1
    ctx.beginPath(); ctx.moveTo(0, zeroY); ctx.lineTo(w, zeroY); ctx.stroke()
    ctx.setLineDash([])

    // Max drawdown
    const minVal = Math.min(...allVals)
    if (minVal < 0) {
      const ddY = ty(minVal)
      ctx.setLineDash([3, 6])
      ctx.strokeStyle = '#FF224488'
      ctx.lineWidth   = 1
      ctx.beginPath(); ctx.moveTo(0, ddY); ctx.lineTo(w, ddY); ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = '#FF224488'
      ctx.font      = '8px JetBrains Mono'
      ctx.fillText(`MaxDD: ${minVal.toFixed(2)}$`, 4, ddY - 2)
    }

    // Series
    Object.entries(pnlSeries).forEach(([name, pts]) => {
      if (pts.length < 2) return
      const col = SERIES_COLORS[name] ?? '#8844FF'
      const xs  = pts.map((_, i) => (i / (pts.length - 1)) * w)
      const ys  = pts.map(p => ty(p.v))

      // Shade
      const grad = ctx.createLinearGradient(0, 0, 0, h)
      grad.addColorStop(0, col + '30')
      grad.addColorStop(1, col + '00')
      ctx.beginPath()
      ctx.moveTo(xs[0], ys[0])
      xs.slice(1).forEach((x, i) => ctx.lineTo(x, ys[i + 1]))
      ctx.lineTo(w, zeroY); ctx.lineTo(0, zeroY); ctx.closePath()
      ctx.fillStyle = grad; ctx.fill()

      // Line
      ctx.beginPath()
      ctx.moveTo(xs[0], ys[0])
      xs.slice(1).forEach((x, i) => ctx.lineTo(x, ys[i + 1]))
      ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.stroke()
    })

    // Legend
    let lx = 8
    Object.entries(SERIES_COLORS).forEach(([name, col]) => {
      ctx.fillStyle = col
      ctx.fillRect(lx, 6, 16, 2)
      ctx.fillStyle = '#666688'
      ctx.font      = '8px JetBrains Mono'
      ctx.fillText(name, lx + 20, 11)
      lx += 80
    })
  }, [pnlSeries])

  return (
    <div className="ql-panel flex flex-col" style={{ overflow: 'hidden' }}>
      <div className="ql-panel-title">PnL INTRADAY ── cumulative (reset UTC 00:00)</div>
      <div className="flex-1 px-2 py-1" style={{ minHeight: 0 }}>
        <canvas
          ref={canvasRef}
          width={600}
          height={100}
          style={{ width: '100%', height: '100%', display: 'block' }}
        />
      </div>
    </div>
  )
}

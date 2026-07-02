'use client'
import { useRef, useEffect } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'

type Point = { x: number; y: number }

function Sparkline({ data, color = '#FF00AA', height = 44 }:
  { data: number[]; color?: string; height?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || data.length < 2) return
    const ctx   = canvas.getContext('2d')!
    const w     = canvas.width
    const h     = canvas.height
    ctx.clearRect(0, 0, w, h)
    const mn  = Math.min(...data)
    const mx  = Math.max(...data)
    const rng = mx - mn || 1e-9
    const pts: Point[] = data.map((v, i) => ({
      x: (i / (data.length - 1)) * w,
      y: h - ((v - mn) / rng) * (h - 4) - 2,
    }))
    // Fill
    const grad = ctx.createLinearGradient(0, 0, 0, h)
    const c    = color
    grad.addColorStop(0, c + '3A')
    grad.addColorStop(1, c + '00')
    ctx.beginPath()
    ctx.moveTo(pts[0].x, pts[0].y)
    pts.slice(1).forEach(p => ctx.lineTo(p.x, p.y))
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath()
    ctx.fillStyle = grad
    ctx.fill()
    // Line
    ctx.beginPath()
    ctx.moveTo(pts[0].x, pts[0].y)
    pts.slice(1).forEach(p => ctx.lineTo(p.x, p.y))
    ctx.strokeStyle = color
    ctx.lineWidth   = 1.5
    ctx.stroke()
  }, [data, color])

  return (
    <canvas
      ref={canvasRef}
      width={400}
      height={height}
      style={{ width: '100%', height }}
    />
  )
}

export default function SpreadMonitorPanel() {
  const spread = useQuantLunaStore(s => s.spread)
  const histRef = useRef<number[]>([])

  useEffect(() => {
    if (spread?.spread !== undefined) {
      histRef.current = [...histRef.current.slice(-199), spread.spread]
    }
  }, [spread])

  const z   = spread?.z       ?? 0
  const sp  = spread?.spread  ?? 0
  const hl  = spread?.halfLife ?? 0
  const kp  = spread?.kalmanP ?? 0
  const h   = spread?.health  ?? 'HEALTHY'

  const zCol  = Math.abs(z) > 2 ? '#FFAA00' : Math.abs(z) > 3 ? '#FF2244' : '#00FF88'
  const hCol  = h === 'HEALTHY' ? '#00FF88' : h === 'DEGRADED' ? '#FFAA00' : '#FF2244'

  // Z-score bar (normalized -4..+4 → 0..100%)
  const zNorm   = Math.min(Math.max((z + 4) / 8, 0), 1)
  const zBarW   = `${(zNorm * 100).toFixed(1)}%`
  const zBarCol = Math.abs(z) < 0.5 ? '#00FF88' : Math.abs(z) < 2 ? '#0088FF' : Math.abs(z) < 3 ? '#FFAA00' : '#FF2244'

  return (
    <div className="ql-panel flex flex-col" style={{ minHeight: 110, maxHeight: 130 }}>
      <div className="ql-panel-title">
        SPREAD MONITOR ── Ornstein-Uhlenbeck
      </div>
      <div className="flex flex-1 gap-4 px-3 py-2 overflow-hidden">
        {/* Metrics */}
        <div className="flex flex-col gap-1 shrink-0" style={{ minWidth: 200 }}>
          <MetricRow label="z-score"   value={`${z >= 0 ? '+' : ''}${z.toFixed(4)}`}    color={zCol} />
          <MetricRow label="spread"    value={sp.toFixed(6)}                              color="#E0E0F0" />
          <MetricRow label="half-life" value={`${hl.toFixed(1)}h`}                        color="#8844FF" />
          <MetricRow label="KalmanP"   value={kp.toFixed(6)}                              color="#666688" />
          <div className="flex items-center gap-2 mt-1">
            <span className="orb" style={{ background: hCol, boxShadow: `0 0 5px ${hCol}`, width:7, height:7, borderRadius:'50%', display:'inline-block' }} />
            <span className="mono" style={{ color: hCol, fontSize: 10, fontWeight: 700 }}>{h}</span>
          </div>
        </div>

        {/* Z-score bar + sparkline */}
        <div className="flex flex-col flex-1 gap-2 overflow-hidden">
          <div>
            <div className="flex justify-between" style={{ fontSize: 9, color: '#666688', marginBottom: 2 }}>
              <span>-4</span>
              <span style={{ color: '#666688' }}>z-score range</span>
              <span>+4</span>
            </div>
            <div style={{ height: 8, background: '#08080F', borderRadius: 4, position: 'relative', overflow: 'hidden' }}>
              {/* threshold markers */}
              <div style={{ position:'absolute', left:'62.5%', top:0, width:1, height:'100%', background:'rgba(255,170,0,0.5)' }} />
              <div style={{ position:'absolute', left:'37.5%', top:0, width:1, height:'100%', background:'rgba(255,170,0,0.5)' }} />
              <div
                style={{
                  position:'absolute', left: 0, top: 0,
                  width: zBarW, height: '100%',
                  background: zBarCol,
                  boxShadow: `0 0 6px ${zBarCol}`,
                  transition: 'width 0.15s ease-out',
                  borderRadius: 4,
                }}
              />
            </div>
            <div className="flex justify-between" style={{ fontSize: 8, color: '#444466', marginTop: 1 }}>
              <span>-2.0</span><span>0</span><span>+2.0</span>
            </div>
          </div>
          <div className="flex-1 overflow-hidden">
            <Sparkline data={histRef.current} color="#FF00AA" height={38} />
          </div>
        </div>
      </div>
    </div>
  )
}

function MetricRow({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex gap-2" style={{ fontSize: 10 }}>
      <span style={{ color: '#666688', minWidth: 68 }}>{label}:</span>
      <span className="mono" style={{ color, fontWeight: 600 }}>{value}</span>
    </div>
  )
}

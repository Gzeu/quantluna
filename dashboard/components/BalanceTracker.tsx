'use client'
import { useEffect, useRef, useState } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'

function MiniSparkline({ data, color }: { data: number[]; color: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current; if (!c || data.length < 2) return
    const ctx = c.getContext('2d')!
    const w = c.width; const h = c.height
    ctx.clearRect(0, 0, w, h)
    const mn = Math.min(...data); const mx = Math.max(...data)
    const rng = mx - mn || 1
    const pts = data.map((v, i) => ({ x: (i/(data.length-1))*w, y: h - ((v-mn)/rng)*(h-2) - 1 }))
    const grad = ctx.createLinearGradient(0,0,0,h)
    grad.addColorStop(0, color+'40'); grad.addColorStop(1, color+'00')
    ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y)
    pts.slice(1).forEach(p => ctx.lineTo(p.x, p.y))
    ctx.lineTo(w,h); ctx.lineTo(0,h); ctx.closePath()
    ctx.fillStyle = grad; ctx.fill()
    ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y)
    pts.slice(1).forEach(p => ctx.lineTo(p.x, p.y))
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke()
  }, [data, color])
  return <canvas ref={ref} width={280} height={36} style={{width:'100%', height:36}} />
}

export default function BalanceTracker() {
  const pnl         = useQuantLunaStore(s => s.pnl)
  const [flash, setFlash] = useState<'profit'|'loss'|null>(null)
  const prevTotal   = useRef(0)
  const equityHist  = useRef<number[]>([])

  useEffect(() => {
    if (!pnl) return
    const cur = pnl.total
    if (prevTotal.current && cur !== prevTotal.current) {
      setFlash(cur > prevTotal.current ? 'profit' : 'loss')
      setTimeout(() => setFlash(null), 600)
    }
    prevTotal.current = cur
    equityHist.current = [...equityHist.current.slice(-95), cur]
  }, [pnl])

  const total   = pnl?.total    ?? 0
  const avail   = pnl?.available ?? 0
  const margin  = pnl?.margin   ?? 0
  const unreal  = pnl?.unrealized ?? 0
  const dpnl    = pnl?.dailyPnl ?? 0
  const dpct    = pnl?.dailyPct ?? 0

  const profitCol = dpnl >= 0 ? '#00FF88' : '#FF2244'
  const flashBg   = flash === 'profit'
    ? 'rgba(0,255,136,0.08)'
    : flash === 'loss' ? 'rgba(255,34,68,0.08)' : 'transparent'

  return (
    <div
      className="ql-panel flex flex-col"
      style={{ transition: 'background 0.3s', background: flashBg === 'transparent' ? '#0D0D1A' : flashBg }}
    >
      <div className="ql-panel-title">BALANCE TRACKER</div>
      <div className="flex flex-col gap-2 px-3 py-3">
        {/* Total */}
        <div>
          <div
            className="mono glow-green"
            style={{ fontSize: 26, fontWeight: 700, color: '#00FF88', lineHeight: 1 }}
          >
            ${total.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div
            className="mono"
            style={{ color: profitCol, fontSize: 11, marginTop: 2 }}
          >
            {dpnl >= 0 ? '+' : ''}{dpnl.toFixed(2)}$
            {'  '}
            <span style={{ color: '#666688' }}>({dpct >= 0 ? '+' : ''}{dpct.toFixed(3)}%)</span>
          </div>
        </div>

        {/* Split */}
        <div className="grid gap-1" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
          <BalRow label="Available" value={`$${avail.toLocaleString('en-US',{minimumFractionDigits:0})}`} color="#0088FF" />
          <BalRow label="Margin"    value={`$${margin.toLocaleString('en-US',{minimumFractionDigits:0})}`} color="#FFAA00" />
          <BalRow label="Unrealized" value={`${unreal>=0?'+':''}$${unreal.toFixed(2)}`} color={unreal>=0?'#00FF88':'#FF2244'} />
        </div>

        {/* Equity sparkline */}
        <MiniSparkline data={equityHist.current} color={dpnl >= 0 ? '#00FF88' : '#FF2244'} />
      </div>
    </div>
  )
}

function BalRow({ label, value, color }: { label:string; value:string; color:string }) {
  return (
    <div className="flex flex-col">
      <span style={{ color: '#666688', fontSize: 8 }}>{label}</span>
      <span className="mono" style={{ color, fontSize: 10, fontWeight: 600 }}>{value}</span>
    </div>
  )
}

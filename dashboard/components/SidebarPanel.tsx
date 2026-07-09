'use client'
import { useQuantLunaStore } from '../store/quantlunaStore'
import type { Regime } from '../store/quantlunaStore'

const REGIME_MAP: Record<Regime, number> = { LOW: 0.15, NORMAL: 0.4, HIGH: 0.72, EXTREME: 0.95 }
const REGIME_COL: Record<Regime, string> = {
  LOW: '#00FF88', NORMAL: '#0088FF', HIGH: '#FFAA00', EXTREME: '#FF2244',
}

function GaugeSVG({ regime }: { regime: Regime }) {
  const val = REGIME_MAP[regime] ?? 0.4
  const col = REGIME_COL[regime] ?? '#0088FF'
  const r   = 28
  const cx  = 40
  const cy  = 40
  const startAngle  = 225
  const totalAngle  = 270
  const sweepAngle  = totalAngle * val
  const toRad = (d: number) => (d * Math.PI) / 180
  const arc = (angle: number) => [
    cx + r * Math.cos(toRad(angle - 90)),
    cy + r * Math.sin(toRad(angle - 90)),
  ]
  const [sx, sy] = arc(startAngle)
  const [ex, ey] = arc(startAngle - sweepAngle)
  const large     = sweepAngle > 180 ? 1 : 0
  const [tx, ty] = arc(startAngle)
  const [bx, by] = arc(startAngle - totalAngle)
  return (
    <svg width={80} height={80} style={{ overflow: 'visible' }}>
      <path
        d={`M ${arc(startAngle)[0]} ${arc(startAngle)[1]} A ${r} ${r} 0 1 0 ${arc(startAngle - totalAngle)[0]} ${arc(startAngle - totalAngle)[1]}`}
        fill="none" stroke="#1A1A3E" strokeWidth={5} strokeLinecap="round"
      />
      {val > 0 && (
        <path
          d={`M ${sx} ${sy} A ${r} ${r} 0 ${large} 0 ${ex} ${ey}`}
          fill="none" stroke={col} strokeWidth={5} strokeLinecap="round"
          style={{ filter: `drop-shadow(0 0 4px ${col})` }}
        />
      )}
      <text x={cx} y={cy + 5} textAnchor="middle"
        style={{ fill: col, fontSize: 8, fontFamily: 'JetBrains Mono', fontWeight: 700 }}>
        {regime}
      </text>
    </svg>
  )
}

function PairRow({ pair, z, halfLife, spread, spreadDelta, pnl, position }:
  { pair:string; z:number; halfLife:number; spread:number;
    spreadDelta:number; pnl:number; position:string }) {
  const profitCol = pnl >= 0 ? '#00FF88' : '#FF2244'
  const borderCol = pnl >= 0 ? 'rgba(0,255,136,0.35)' : 'rgba(255,34,68,0.35)'
  const posCol    = position === 'LONG' ? '#00FF88' : position === 'SHORT' ? '#FF2244' : '#666688'
  return (
    <div
      className={`mb-1 rounded ${position !== 'FLAT' ? (pnl>=0?'pos-open-long':'pos-open-short') : ''}`}
      style={{
        background: '#0D0D1A',
        border: `1px solid ${borderCol}`,
        padding: '5px 7px',
        fontSize: 9,
      }}
    >
      <div className="flex justify-between items-center mb-1">
        <span style={{ color: '#E0E0F0', fontWeight: 700, fontSize: 10 }}>{pair}</span>
        <span className="mono" style={{ color: posCol, fontSize: 9 }}>{position !== 'FLAT' ? `● ${position}` : '○ FLAT'}</span>
      </div>
      <div className="mono" style={{ color: '#666688' }}>
        z: <span style={{ color: Math.abs(z) > 2 ? '#FFAA00' : '#E0E0F0' }}>{z > 0 ? '+' : ''}{z.toFixed(3)}</span>
        {'  '}
        hl: <span style={{ color: '#8844FF' }}>{halfLife.toFixed(1)}h</span>
      </div>
      <div className="mono" style={{ color: '#666688' }}>
        sprd: <span style={{ color: '#E0E0F0' }}>{spread.toFixed(5)}</span>
        {'  '}
        <span style={{ color: spreadDelta >= 0 ? '#00FF88' : '#FF2244' }}>
          {spreadDelta >= 0 ? '+' : ''}{spreadDelta.toFixed(5)}
        </span>
      </div>
      <div className="mono" style={{ color: profitCol, fontWeight: 700 }}>
        PnL: {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}$
      </div>
    </div>
  )
}

export default function SidebarPanel() {
  const { pairs, markets, regime: regimeData, spread } = useQuantLunaStore()
  const regime = regimeData?.regime ?? 'NORMAL'
  const kp     = spread?.kalmanP ?? 0

  return (
    <aside
      className="flex flex-col shrink-0 overflow-hidden"
      style={{ width: 238, background: '#0D0D1A', borderRight: '1px solid #1A1A3E' }}
    >
      {/* PAIRS */}
      <div className="ql-panel-title">PAIRS MONITORED</div>
      <div className="px-2 pt-2">
        {pairs.map(p => <PairRow key={p.pair} {...p} />)}
      </div>

      {/* SINGLE MARKETS */}
      <div className="ql-panel-title mt-2">SINGLE MARKETS</div>
      <div className="overflow-y-auto flex-1 px-2 py-1">
        {markets.slice(0, 30).map(m => {
          const col = m.change24h >= 0 ? '#00FF88' : '#FF2244'
          return (
            <div
              key={m.symbol}
              className="flex justify-between items-center py-px"
              style={{ fontSize: 9, borderBottom: '1px solid rgba(26,26,62,0.4)' }}
            >
              <span style={{ color: '#666688', width: 40, flexShrink: 0 }}>{m.symbol}</span>
              <span className="mono" style={{ color: '#E0E0F0', flex: 1, textAlign: 'right' }}>
                {m.price > 1000 ? m.price.toFixed(1) : m.price > 1 ? m.price.toFixed(3) : m.price.toFixed(5)}
              </span>
              <span className="mono" style={{ color: col, width: 52, textAlign: 'right' }}>
                {m.change24h >= 0 ? '+' : ''}{m.change24h.toFixed(2)}%
              </span>
            </div>
          )
        })}
      </div>

      {/* REGIME */}
      <div className="ql-panel-title">REGIME</div>
      <div className="flex flex-col items-center py-3 gap-1">
        <GaugeSVG regime={regime} />
        <span className="mono" style={{ color: '#8844FF', fontSize: 9 }}>
          KalmanP: {kp.toFixed(6)}
        </span>
      </div>
    </aside>
  )
}

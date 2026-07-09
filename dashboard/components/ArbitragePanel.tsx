'use client'
import { useEffect, useRef, useState } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'
import type { ArbOpportunity } from '../store/quantlunaStore'

function TtlBar({ ttl, ttlMax }: { ttl: number; ttlMax: number }) {
  const pct  = Math.max(0, (ttl / ttlMax) * 100)
  const col  = pct > 60 ? '#00FF88' : pct > 30 ? '#FFAA00' : '#FF2244'
  return (
    <div style={{ width: 36, height: 6, background: '#0A0A14', borderRadius: 3, overflow: 'hidden' }}>
      <div
        style={{
          width: `${pct}%`, height: '100%',
          background: col,
          boxShadow: `0 0 4px ${col}`,
          transition: 'width 0.25s linear',
        }}
      />
    </div>
  )
}

function ArbRow({ opp, onExec }: { opp: ArbOpportunity; onExec: (o: ArbOpportunity) => void }) {
  const isHot = opp.spreadPct > 0.03
  return (
    <tr
      className={isHot ? 'arb-hot' : ''}
      style={{
        cursor: 'pointer',
        borderBottom: '1px solid rgba(26,26,62,0.5)',
      }}
    >
      <td className="ql-table-td"
        style={{ color: isHot ? '#FF00AA' : '#E0E0F0', fontWeight: isHot ? 700 : 400 }}>
        {opp.pair}
      </td>
      <td className="ql-table-td mono" style={{ color: '#E0E0F0' }}>
        {opp.bybit.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </td>
      <td className="ql-table-td mono" style={{ color: '#E0E0F0' }}>
        {opp.binance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </td>
      <td className="ql-table-td mono"
        style={{ color: isHot ? '#FF00AA' : '#FFAA00', fontWeight: isHot ? 700 : 400 }}>
        {opp.spreadPct.toFixed(4)}%
      </td>
      <td className="ql-table-td">
        <div className="flex flex-col items-center gap-1">
          <span className="mono" style={{ color: opp.ttl < 6 ? '#FF2244' : '#666688', fontSize: 9 }}>
            {opp.ttl}s
          </span>
          <TtlBar ttl={opp.ttl} ttlMax={opp.ttlMax} />
        </div>
      </td>
      <td className="ql-table-td" style={{ textAlign: 'center' }}>
        <button
          onClick={() => onExec(opp)}
          className="mono"
          style={{
            background: 'rgba(0,255,136,0.1)',
            border: '1px solid #00FF88',
            color: '#00FF88',
            fontSize: 9, padding: '1px 6px', borderRadius: 2, cursor: 'pointer',
          }}
        >
          ▶
        </button>
      </td>
    </tr>
  )
}

export default function ArbitragePanel() {
  const { arb, setModal } = useQuantLunaStore()
  const [soundOn, setSoundOn] = useState(true)
  const prevCountRef = useRef(0)
  const audioRef     = useRef<AudioContext | null>(null)

  // TTL countdown
  const [ticked, setTicked] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setTicked(n => n + 1), 1000)
    return () => clearInterval(t)
  }, [])

  // Beep on new arb
  useEffect(() => {
    const cur = arb.filter(a => a.spreadPct > 0.03).length
    if (soundOn && cur > prevCountRef.current) {
      try {
        if (!audioRef.current) audioRef.current = new AudioContext()
        const osc  = audioRef.current.createOscillator()
        const gain = audioRef.current.createGain()
        osc.connect(gain); gain.connect(audioRef.current.destination)
        osc.frequency.value = 880
        gain.gain.setValueAtTime(0.08, audioRef.current.currentTime)
        gain.gain.exponentialRampToValueAtTime(0.001, audioRef.current.currentTime + 0.18)
        osc.start(); osc.stop(audioRef.current.currentTime + 0.18)
      } catch {}
    }
    prevCountRef.current = cur
  }, [arb, soundOn])

  const hotCount = arb.filter(a => a.spreadPct > 0.03).length

  return (
    <div className="ql-panel flex flex-col overflow-hidden">
      <div className="ql-panel-title flex items-center justify-between">
        <span>ARBITRAGE DETECTION</span>
        {hotCount > 0 && (
          <span
            className="mono"
            style={{
              color: '#FF00AA', fontSize: 9, fontWeight: 700,
              border: '1px solid #FF00AA', padding: '1px 6px',
              borderRadius: 2, animation: 'arb-pulse 1s ease-in-out infinite',
            }}
          >
            {hotCount} HOT
          </span>
        )}
      </div>

      <div className="overflow-y-auto flex-1">
        <table className="ql-table">
          <thead>
            <tr>
              <th>PAIR</th>
              <th>BYBIT</th>
              <th>BINANCE</th>
              <th>SPD%</th>
              <th>TTL</th>
              <th>⚡</th>
            </tr>
          </thead>
          <tbody>
            {arb.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ textAlign: 'center', color: '#666688', padding: '12px', fontSize: 10 }}>
                  scanning...
                </td>
              </tr>
            ) : (
              arb.map(opp => (
                <ArbRow
                  key={opp.id}
                  opp={{ ...opp, ttl: Math.max(0, opp.ttl - Math.floor((Date.now() - opp.detectedAt) / 1000)) }}
                  onExec={() => setModal('order')}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between px-3 py-1" style={{ borderTop: '1px solid #1A1A3E' }}>
        <label className="flex items-center gap-2" style={{ cursor: 'pointer', fontSize: 9, color: '#666688' }}>
          <input
            type="checkbox"
            checked={soundOn}
            onChange={e => setSoundOn(e.target.checked)}
            style={{ accentColor: '#0088FF' }}
          />
          Alert sound
        </label>
        <span className="mono" style={{ fontSize: 9, color: '#666688' }}>
          {arb.length} opportunities
        </span>
      </div>
    </div>
  )
}

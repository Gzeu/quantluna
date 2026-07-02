'use client'
import { useState, useEffect } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'
import type { Regime } from '../store/quantlunaStore'

const REGIME_CONFIG: Record<Regime, { icon: string; color: string; cls: string }> = {
  LOW:     { icon: '▼', color: '#00FF88', cls: 'text-green' },
  NORMAL:  { icon: '●', color: '#0088FF', cls: 'text-blue'  },
  HIGH:    { icon: '▲', color: '#FFAA00', cls: 'text-yellow' },
  EXTREME: { icon: '⚡', color: '#FF2244', cls: 'regime-extreme' },
}

function HealthOrb({ label, status }: { label: string; status: 'online'|'offline'|'latency' }) {
  const cls = status === 'online' ? 'orb-green' : status === 'latency' ? 'orb-yellow' : 'orb-red'
  return (
    <div className="flex items-center gap-1 mr-3">
      <span className={`orb ${cls}`} />
      <span style={{ color: '#666688', fontSize: 9 }}>{label}</span>
    </div>
  )
}

function ConfirmModal({ onConfirm, onCancel }: { onConfirm: ()=>void; onCancel: ()=>void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center"
         style={{ background: 'rgba(0,0,0,0.7)' }}>
      <div className="ql-panel p-6" style={{ minWidth: 340 }}>
        <div className="ql-panel-title mb-4">⚡ MODE SWITCH CONFIRMATION</div>
        <p style={{ color: '#E0E0F0', fontSize: 12, marginBottom: 16 }}>
          Confirmi schimbarea în modul <span style={{color:'#FF2244',fontWeight:'bold'}}>LIVE</span>?<br/>
          <span style={{color:'#666688'}}>Toate ordinele vor fi executate în real.</span>
        </p>
        <div className="flex gap-3">
          <button className="ql-btn ql-btn-red flex-1" onClick={onConfirm}>✓ DA, ACTIVEZ LIVE</button>
          <button className="ql-btn flex-1" onClick={onCancel}>✗ Anuleaza</button>
        </div>
      </div>
    </div>
  )
}

export default function HeaderBar() {
  const { regime: regimeData, isLive, toggleLive, isPaused, togglePause } = useQuantLunaStore()
  const [utc, setUtc]         = useState('')
  const [showConfirm, setShowConfirm] = useState(false)
  const [ms, setMs]           = useState(0)

  useEffect(() => {
    const t = setInterval(() => {
      const n = new Date()
      setUtc(n.toISOString().slice(11, 19))
      setMs(n.getMilliseconds())
    }, 100)
    return () => clearInterval(t)
  }, [])

  const regime    = regimeData?.regime ?? 'NORMAL'
  const rc        = REGIME_CONFIG[regime]
  const cbOpen    = regimeData?.cbOpen ?? false
  const cbCd      = regimeData?.cbCountdown ?? 0
  const lat       = regimeData?.latencyMs ?? 0

  const handleLiveToggle = () => {
    if (!isLive) setShowConfirm(true)
    else toggleLive()
  }

  return (
    <>
      <header
        className="flex items-center px-3 shrink-0"
        style={{
          height: 40,
          background: '#0D0D1A',
          borderBottom: '1px solid #1A1A3E',
          gap: 0,
          overflow: 'hidden',
        }}
      >
        {/* Logo */}
        <span
          className="mono glow-green"
          style={{ fontSize: 14, fontWeight: 700, letterSpacing: 3, color: '#00FF88', marginRight: 16, whiteSpace: 'nowrap' }}
        >
          ⟁ QUANTLUNA
        </span>

        <VSep />

        {/* Regime */}
        <div className="flex items-center gap-2 mx-3">
          <span style={{ color: '#666688', fontSize: 9 }}>REGIME</span>
          <span
            className={`mono ${regime === 'EXTREME' ? 'regime-extreme' : ''}`}
            style={{ color: rc.color, fontSize: 10, fontWeight: 700 }}
          >
            {rc.icon} {regime}
          </span>
        </div>

        <VSep />

        {/* Circuit Breaker */}
        <div className="flex items-center gap-2 mx-3">
          <span style={{ color: '#666688', fontSize: 9 }}>CB</span>
          <span
            className="mono"
            style={{ color: cbOpen ? '#FF2244' : '#00FF88', fontSize: 10, fontWeight: 700 }}
          >
            {cbOpen ? `✗ OPEN [${String(cbCd).padStart(2,'0')}s]` : '✓ CLOSED'}
          </span>
        </div>

        <VSep />

        {/* Health orbs */}
        <div className="flex items-center mx-2">
          <HealthOrb label="WS"  status={regimeData?.wsOk      ? 'online' : 'offline'} />
          <HealthOrb label="BYB" status={regimeData?.bybitOk   ? 'online' : 'offline'} />
          <HealthOrb label="BNB" status={
            !regimeData?.binanceOk ? 'offline' : lat > 200 ? 'latency' : 'online'
          } />
          <HealthOrb label="OKX" status={regimeData?.okxOk     ? 'online' : 'offline'} />
          {lat > 0 && (
            <span style={{ color: lat > 200 ? '#FFAA00' : '#666688', fontSize: 9 }}>
              {lat}ms
            </span>
          )}
        </div>

        <div className="flex-1" />

        {/* Pause indicator */}
        {isPaused && (
          <span
            className="mono"
            style={{ color:'#FFAA00', fontSize:9, marginRight:12,
                     border:'1px solid #FFAA00', padding:'2px 6px', borderRadius:3 }}
          >
            ⏸ PAUSED
          </span>
        )}

        {/* UTC Clock */}
        <span
          className="mono"
          style={{ color: '#E0E0F0', fontSize: 11, marginRight: 16, letterSpacing: 1, minWidth: 120, textAlign: 'right' }}
        >
          {utc}<span style={{color:'#666688'}}>.{String(ms).padStart(3,'0')}</span> UTC
        </span>

        {/* PAPER/LIVE toggle */}
        <button
          className="mono"
          onClick={handleLiveToggle}
          style={{
            background: isLive ? 'rgba(255,34,68,0.15)' : 'rgba(0,136,255,0.1)',
            border: `1px solid ${isLive ? '#FF2244' : '#0088FF'}`,
            color: isLive ? '#FF2244' : '#0088FF',
            fontSize: 10, fontWeight: 700,
            padding: '3px 14px', borderRadius: 3, cursor: 'pointer',
            transition: 'all 0.2s',
          }}
        >
          {isLive ? '⚡ LIVE' : '● PAPER'}
        </button>
      </header>

      {showConfirm && (
        <ConfirmModal
          onConfirm={() => { toggleLive(); setShowConfirm(false) }}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </>
  )
}

function VSep() {
  return <div style={{ width: 1, height: 24, background: '#1A1A3E', marginRight: 0 }} />
}

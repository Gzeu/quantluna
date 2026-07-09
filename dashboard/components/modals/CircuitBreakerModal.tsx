'use client'
import { useQuantLunaStore } from '../../store/quantlunaStore'
import { ModalWrap } from './OrderManagerModal'

export default function CircuitBreakerModal() {
  const { regime, setModal, addLog } = useQuantLunaStore()
  const cbOpen = regime?.cbOpen ?? false
  const cd     = regime?.cbCountdown ?? 0

  const handleReset = () => {
    addLog({
      ts:     new Date().toISOString().slice(11,23),
      level:  'RISK',
      module: 'CIRCUIT_B',
      msg:    'MANUAL RESET triggered from Dashboard UI',
    })
    setModal(null)
  }

  return (
    <ModalWrap title="CIRCUIT BREAKER STATUS  [F2]" onClose={() => setModal(null)}>
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-4">
          <div
            style={{
              width: 48, height: 48, borderRadius: '50%',
              background: cbOpen ? 'rgba(255,34,68,0.15)' : 'rgba(0,255,136,0.15)',
              border: `2px solid ${cbOpen ? '#FF2244' : '#00FF88'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 22,
              boxShadow: `0 0 12px ${cbOpen ? '#FF224460' : '#00FF8860'}`,
            }}
          >
            {cbOpen ? '✗' : '✓'}
          </div>
          <div>
            <div className="mono" style={{ fontSize:14, fontWeight:700, color: cbOpen ? '#FF2244' : '#00FF88' }}>
              {cbOpen ? 'OPEN' : 'CLOSED'}
            </div>
            {cbOpen && (
              <div className="mono" style={{ fontSize:10, color:'#FFAA00' }}>Reopen in {cd}s</div>
            )}
          </div>
        </div>

        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, fontSize:10 }}>
          <StatRow label="Daily Drawdown" value="-1.2%" color="#FFAA00" />
          <StatRow label="DD Limit"       value="-5.0%"  color="#FF2244" />
          <StatRow label="Max Position"   value="$26,637" color="#E0E0F0" />
          <StatRow label="Trades Today"   value="24"      color="#0088FF" />
        </div>

        <div style={{ borderTop:'1px solid #1A1A3E', paddingTop:12, display:'flex', gap:8 }}>
          <button
            className="ql-btn ql-btn-red flex-1"
            onClick={handleReset}
            style={{ padding:'8px', fontSize:11 }}
          >
            ↺ RESET CIRCUIT BREAKER
          </button>
          <button className="ql-btn flex-1" onClick={() => setModal(null)}
            style={{ padding:'8px', fontSize:11 }}>Close</button>
        </div>
      </div>
    </ModalWrap>
  )
}

function StatRow({ label, value, color }: { label:string; value:string; color:string }) {
  return (
    <div style={{ background:'#08080F', borderRadius:3, padding:'6px 10px',
      border:'1px solid #1A1A3E', display:'flex', flexDirection:'column', gap:2 }}>
      <span style={{ color:'#666688', fontSize:8 }}>{label}</span>
      <span className="mono" style={{ color, fontWeight:700 }}>{value}</span>
    </div>
  )
}

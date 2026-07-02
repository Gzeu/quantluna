'use client'
import { useState } from 'react'
import { useQuantLunaStore } from '../../store/quantlunaStore'

export default function OrderManagerModal() {
  const setModal = useQuantLunaStore(s => s.setModal)
  const addLog   = useQuantLunaStore(s => s.addLog)
  const [pair, setPair]   = useState('BTC/USDT')
  const [side, setSide]   = useState<'BUY'|'SELL'>('BUY')
  const [qty,  setQty]    = useState('0.01')
  const [price, setPrice] = useState('67820')
  const [exch, setExch]   = useState('Bybit')
  const [confirm, setConfirm] = useState(false)

  const handleSubmit = () => {
    if (!confirm) { setConfirm(true); return }
    addLog({
      ts:     new Date().toISOString().slice(11,23),
      level:  side as any,
      module: 'ORDER_MGR',
      msg:    `MANUAL ${side} ${qty} ${pair} @ $${price} via ${exch}`,
    })
    setModal(null)
  }

  return (
    <ModalWrap title="ORDER MANAGER  [F1]" onClose={() => setModal(null)}>
      <div className="flex flex-col gap-3">
        <Row label="Pair">
          <select className="ql-input flex-1" value={pair} onChange={e => setPair(e.target.value)}
            style={{ background: '#08080F', color: '#E0E0F0' }}>
            {['BTC/USDT','ETH/USDT','SOL/USDT','BNB/USDT','XRP/USDT','BTC/ETH','SOL/BNB'].map(p =>
              <option key={p}>{p}</option>)}
          </select>
        </Row>
        <Row label="Side">
          <div className="flex gap-2 flex-1">
            <button
              className="ql-btn flex-1"
              onClick={() => setSide('BUY')}
              style={side==='BUY' ? { background:'rgba(0,255,136,0.2)', borderColor:'#00FF88', color:'#00FF88', fontWeight:700 } : {}}
            >BUY</button>
            <button
              className="ql-btn flex-1"
              onClick={() => setSide('SELL')}
              style={side==='SELL' ? { background:'rgba(255,34,68,0.2)', borderColor:'#FF2244', color:'#FF2244', fontWeight:700 } : {}}
            >SELL</button>
          </div>
        </Row>
        <Row label="Qty">
          <input type="number" className="ql-input flex-1" value={qty}
            onChange={e => setQty(e.target.value)} step="0.001" min="0.001" />
        </Row>
        <Row label="Price">
          <input type="number" className="ql-input flex-1" value={price}
            onChange={e => setPrice(e.target.value)} step="0.01" />
        </Row>
        <Row label="Exchange">
          <select className="ql-input flex-1" value={exch} onChange={e => setExch(e.target.value)}
            style={{ background: '#08080F', color: '#E0E0F0' }}>
            {['Bybit','Binance','OKX'].map(x => <option key={x}>{x}</option>)}
          </select>
        </Row>

        {confirm && (
          <div style={{ background:'rgba(255,34,68,0.1)', border:'1px solid #FF2244',
            borderRadius:3, padding:'8px 12px', fontSize:10, color:'#FF2244' }}>
            Confirm: {side} {qty} {pair} @ ${price} on {exch}?
          </div>
        )}

        <button
          onClick={handleSubmit}
          className="ql-btn ql-btn-green"
          style={{ fontSize:11, padding:'8px', marginTop:4 }}
        >
          {confirm ? '✓ CONFIRMA ORDINUL' : '▶ SUBMIT ORDER'}
        </button>
        {confirm && (
          <button className="ql-btn" onClick={() => setConfirm(false)}
            style={{ fontSize:10, padding:'5px' }}>Anuleaza</button>
        )}
      </div>
    </ModalWrap>
  )
}

function Row({ label, children }: { label:string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span style={{ color:'#666688', fontSize:10, minWidth:60 }}>{label}</span>
      <div className="flex flex-1">{children}</div>
    </div>
  )
}

export function ModalWrap({ title, onClose, children }: {
  title: string; onClose: ()=>void; children: React.ReactNode
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.75)' }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div className="ql-panel" style={{ minWidth: 380, maxWidth: 480, boxShadow: '0 0 40px rgba(0,136,255,0.2)' }}>
        <div className="ql-panel-title flex items-center justify-between">
          <span>{title}</span>
          <button onClick={onClose} style={{ color:'#666688', fontSize:14, background:'none', border:'none', cursor:'pointer' }}>×</button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

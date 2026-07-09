'use client'
import { useQuantLunaStore } from '../../store/quantlunaStore'
import OrderManagerModal    from './OrderManagerModal'
import CircuitBreakerModal  from './CircuitBreakerModal'
import { ModalWrap }        from './OrderManagerModal'

export default function ModalsHost() {
  const { activeModal, setModal } = useQuantLunaStore()

  if (!activeModal) return null

  if (activeModal === 'order')          return <OrderManagerModal />
  if (activeModal === 'circuitbreaker') return <CircuitBreakerModal />

  if (activeModal === 'backtest') return (
    <ModalWrap title="BACKTEST QUICK LAUNCH  [F3]" onClose={() => setModal(null)}>
      <div style={{ color:'#666688', fontSize:10, lineHeight:1.8 }}>
        <p>Pair: <select style={{background:'#08080F',color:'#E0E0F0',border:'1px solid #1A1A3E',padding:'2px 6px'}}>
          <option>BTC/ETH</option><option>SOL/BNB</option><option>XRP/ADA</option>
        </select></p>
        <p>Start: <input type="date" className="ql-input" style={{marginLeft:4}} /></p>
        <p>End:   <input type="date" className="ql-input" style={{marginLeft:4}} /></p>
        <p style={{color:'#666688',fontSize:9,marginTop:8}}>Connects to: backtest/ module via REST /api/backtest</p>
        <button className="ql-btn ql-btn-green" style={{marginTop:12,padding:'6px 20px'}}>▶ RUN BACKTEST</button>
      </div>
    </ModalWrap>
  )

  if (activeModal === 'config') return (
    <ModalWrap title="CONFIG EDITOR  [F4]" onClose={() => setModal(null)}>
      <div style={{ color:'#666688', fontSize:10 }}>
        <p style={{color:'#00FF88',marginBottom:8}}>config/settings.toml</p>
        <textarea
          style={{
            width:'100%', height:200,
            background:'#08080F', border:'1px solid #1A1A3E',
            color:'#E0E0F0', fontFamily:'JetBrains Mono',
            fontSize:10, padding:8, borderRadius:3, resize:'vertical',
          }}
          defaultValue={`[strategy]\nz_entry_threshold = 2.0\nz_exit_threshold = 0.5\nhalf_life_min = 5\nhalf_life_max = 72\n\n[risk]\nmax_daily_drawdown = -0.05\nmax_position_size = 0.1\ncircuit_breaker_cooldown = 30\n\n[exchanges]\nprimary = "bybit"\nfallback = ["binance", "okx"]`}
        />
        <div style={{display:'flex',gap:8,marginTop:12}}>
          <button className="ql-btn ql-btn-green" style={{flex:1,padding:'6px'}}>✓ SAVE (no restart)</button>
          <button className="ql-btn" style={{flex:1,padding:'6px'}} onClick={() => setModal(null)}>Cancel</button>
        </div>
      </div>
    </ModalWrap>
  )

  if (activeModal === 'notifier') return (
    <ModalWrap title="NOTIFIER TEST  [F5]" onClose={() => setModal(null)}>
      <div style={{ color:'#666688', fontSize:10, display:'flex', flexDirection:'column', gap:12 }}>
        <p>Test notifier via <span style={{color:'#0088FF'}}>notifications/notifier_bus.py</span></p>
        <input type="text" className="ql-input" defaultValue="QuantLuna test message from Dashboard" style={{width:'100%'}} />
        <div style={{display:'flex',gap:8}}>
          <button className="ql-btn ql-btn-green" style={{flex:1,padding:'6px'}}>Telegram</button>
          <button className="ql-btn" style={{flex:1,padding:'6px',borderColor:'#8844FF',color:'#8844FF'}}>Slack</button>
          <button className="ql-btn" style={{flex:1,padding:'6px'}} onClick={() => setModal(null)}>Cancel</button>
        </div>
      </div>
    </ModalWrap>
  )

  return null
}

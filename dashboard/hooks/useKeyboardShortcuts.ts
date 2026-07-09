'use client'
import { useEffect } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'

export function useKeyboardShortcuts() {
  const store = useQuantLunaStore()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // F1-F5: modals
      if (e.key === 'F1') { e.preventDefault(); store.setModal('order') }
      if (e.key === 'F2') { e.preventDefault(); store.setModal('circuitbreaker') }
      if (e.key === 'F3') { e.preventDefault(); store.setModal('backtest') }
      if (e.key === 'F4') { e.preventDefault(); store.setModal('config') }
      if (e.key === 'F5') { e.preventDefault(); store.setModal('notifier') }

      // Ctrl combos
      if (e.ctrlKey && e.key === 'p') { e.preventDefault(); store.togglePause() }
      if (e.ctrlKey && e.key === 'e') {
        e.preventDefault()
        const state = useQuantLunaStore.getState()
        const csv = [
          'ts,level,module,msg',
          ...state.logs.map(l =>
            `${l.ts},${l.level},${l.module},"${l.msg.replace(/"/g,'""')}"`
          )
        ].join('\n')
        const a = document.createElement('a')
        a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
        a.download = `quantluna_log_${Date.now()}.csv`
        a.click()
      }
      if (e.ctrlKey && e.key === 'r') {
        e.preventDefault()
        store.addLog({
          ts: new Date().toISOString().slice(11,23),
          level: 'SYS', module: 'DASHBOARD',
          msg: 'Force refresh triggered via Ctrl+R',
        })
      }

      // Escape: dismiss modal
      if (e.key === 'Escape') store.setModal(null)

      // Ctrl+1..6: focus panels (could scroll-to in future)
      if (e.ctrlKey && ['1','2','3','4','5','6'].includes(e.key)) {
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [store])
}

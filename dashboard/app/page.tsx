'use client'
import { useEffect } from 'react'
import HeaderBar         from '../components/HeaderBar'
import SidebarPanel      from '../components/SidebarPanel'
import CandlestickChart  from '../components/CandlestickChart'
import SpreadMonitorPanel from '../components/SpreadMonitorPanel'
import MarketHeatmap     from '../components/MarketHeatmap'
import BalanceTracker    from '../components/BalanceTracker'
import PnLChart          from '../components/PnLChart'
import ArbitragePanel    from '../components/ArbitragePanel'
import ExecutionLog      from '../components/ExecutionLog'
import ModalsHost        from '../components/modals/ModalsHost'
import { useQuantLunaWS }          from '../hooks/useQuantLunaWS'
import { useKeyboardShortcuts }    from '../hooks/useKeyboardShortcuts'

function QuantLunaApp() {
  useQuantLunaWS()
  useKeyboardShortcuts()
  return null
}

export default function DashboardPage() {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateRows: '40px 1fr 180px',
        gridTemplateColumns: '238px 1fr 320px',
        gridTemplateAreas: `
          "header  header  header"
          "sidebar central right"
          "log     log     log"
        `,
        height: '100vh',
        width:  '100vw',
        gap: 4,
        padding: 4,
        background: '#08080F',
        overflow: 'hidden',
      }}
    >
      <QuantLunaApp />

      {/* Header */}
      <div style={{ gridArea: 'header' }}>
        <HeaderBar />
      </div>

      {/* Sidebar */}
      <div style={{ gridArea: 'sidebar', overflow: 'hidden' }}>
        <SidebarPanel />
      </div>

      {/* Central zone: Chart + Spread + Heatmap */}
      <div
        style={{
          gridArea: 'central',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          overflow: 'hidden',
          minHeight: 0,
        }}
      >
        <div style={{ flex: '6 1 0', minHeight: 0 }}>
          <CandlestickChart />
        </div>
        <div style={{ flex: '2 1 0', minHeight: 0 }}>
          <SpreadMonitorPanel />
        </div>
        <div style={{ flex: '2 1 0', minHeight: 0 }}>
          <MarketHeatmap />
        </div>
      </div>

      {/* Right zone: Balance + PnL + Arb */}
      <div
        style={{
          gridArea: 'right',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          overflow: 'hidden',
          minHeight: 0,
        }}
      >
        <div style={{ flex: '3 1 0', minHeight: 0 }}>
          <BalanceTracker />
        </div>
        <div style={{ flex: '3 1 0', minHeight: 0 }}>
          <PnLChart />
        </div>
        <div style={{ flex: '4 1 0', minHeight: 0 }}>
          <ArbitragePanel />
        </div>
      </div>

      {/* Execution Log */}
      <div style={{ gridArea: 'log' }}>
        <ExecutionLog />
      </div>

      {/* Modals overlay */}
      <ModalsHost />
    </div>
  )
}

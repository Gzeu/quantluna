'use client'
import { useEffect, useRef, useCallback, useState } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'
import type { LogLevel } from '../store/quantlunaStore'

const LEVEL_COLORS: Record<LogLevel, string> = {
  INFO:  '#0088FF',
  BUY:   '#00FF88',
  SELL:  '#FF2244',
  WARN:  '#FFAA00',
  ARB:   '#FF00AA',
  ERROR: '#FF0000',
  RISK:  '#FF6600',
  SYS:   '#666688',
}

const ALL_LEVELS: LogLevel[] = ['INFO','BUY','SELL','WARN','ARB','ERROR','RISK','SYS']

export default function ExecutionLog() {
  const {
    logs, logFilters, logSearch, logAutoScroll,
    toggleLogFilter, setLogSearch, setLogAutoScroll, clearLogs,
  } = useQuantLunaStore()

  const scrollRef      = useRef<HTMLDivElement>(null)
  const prevLogsLen    = useRef(0)
  const userScrolled   = useRef(false)

  const exportCSV = useCallback(() => {
    const csv = [
      'ts,level,module,msg',
      ...logs.map(l => `${l.ts},${l.level},${l.module},"${l.msg.replace(/"/g, '""')}"`)
    ].join('\n')
    const a   = document.createElement('a')
    a.href    = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    a.download = `quantluna_log_${Date.now()}.csv`
    a.click()
  }, [logs])

  // Auto-scroll
  useEffect(() => {
    if (!logAutoScroll || !scrollRef.current) return
    if (logs.length !== prevLogsLen.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      prevLogsLen.current = logs.length
    }
  }, [logs, logAutoScroll])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20
    if (!atBottom && logAutoScroll) {
      setLogAutoScroll(false)
      userScrolled.current = true
    } else if (atBottom && !logAutoScroll) {
      setLogAutoScroll(true)
      userScrolled.current = false
    }
  }

  // Filter + search
  const visible = logs.filter(l =>
    logFilters.has(l.level as LogLevel) &&
    (logSearch === '' ||
      l.msg.toLowerCase().includes(logSearch.toLowerCase()) ||
      l.module.toLowerCase().includes(logSearch.toLowerCase()))
  )

  return (
    <div
      className="ql-panel flex flex-col shrink-0"
      style={{ height: 180, borderTop: '1px solid #1A1A3E' }}
    >
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 shrink-0"
        style={{ height: 30, borderBottom: '1px solid #1A1A3E', flexWrap: 'nowrap', overflow: 'hidden' }}
      >
        <span className="mono" style={{ color: '#00FF88', fontSize: 10, fontWeight: 700, letterSpacing: 2, marginRight: 4, whiteSpace: 'nowrap' }}>
          EXECUTION LOG
        </span>
        <div style={{ width: 1, height: 16, background: '#1A1A3E', marginRight: 4 }} />

        {/* Level filter checkboxes */}
        {ALL_LEVELS.map(lv => (
          <label
            key={lv}
            className="flex items-center gap-1"
            style={{ cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            <input
              type="checkbox"
              checked={logFilters.has(lv)}
              onChange={() => toggleLogFilter(lv)}
              style={{ accentColor: LEVEL_COLORS[lv], width: 10, height: 10 }}
            />
            <span style={{ color: LEVEL_COLORS[lv], fontSize: 9 }}>{lv}</span>
          </label>
        ))}

        <div style={{ width: 1, height: 16, background: '#1A1A3E', marginRight: 4 }} />

        <input
          type="text"
          placeholder="search..."
          value={logSearch}
          onChange={e => setLogSearch(e.target.value)}
          className="ql-input"
          style={{ width: 130, height: 20, flexShrink: 0 }}
        />

        <div className="flex-1" />

        {/* Auto-scroll toggle */}
        <button
          className="mono"
          onClick={() => setLogAutoScroll(!logAutoScroll)}
          title={logAutoScroll ? 'Pause auto-scroll' : 'Resume auto-scroll'}
          style={{
            background: logAutoScroll ? 'rgba(0,136,255,0.1)' : 'rgba(255,170,0,0.1)',
            border: `1px solid ${logAutoScroll ? '#0088FF' : '#FFAA00'}`,
            color: logAutoScroll ? '#0088FF' : '#FFAA00',
            fontSize: 9, padding: '1px 8px', borderRadius: 2, cursor: 'pointer',
            whiteSpace: 'nowrap', flexShrink: 0,
          }}
        >
          {logAutoScroll ? '▼ AUTO' : '⏸ PAUSED'}
        </button>

        <button className="ql-btn" onClick={exportCSV} style={{ whiteSpace: 'nowrap', flexShrink: 0, height: 20, fontSize: 9 }}>
          📥 CSV
        </button>

        <button className="ql-btn ql-btn-red" onClick={clearLogs} style={{ whiteSpace: 'nowrap', flexShrink: 0, height: 20, fontSize: 9 }}>
          CLEAR
        </button>
      </div>

      {/* Log content - virtualized by slicing last 300 visible */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-3 py-1"
        style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 9.5, lineHeight: 1.5 }}
      >
        {visible.slice(-300).map(entry => {
          const isCritical = entry.msg.includes('CIRCUIT BREAKER') || entry.msg.includes('ORPHAN')
          const col = LEVEL_COLORS[entry.level as LogLevel] ?? '#666688'
          return (
            <div
              key={entry.id}
              className="log-row-enter flex gap-2"
              style={{
                background: isCritical ? 'rgba(255,34,68,0.12)' : 'transparent',
                borderLeft: isCritical ? '2px solid #FF2244' : '2px solid transparent',
                paddingLeft: 4,
              }}
            >
              <span style={{ color: '#444466', flexShrink: 0 }}>[{entry.ts}]</span>
              <span style={{ color: col, fontWeight: 700, flexShrink: 0, minWidth: 40 }}>
                [{entry.level.padEnd(5)}]
              </span>
              <span style={{ color: '#666688', flexShrink: 0, minWidth: 80 }}>
                [{entry.module.padEnd(9)}]
              </span>
              <span style={{ color: isCritical ? '#FF2244' : '#E0E0F0' }}>
                {entry.msg}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

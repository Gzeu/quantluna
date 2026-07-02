'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTradingStore } from '../hooks/useTradingStore'
import type { LogLevel, LogEntry } from '../types'
import { format } from 'date-fns'

const LEVEL_COLORS: Record<LogLevel, string> = {
  INFO:  'text-text-muted',
  BUY:   'text-neon-green font-semibold',
  SELL:  'text-alert-danger font-semibold',
  WARN:  'text-alert-warn',
  ARB:   'text-neon-magenta font-semibold',
  ERROR: 'text-alert-danger font-bold',
  RISK:  'text-alert-warn font-bold',
  SYS:   'text-neon-blue',
}

const ALL_LEVELS: LogLevel[] = ['INFO', 'BUY', 'SELL', 'WARN', 'ARB', 'ERROR', 'RISK', 'SYS']

function formatTs(ts: number): string {
  return format(new Date(ts), 'HH:mm:ss.SSS')
}

export default function ExecutionLog() {
  const { logEntries, clearLog } = useTradingStore()

  const [search, setSearch] = useState('')
  const [activeLevels, setActiveLevels] = useState<Set<LogLevel>>(new Set(ALL_LEVELS))
  const [autoScroll, setAutoScroll] = useState(true)

  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const userScrollingRef = useRef(false)

  const toggleLevel = (level: LogLevel) => {
    setActiveLevels((prev) => {
      const next = new Set(prev)
      if (next.has(level)) next.delete(level)
      else next.add(level)
      return next
    })
  }

  const filtered: LogEntry[] = useMemo(() => {
    const q = search.toLowerCase()
    return logEntries.filter(
      (e) =>
        activeLevels.has(e.level) &&
        (q === '' || e.message.toLowerCase().includes(q) || e.module.toLowerCase().includes(q)),
    )
  }, [logEntries, activeLevels, search])

  // Auto-scroll to bottom
  useEffect(() => {
    if (autoScroll && !userScrollingRef.current && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [filtered, autoScroll])

  // Detect manual scroll
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    userScrollingRef.current = !atBottom
    if (atBottom) setAutoScroll(true)
  }, [])

  // CSV export
  const exportCsv = useCallback(() => {
    const header = 'timestamp,level,module,message'
    const rows = filtered.map(
      (e) =>
        `${formatTs(e.ts)},${e.level},${JSON.stringify(e.module)},${JSON.stringify(e.message)}`,
    )
    const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `quantluna-log-${Date.now()}.csv`
    a.click()
  }, [filtered])

  // Ctrl+E shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 'e') { e.preventDefault(); exportCsv() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [exportCsv])

  return (
    <div className="flex h-full flex-col rounded-lg border border-bg-border bg-bg-panel overflow-hidden">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-bg-border px-3 py-1.5">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search log…"
          className="h-6 w-36 rounded bg-bg-primary px-2 font-mono text-xs text-text-primary placeholder:text-text-muted border border-bg-border focus:outline-none focus:border-neon-blue"
        />
        <div className="flex flex-wrap gap-1">
          {ALL_LEVELS.map((lvl) => (
            <button
              key={lvl}
              onClick={() => toggleLevel(lvl)}
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold border transition-opacity ${
                activeLevels.has(lvl) ? 'opacity-100' : 'opacity-30'
              } ${LEVEL_COLORS[lvl]} border-current`}
            >
              {lvl}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setAutoScroll((v) => !v)}
            className={`font-mono text-[10px] px-2 py-0.5 rounded border ${
              autoScroll ? 'border-neon-green text-neon-green' : 'border-bg-border text-text-muted'
            }`}
          >
            AUTO-SCROLL
          </button>
          <button
            onClick={exportCsv}
            className="font-mono text-[10px] px-2 py-0.5 rounded border border-neon-blue text-neon-blue hover:bg-neon-blue/10"
          >
            EXPORT CSV
          </button>
          <button
            onClick={clearLog}
            className="font-mono text-[10px] px-2 py-0.5 rounded border border-alert-warn text-alert-warn hover:bg-alert-warn/10"
          >
            CLEAR
          </button>
        </div>
      </div>

      {/* Log entries */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto font-mono text-[11px] leading-5"
      >
        {filtered.length === 0 ? (
          <p className="px-4 py-6 text-center text-text-muted">No entries match current filter</p>
        ) : (
          filtered.map((entry, idx) => {
            const highlight =
              entry.message.includes('CIRCUIT_BREAKER') || entry.message.includes('ORPHAN')
            return (
              <div
                key={idx}
                className={`flex gap-2 px-3 py-0.5 hover:bg-white/5 ${
                  highlight ? 'bg-alert-danger/20' : ''
                }`}
              >
                <span className="shrink-0 text-text-muted">{formatTs(entry.ts)}</span>
                <span className={`shrink-0 w-12 ${LEVEL_COLORS[entry.level]}`}>
                  [{entry.level}]
                </span>
                <span className="shrink-0 text-neon-blue w-24 truncate">[{entry.module}]</span>
                <span className="text-text-primary break-all">{entry.message}</span>
              </div>
            )
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

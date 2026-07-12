'use client'
/**
 * SizingPanel.tsx — QuantLuna Dashboard S46
 *
 * Afiseaza status live pentru:
 *   - SizingEngine v2.5  (GET /sizing/live_status)
 *   - DecisionEngine v2.5 (GET /api/decision/status)
 *
 * Polling 5s. Graceful fallback cand engine-ul nu e inca injectat.
 */
import { useEffect, useState, useCallback } from 'react'
import { getSizingLiveStatus, getDecisionStatus } from '../lib/api'
import type { SizingLiveStatus, DecisionStatus } from '../lib/api'

const POLL_MS = 5_000

const C = {
  bg:      '#0E0E1A',
  border:  '#1E2235',
  label:   '#6B7280',
  value:   '#E5E7EB',
  green:   '#22C55E',
  red:     '#EF4444',
  yellow:  '#F59E0B',
  cyan:    '#06B6D4',
  muted:   '#374151',
  header:  '#9CA3AF',
} as const

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        padding: '2px 6px',
        borderRadius: 4,
        background: ok ? '#14532D' : '#450A0A',
        color: ok ? C.green : C.red,
        letterSpacing: '0.05em',
        textTransform: 'uppercase',
      }}
    >
      {label}
    </span>
  )
}

function Row({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '3px 0' }}>
      <span style={{ fontSize: 11, color: C.label }}>{label}</span>
      <span style={{ fontSize: 11, fontFamily: 'monospace', color: color ?? C.value }}>{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 10, fontWeight: 600, color: C.header, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 }}>
        {title}
      </div>
      {children}
    </div>
  )
}

export default function SizingPanel() {
  const [sizing,   setSizing]   = useState<SizingLiveStatus | null>(null)
  const [decision, setDecision] = useState<DecisionStatus | null>(null)
  const [error,    setError]    = useState<string | null>(null)

  const poll = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([
        getSizingLiveStatus(),
        getDecisionStatus(),
      ])
      setSizing(s)
      setDecision(d)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Eroare fetch')
    }
  }, [])

  useEffect(() => {
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => clearInterval(id)
  }, [poll])

  const fmt = (v: number | null | undefined, dec = 4) =>
    v == null ? '—' : v.toFixed(dec)

  return (
    <div
      style={{
        background: C.bg,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        padding: '10px 12px',
        height: '100%',
        overflow: 'auto',
        boxSizing: 'border-box',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: C.cyan, letterSpacing: '0.05em' }}>
          SIZING · DECISION
        </span>
        {error ? (
          <Badge ok={false} label="err" />
        ) : sizing?.enabled ? (
          <Badge ok={true} label="live" />
        ) : (
          <Badge ok={false} label="off" />
        )}
      </div>

      {error && (
        <div style={{ fontSize: 10, color: C.red, marginBottom: 8 }}>{error}</div>
      )}

      {/* SizingEngine */}
      <Section title="SizingEngine v2.5">
        {!sizing?.enabled ? (
          <div style={{ fontSize: 10, color: C.muted }}>
            {sizing?.status ?? 'Asteptam engine-ul...'}
          </div>
        ) : (
          <>
            <Row label="Capital"      value={sizing.capital_usdt != null ? `$${sizing.capital_usdt.toLocaleString()}` : fmt(sizing.capital_usdt as number)} />
            <Row label="Leverage max" value={sizing.max_leverage != null ? `${sizing.max_leverage}x` : '—'} />
            <Row label="Kelly scale"  value={sizing.kelly_fraction ?? '—'} />
            <Row
              label="Streak"
              value={sizing.current_streak ?? '—'}
              color={(sizing.current_streak ?? 0) < 0 ? C.red : C.green}
            />
            <Row
              label="Drawdown"
              value={sizing.current_drawdown != null ? `${(sizing.current_drawdown * 100).toFixed(1)}%` : '—'}
              color={(sizing.current_drawdown ?? 0) > 0.05 ? C.red : C.green}
            />
            {sizing.last_multiplier != null && (
              <Row label="Multiplier" value={`${sizing.last_multiplier.toFixed(2)}x`} color={C.yellow} />
            )}
            {sizing.kelly_cap != null && (
              <Row label="Kelly cap" value={`${(sizing.kelly_cap * 100).toFixed(1)}%`} />
            )}
          </>
        )}
      </Section>

      <div style={{ borderTop: `1px solid ${C.border}`, margin: '6px 0' }} />

      {/* DecisionEngine */}
      <Section title="DecisionEngine v2.5">
        {!decision?.enabled ? (
          <div style={{ fontSize: 10, color: C.muted }}>
            {decision?.status ?? 'Asteptam engine-ul...'}
          </div>
        ) : (
          <>
            <Row
              label="In pozitie"
              value={decision.in_position ? 'DA' : 'NU'}
              color={decision.in_position ? C.green : C.muted}
            />
            <Row label="Entry z"        value={fmt(decision.entry_zscore)} />
            <Row label="Exit z"         value={fmt(decision.exit_zscore)} />
            <Row label="Partial exit z" value={fmt(decision.partial_exit_zscore)} />
            <Row label="Scale-in z"     value={fmt(decision.scale_in_zscore)} />
            <Row label="Qty Y"          value={fmt(decision.base_qty_y, 6)} />
            <Row label="Qty X"          value={fmt(decision.base_qty_x, 6)} />
            <Row
              label="Streak"
              value={decision.current_streak ?? 0}
              color={(decision.current_streak ?? 0) < 0 ? C.red : C.green}
            />
            <Row
              label="Drawdown"
              value={decision.current_drawdown != null ? `${(decision.current_drawdown * 100).toFixed(1)}%` : '—'}
              color={(decision.current_drawdown ?? 0) > 0.05 ? C.red : C.green}
            />
          </>
        )}
      </Section>
    </div>
  )
}

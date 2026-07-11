/**
 * dashboard/pages/watchdog.tsx  -  QuantLuna Watchdog Dashboard v1.0
 * Sprint S45 (2026-07-12)
 *
 * Sectiuni:
 *   1. STATUS CARD    – running, check count, last check, perechi monitorizate
 *   2. THRESHOLDS     – tabel per pereche cu edit inline, Save / Silence / Test
 *   3. ALERTS FEED    – stream live alerte colorate pe severitate, filtru
 */

import React, {
  useCallback, useEffect, useRef, useState,
} from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ── Types ────────────────────────────────────────────────────────────────────

interface WatchdogStatus {
  running:       boolean;
  check_count:   number;
  last_check:    string | null;
  pairs_count:   number;
  alerts_total:  number;
  recent_alerts: WatchdogAlert[];
}

interface PairThreshold {
  sharpe_min:    number;
  max_drawdown:  number;
  z_max:         number;
  hl_max:        number;
  loss_streak:   number;
  action:        string;
  silenced_until: string | null;
}

interface WatchdogAlert {
  timestamp:  string;
  pair:       string;
  metric:     string;
  value:      number;
  threshold:  number;
  action:     string;
  severity:   string;
  message:    string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const SEV_STYLE: Record<string, { bg: string; color: string; emoji: string }> = {
  INFO:     { bg: '#1a1a2e', color: '#8b5cf6', emoji: 'ℹ️' },
  WARNING:  { bg: '#1a1400', color: '#fbbf24', emoji: '⚠️' },
  CRITICAL: { bg: '#2a0000', color: '#f87171', emoji: '🚨' },
};

const ACT_STYLE: Record<string, { color: string; label: string }> = {
  ALERT_ONLY:  { color: '#8b5cf6', label: '🔔 Alert' },
  REDUCE_SIZE: { color: '#fbbf24', label: '↓ Reduce' },
  HALT:        { color: '#f87171', label: '🛑 Halt' },
};

function fmt(d: string | null): string {
  if (!d) return '—';
  return new Date(d).toLocaleString('ro-RO', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function silenceCountdown(until: string | null): string | null {
  if (!until) return null;
  const diff = new Date(until).getTime() - Date.now();
  if (diff <= 0) return null;
  const m = Math.ceil(diff / 60000);
  return `Silentat ${m}min`;
}

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
    throw new Error(err.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// ── Status Card ──────────────────────────────────────────────────────────────

function StatusCard({ status }: { status: WatchdogStatus | null }) {
  if (!status) return (
    <div style={{
      background: '#1a1a2e', borderRadius: 12, padding: '16px 24px',
      marginBottom: 24, border: '1px solid #2a2a4a', color: '#555',
    }}>Se încarcă...</div>
  );

  return (
    <div style={{
      background: '#1a1a2e', borderRadius: 12,
      padding: '16px 24px', marginBottom: 24,
      border: `1px solid ${status.running ? '#1a2a1a' : '#2a2a4a'}`,
      display: 'flex', gap: 32, flexWrap: 'wrap', alignItems: 'center',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          width: 10, height: 10, borderRadius: '50%',
          background: status.running ? '#4ade80' : '#3a3a5a',
          display: 'inline-block',
          boxShadow: status.running ? '0 0 8px #4ade80' : 'none',
          animation: status.running ? 'pulse 1.5s infinite' : 'none',
        }} />
        <span style={{
          fontWeight: 700, fontSize: 15,
          color: status.running ? '#4ade80' : '#888',
        }}>
          {status.running ? 'Watchdog activ' : 'Watchdog oprit'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 24, fontSize: 13, flexWrap: 'wrap' }}>
        <div>
          <span style={{ color: '#555' }}>Perechi: </span>
          <span style={{ color: '#ccc', fontWeight: 600 }}>{status.pairs_count}</span>
        </div>
        <div>
          <span style={{ color: '#555' }}>Verificari: </span>
          <span style={{ color: '#ccc', fontWeight: 600 }}>{status.check_count}</span>
        </div>
        <div>
          <span style={{ color: '#555' }}>Alerte totale: </span>
          <span style={{
            color: status.alerts_total > 0 ? '#fbbf24' : '#ccc',
            fontWeight: 600,
          }}>{status.alerts_total}</span>
        </div>
        <div>
          <span style={{ color: '#555' }}>Ultima verificare: </span>
          <span style={{ color: '#aaa' }}>{fmt(status.last_check)}</span>
        </div>
      </div>
    </div>
  );
}

// ── Thresholds Table ───────────────────────────────────────────────────────────

interface EditState {
  sharpe_min:   string;
  max_drawdown: string;
  z_max:        string;
  hl_max:       string;
  loss_streak:  string;
  action:       string;
}

function ThresholdsTable({
  thresholds,
  onSave,
  onSilence,
  onUnsilence,
  onTest,
}: {
  thresholds: Record<string, PairThreshold>;
  onSave:      (pair: string, updates: Partial<PairThreshold>) => Promise<void>;
  onSilence:   (pair: string, minutes: number) => Promise<void>;
  onUnsilence: (pair: string) => Promise<void>;
  onTest:      (pair: string) => Promise<void>;
}) {
  const pairs = Object.keys(thresholds);
  const [editMap, setEditMap] = useState<Record<string, EditState>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  // Initializeaza editMap cand thresholds se schimba
  useEffect(() => {
    const init: Record<string, EditState> = {};
    for (const [pair, t] of Object.entries(thresholds)) {
      if (!editMap[pair]) {
        init[pair] = {
          sharpe_min:   String(t.sharpe_min),
          max_drawdown: String(t.max_drawdown),
          z_max:        String(t.z_max),
          hl_max:       String(t.hl_max),
          loss_streak:  String(t.loss_streak),
          action:       t.action,
        };
      }
    }
    if (Object.keys(init).length > 0)
      setEditMap(prev => ({ ...prev, ...init }));
  }, [thresholds]);

  const setField = (pair: string, field: keyof EditState, val: string) => {
    setEditMap(prev => ({ ...prev, [pair]: { ...prev[pair], [field]: val } }));
  };

  const handleSave = async (pair: string) => {
    const e = editMap[pair];
    if (!e) return;
    setSaving(prev => ({ ...prev, [pair]: true }));
    try {
      await onSave(pair, {
        sharpe_min:   parseFloat(e.sharpe_min),
        max_drawdown: parseFloat(e.max_drawdown),
        z_max:        parseFloat(e.z_max),
        hl_max:       parseFloat(e.hl_max),
        loss_streak:  parseInt(e.loss_streak, 10),
        action:       e.action,
      });
    } finally {
      setSaving(prev => ({ ...prev, [pair]: false }));
    }
  };

  if (pairs.length === 0) return (
    <div style={{ color: '#555', textAlign: 'center', padding: '32px 0' }}>
      Nicio pereche monitorizata.
    </div>
  );

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ color: '#555', fontSize: 11, textTransform: 'uppercase' }}>
            {['Pereche', 'Sharpe min', 'Max DD %', 'Z max', 'HL max (h)',
              'Loss streak', 'Actiune', 'Silence', ''].map(h => (
              <th key={h} style={{
                padding: '8px 10px', textAlign: 'left',
                borderBottom: '1px solid #1a1a2a', fontWeight: 600,
                whiteSpace: 'nowrap',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pairs.map(pair => {
            const thr = thresholds[pair];
            const e   = editMap[pair];
            const sc  = silenceCountdown(thr?.silenced_until ?? null);
            if (!e) return null;
            return (
              <tr
                key={pair}
                style={{ borderBottom: '1px solid #141420' }}
                onMouseEnter={el => (el.currentTarget.style.background = '#1a1a2e')}
                onMouseLeave={el => (el.currentTarget.style.background = 'transparent')}
              >
                {/* Pereche */}
                <td style={{ padding: '10px 10px', fontWeight: 700, color: '#ccc', whiteSpace: 'nowrap' }}>
                  {pair}
                  {sc && (
                    <div style={{
                      fontSize: 10, color: '#fbbf24',
                      background: '#1a1400', borderRadius: 3,
                      padding: '1px 5px', marginTop: 3, display: 'inline-block',
                    }}>{sc}</div>
                  )}
                </td>

                {/* Sharpe min */}
                <td style={{ padding: '8px 10px' }}>
                  <NumInput val={e.sharpe_min} step="0.1"
                    onChange={v => setField(pair, 'sharpe_min', v)} />
                </td>

                {/* Max DD */}
                <td style={{ padding: '8px 10px' }}>
                  <NumInput val={e.max_drawdown} step="0.01"
                    onChange={v => setField(pair, 'max_drawdown', v)} />
                </td>

                {/* Z max */}
                <td style={{ padding: '8px 10px' }}>
                  <NumInput val={e.z_max} step="0.5"
                    onChange={v => setField(pair, 'z_max', v)} />
                </td>

                {/* HL max */}
                <td style={{ padding: '8px 10px' }}>
                  <NumInput val={e.hl_max} step="12"
                    onChange={v => setField(pair, 'hl_max', v)} />
                </td>

                {/* Loss streak */}
                <td style={{ padding: '8px 10px' }}>
                  <NumInput val={e.loss_streak} step="1"
                    onChange={v => setField(pair, 'loss_streak', v)} />
                </td>

                {/* Actiune */}
                <td style={{ padding: '8px 10px' }}>
                  <select
                    value={e.action}
                    onChange={ev => setField(pair, 'action', ev.target.value)}
                    style={{
                      background: '#16162a', border: '1px solid #2a2a4a',
                      borderRadius: 5, color: ACT_STYLE[e.action]?.color ?? '#ccc',
                      padding: '4px 8px', fontSize: 12, cursor: 'pointer',
                    }}
                  >
                    {Object.entries(ACT_STYLE).map(([k, v]) => (
                      <option key={k} value={k}>{v.label}</option>
                    ))}
                  </select>
                </td>

                {/* Silence badge + control */}
                <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                  {sc ? (
                    <button
                      onClick={() => onUnsilence(pair)}
                      style={btnStyle('#1a1500', '#fbbf24')}
                    >▶ Activare</button>
                  ) : (
                    <button
                      onClick={() => onSilence(pair, 60)}
                      style={btnStyle('#16162a', '#555')}
                    >🔕 1h</button>
                  )}
                </td>

                {/* Actions */}
                <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      onClick={() => handleSave(pair)}
                      disabled={saving[pair]}
                      style={btnStyle('#0a2a0a', '#4ade80')}
                    >{saving[pair] ? '⏳' : '✓ Save'}</button>
                    <button
                      onClick={() => onTest(pair)}
                      style={btnStyle('#1a1a3a', '#8b5cf6')}
                    >🔔 Test</button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function NumInput({ val, step, onChange }: {
  val: string; step: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="number" value={val} step={step}
      onChange={e => onChange(e.target.value)}
      style={{
        width: 72, background: '#16162a',
        border: '1px solid #2a2a4a', borderRadius: 5,
        color: '#ccc', padding: '4px 6px', fontSize: 12,
      }}
    />
  );
}

function btnStyle(bg: string, color: string): React.CSSProperties {
  return {
    padding: '4px 10px', borderRadius: 5,
    background: bg, border: `1px solid ${color}`,
    color, fontSize: 11, cursor: 'pointer',
  };
}

// ── Alerts Feed ────────────────────────────────────────────────────────────────

function AlertsFeed({
  alerts,
  filter,
  onFilterChange,
}: {
  alerts: WatchdogAlert[];
  filter: string;
  onFilterChange: (f: string) => void;
}) {
  const filtered = filter === 'ALL'
    ? alerts
    : alerts.filter(a => a.severity === filter);

  return (
    <div>
      {/* Filtru */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: '#555' }}>Filtru:</span>
        {(['ALL', 'CRITICAL', 'WARNING', 'INFO'] as const).map(f => (
          <button
            key={f}
            onClick={() => onFilterChange(f)}
            style={{
              padding: '3px 10px', borderRadius: 5, fontSize: 11,
              background: filter === f ? '#1a1a3a' : 'transparent',
              border: `1px solid ${filter === f ? '#8b5cf6' : '#2a2a4a'}`,
              color: filter === f ? '#e0e0ff' : '#555',
              cursor: 'pointer',
            }}
          >{f === 'ALL' ? `Toate (${alerts.length})` : f}</button>
        ))}
      </div>

      {/* Feed */}
      {filtered.length === 0 ? (
        <div style={{
          color: '#555', textAlign: 'center', padding: '40px 0', fontSize: 13,
        }}>
          {filter === 'ALL' ? '✅ Nicio alertă înregistrată.' : `Nicio alertă ${filter}.`}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {filtered.map((a, i) => {
            const sty = SEV_STYLE[a.severity] ?? SEV_STYLE['INFO'];
            const act = ACT_STYLE[a.action] ?? { color: '#888', label: a.action };
            return (
              <div
                key={i}
                style={{
                  background: sty.bg,
                  border: `1px solid ${sty.color}22`,
                  borderLeft: `3px solid ${sty.color}`,
                  borderRadius: 7,
                  padding: '10px 14px',
                  display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start',
                }}
              >
                <span style={{ fontSize: 16, flexShrink: 0 }}>{sty.emoji}</span>
                <div style={{ flex: 1, minWidth: 200 }}>
                  <div style={{
                    display: 'flex', gap: 10, alignItems: 'center', marginBottom: 4,
                  }}>
                    <span style={{ fontWeight: 700, color: '#e0e0ff', fontSize: 13 }}>
                      {a.pair}
                    </span>
                    <span style={{
                      fontSize: 10, background: '#2a2a4a',
                      color: '#8b5cf6', borderRadius: 3, padding: '1px 6px',
                    }}>{a.metric}</span>
                    <span style={{
                      fontSize: 10, color: act.color,
                      background: '#1a1a2a', borderRadius: 3, padding: '1px 6px',
                    }}>{act.label}</span>
                    <span style={{ marginLeft: 'auto', fontSize: 11, color: '#555' }}>
                      {fmt(a.timestamp)}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: '#888', fontFamily: 'monospace' }}>
                    valoare={a.value.toFixed(4)}  threshold={a.threshold.toFixed(4)}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function WatchdogPage() {
  const [status,     setStatus]     = useState<WatchdogStatus | null>(null);
  const [thresholds, setThresholds] = useState<Record<string, PairThreshold>>({});
  const [alerts,     setAlerts]     = useState<WatchdogAlert[]>([]);
  const [filter,     setFilter]     = useState('ALL');
  const [tab,        setTab]        = useState<'thresholds' | 'alerts'>('thresholds');
  const [toast,      setToast]      = useState<{ msg: string; type: 'ok' | 'err' } | null>(null);
  const [apiError,   setApiError]   = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const showToast = useCallback((msg: string, type: 'ok' | 'err') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  }, []);

  // Poll status + thresholds + alerts la 2s
  useEffect(() => {
    async function poll() {
      try {
        const [st, thr, al] = await Promise.all([
          apiFetch<WatchdogStatus>('/api/watchdog/status'),
          apiFetch<{ thresholds: Record<string, PairThreshold> }>('/api/watchdog/thresholds'),
          apiFetch<{ alerts: WatchdogAlert[] }>('/api/watchdog/alerts?limit=100'),
        ]);
        setStatus(st);
        setThresholds(thr.thresholds || {});
        setAlerts(al.alerts || []);
        setApiError(null);
      } catch (e: any) {
        setApiError(e.message);
      }
    }
    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const handleSave = useCallback(async (pair: string, updates: Partial<PairThreshold>) => {
    try {
      await apiFetch(`/api/watchdog/thresholds/${pair}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      showToast(`✓ Threshold actualizat: ${pair}`, 'ok');
    } catch (e: any) {
      showToast(`Eroare: ${e.message}`, 'err');
    }
  }, [showToast]);

  const handleSilence = useCallback(async (pair: string, minutes: number) => {
    try {
      await apiFetch(`/api/watchdog/silence/${pair}?minutes=${minutes}`, { method: 'POST' });
      showToast(`🔕 ${pair} silentat ${minutes}min`, 'ok');
    } catch (e: any) {
      showToast(`Eroare: ${e.message}`, 'err');
    }
  }, [showToast]);

  const handleUnsilence = useCallback(async (pair: string) => {
    try {
      await apiFetch(`/api/watchdog/unsilence/${pair}`, { method: 'POST' });
      showToast(`▶ ${pair} reactivat`, 'ok');
    } catch (e: any) {
      showToast(`Eroare: ${e.message}`, 'err');
    }
  }, [showToast]);

  const handleTest = useCallback(async (pair: string) => {
    try {
      await apiFetch(`/api/watchdog/test/${pair}`, { method: 'POST' });
      showToast(`🔔 Alert test trimis pentru ${pair}`, 'ok');
    } catch (e: any) {
      showToast(`Eroare: ${e.message}`, 'err');
    }
  }, [showToast]);

  const alertsLast5 = alerts.filter(a => {
    const diff = Date.now() - new Date(a.timestamp).getTime();
    return diff < 5 * 60 * 1000;
  }).length;

  return (
    <div style={{
      background: '#0f0f1a', minHeight: '100vh',
      padding: '28px 40px', color: '#e0e0ff',
      fontFamily: 'Inter, system-ui, sans-serif',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <span style={{ fontSize: 26, fontWeight: 800 }}>👁 Watchdog</span>
        <span style={{
          background: '#1a1a2e', border: '1px solid #2a2a4a',
          borderRadius: 6, padding: '2px 10px', fontSize: 12, color: '#888',
        }}>Monitoring Continuu</span>
        {alertsLast5 > 0 && (
          <span style={{
            background: '#2a0000', border: '1px solid #f87171',
            borderRadius: 6, padding: '2px 10px',
            fontSize: 11, color: '#f87171', fontWeight: 700,
          }}>🚨 {alertsLast5} alerte în ultimele 5min</span>
        )}
      </div>

      {apiError && (
        <div style={{
          background: '#2a0000', border: '1px solid #f87171',
          borderRadius: 8, padding: '10px 16px',
          marginBottom: 16, color: '#f87171', fontSize: 13,
        }}>⚠ API offline: {apiError} — pornește FastAPI pe {API}</div>
      )}

      {/* Status */}
      <StatusCard status={status} />

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 2, marginBottom: 20 }}>
        {([
          ['thresholds', `⚙️ Thresholds (${Object.keys(thresholds).length})`],
          ['alerts',     `🔔 Alerte (${alerts.length})`],
        ] as const).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: '7px 20px', borderRadius: '6px 6px 0 0',
              background: tab === t ? '#1a1a2e' : 'transparent',
              border: tab === t ? '1px solid #2a2a4a' : '1px solid transparent',
              borderBottom: tab === t ? '1px solid #0f0f1a' : '1px solid #1a1a2a',
              color: tab === t ? '#e0e0ff' : '#555',
              fontSize: 13, fontWeight: tab === t ? 700 : 400,
              cursor: 'pointer',
            }}
          >{label}</button>
        ))}
      </div>

      <div style={{
        background: '#1a1a2e', borderRadius: '0 8px 8px 8px',
        border: '1px solid #2a2a4a', padding: '20px 24px',
        marginBottom: 24,
      }}>
        {tab === 'thresholds' ? (
          <ThresholdsTable
            thresholds={thresholds}
            onSave={handleSave}
            onSilence={handleSilence}
            onUnsilence={handleUnsilence}
            onTest={handleTest}
          />
        ) : (
          <AlertsFeed
            alerts={alerts}
            filter={filter}
            onFilterChange={setFilter}
          />
        )}
      </div>

      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 32, right: 32,
          background: toast.type === 'ok' ? '#0a2a0a' : '#2a0a0a',
          border: `1px solid ${toast.type === 'ok' ? '#22c55e' : '#f87171'}`,
          borderRadius: 10, padding: '12px 20px',
          color: toast.type === 'ok' ? '#4ade80' : '#f87171',
          fontSize: 14, fontWeight: 600,
          boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
          zIndex: 999, animation: 'fadeIn 0.2s ease',
        }}>
          {toast.type === 'ok' ? '✓' : '✗'} {toast.msg}
        </div>
      )}

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
      `}</style>
    </div>
  );
}

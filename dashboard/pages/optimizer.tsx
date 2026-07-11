/**
 * dashboard/pages/optimizer.tsx  -  QuantLuna Optimizer Dashboard v1.0
 * Sprint S43 (2026-07-12)
 *
 * Sectiuni:
 *   1. STATUS BAR   – running/idle, urmatoarea rulare, progress bar
 *   2. RUN NOW      – form: perechi, obiectiv, grid type, dry-run + buton
 *   3. RESULTS      – tabel per pereche cu Sharpe IS/OOS, WFO, params, badge
 *   4. HEATMAP      – selectie pereche + iframe/link heatmap HTML
 *   5. HISTORY      – ultimele rulari cu timestamp + statistici
 */

import React, {
  useCallback, useEffect, useRef, useState,
} from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ── Types ────────────────────────────────────────────────────────────────────

interface OptimizerStatus {
  running: boolean;
  last_run: string | null;
  pairs_count: number;
  auto_reoptimizer_active: boolean;
  auto_schedule: { weekday: number; hour_utc: number } | null;
  timestamp: string;
}

interface PairResult {
  entry_z: number;
  exit_z: number;
  stop_z: number;
  lookback: number;
  _optimized_at: string;
  _oos_sharpe: number;
  _wfo_score: number;
}

interface HistoryEntry {
  timestamp: string;
  pairs_count: number;
  applied: string[];
  degraded: string[];
  overfit: string[];
  unchanged: string[];
  dry_run: boolean;
  results: Record<string, PairResult>;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const DAYS = ['Lun', 'Mar', 'Mie', 'Joi', 'Vin', 'Sâm', 'Dum'];

function wfoColor(score: number): string {
  if (score >= 0.7) return '#4ade80';
  if (score >= 0.5) return '#fbbf24';
  return '#f87171';
}

function wfoBadge(score: number, oosSharpe: number): { label: string; bg: string; color: string } {
  if (oosSharpe < 0) return { label: 'DEGRADAT', bg: '#3a0000', color: '#f87171' };
  if (score >= 0.5 && oosSharpe > 0) return { label: 'PASS ✓', bg: '#0a2a0a', color: '#4ade80' };
  return { label: 'OVERFIT ⚠', bg: '#2a1a00', color: '#fbbf24' };
}

function fmt(d: string | null): string {
  if (!d) return '—';
  return new Date(d).toLocaleString('ro-RO', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

async function apiFetch<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 700, color: '#555',
      textTransform: 'uppercase', letterSpacing: 1.5,
      marginBottom: 12, paddingBottom: 6,
      borderBottom: '1px solid #1a1a2a',
    }}>{children}</div>
  );
}

function StatusBar({ status }: { status: OptimizerStatus | null }) {
  if (!status) return (
    <div style={{
      background: '#1a1a2e', borderRadius: 12,
      padding: '16px 20px', marginBottom: 24,
      border: '1px solid #2a2a4a', color: '#555',
    }}>Se incarca statusul...</div>
  );

  const scheduleLabel = status.auto_schedule
    ? `${DAYS[status.auto_schedule.weekday]} ${String(status.auto_schedule.hour_utc).padStart(2, '0')}:00 UTC`
    : null;

  return (
    <div style={{
      background: '#1a1a2e', borderRadius: 12,
      padding: '16px 24px', marginBottom: 24,
      border: `1px solid ${status.running ? '#2a3a1a' : '#2a2a4a'}`,
      display: 'flex', gap: 32, flexWrap: 'wrap', alignItems: 'center',
    }}>
      {/* Running indicator */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          width: 10, height: 10, borderRadius: '50%',
          background: status.running ? '#4ade80' : '#3a3a5a',
          display: 'inline-block',
          boxShadow: status.running ? '0 0 8px #4ade80' : 'none',
          animation: status.running ? 'pulse 1s infinite' : 'none',
        }} />
        <span style={{
          fontWeight: 700, fontSize: 15,
          color: status.running ? '#4ade80' : '#aaa',
        }}>
          {status.running ? 'Grid Search în desfășurare...' : 'Idle'}
        </span>
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 24, fontSize: 13 }}>
        <div>
          <span style={{ color: '#555' }}>Perechi: </span>
          <span style={{ color: '#ccc', fontWeight: 600 }}>{status.pairs_count}</span>
        </div>
        {scheduleLabel && (
          <div>
            <span style={{ color: '#555' }}>Auto: </span>
            <span style={{ color: '#8b5cf6', fontWeight: 600 }}>{scheduleLabel}</span>
          </div>
        )}
        {status.last_run && (
          <div>
            <span style={{ color: '#555' }}>Ultima rulare: </span>
            <span style={{ color: '#aaa' }}>{fmt(status.last_run)}</span>
          </div>
        )}
      </div>

      {/* Progress bar cand ruleaza */}
      {status.running && (
        <div style={{
          width: '100%', height: 3,
          background: '#1a1a3a', borderRadius: 2, overflow: 'hidden',
          marginTop: 4,
        }}>
          <div style={{
            height: '100%', borderRadius: 2,
            background: 'linear-gradient(90deg, #4ade80, #22d3ee)',
            animation: 'progress-anim 2s linear infinite',
            width: '40%',
          }} />
        </div>
      )}
    </div>
  );
}

function RunNowPanel({
  pairs,
  onRun,
  running,
}: {
  pairs: string[];
  onRun: (cfg: RunCfg) => void;
  running: boolean;
}) {
  const [selectedPairs, setSelectedPairs] = useState<string[]>([]);
  const [objective, setObjective] = useState('sharpe');
  const [gridType, setGridType] = useState('coarse');
  const [days, setDays] = useState(180);
  const [dryRun, setDryRun] = useState(false);

  const allSelected = selectedPairs.length === 0;

  const togglePair = (p: string) => {
    setSelectedPairs(prev =>
      prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]
    );
  };

  const handleRun = () => {
    if (running) return;
    onRun({ pairs: selectedPairs, objective, gridType, days, dryRun });
  };

  return (
    <div style={{
      background: '#1a1a2e', borderRadius: 12,
      padding: '20px 24px', marginBottom: 24,
      border: '1px solid #2a2a4a',
    }}>
      <SectionTitle>🔍 Run Now — Grid Search Manual</SectionTitle>

      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        {/* Perechi */}
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>Perechi</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {pairs.map(p => (
              <button
                key={p}
                onClick={() => togglePair(p)}
                style={{
                  padding: '4px 10px', borderRadius: 6, fontSize: 11,
                  background: selectedPairs.includes(p) ? '#2a3a1a' : '#16162a',
                  border: `1px solid ${selectedPairs.includes(p) ? '#4ade80' : '#2a2a4a'}`,
                  color: selectedPairs.includes(p) ? '#4ade80' : '#888',
                  cursor: 'pointer',
                }}
              >{p}</button>
            ))}
            <button
              onClick={() => setSelectedPairs([])}
              style={{
                padding: '4px 10px', borderRadius: 6, fontSize: 11,
                background: allSelected ? '#1a1a3a' : '#16162a',
                border: `1px solid ${allSelected ? '#8b5cf6' : '#2a2a4a'}`,
                color: allSelected ? '#8b5cf6' : '#888',
                cursor: 'pointer',
              }}
            >Toate</button>
          </div>
        </div>

        {/* Configurare */}
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>Obiectiv</div>
            <select
              value={objective}
              onChange={e => setObjective(e.target.value)}
              style={{
                background: '#16162a', border: '1px solid #2a2a4a',
                borderRadius: 6, color: '#ccc', padding: '6px 10px',
                fontSize: 13, cursor: 'pointer',
              }}
            >
              {['sharpe', 'calmar', 'sortino', 'pnl', 'profit_factor'].map(o => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>

          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>Grid</div>
            <select
              value={gridType}
              onChange={e => setGridType(e.target.value)}
              style={{
                background: '#16162a', border: '1px solid #2a2a4a',
                borderRadius: 6, color: '#ccc', padding: '6px 10px',
                fontSize: 13, cursor: 'pointer',
              }}
            >
              <option value="coarse">Coarse (~135 combo)</option>
              <option value="fine">Fine (~300 combo)</option>
            </select>
          </div>

          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>Zile date</div>
            <input
              type="number" value={days} min={30} max={365}
              onChange={e => setDays(Number(e.target.value))}
              style={{
                background: '#16162a', border: '1px solid #2a2a4a',
                borderRadius: 6, color: '#ccc', padding: '6px 10px',
                fontSize: 13, width: 72,
              }}
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox" id="dryrun" checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
              style={{ accentColor: '#fbbf24', width: 14, height: 14 }}
            />
            <label htmlFor="dryrun" style={{ fontSize: 12, color: '#888', cursor: 'pointer' }}>
              Dry-run
            </label>
          </div>

          <button
            onClick={handleRun}
            disabled={running}
            style={{
              padding: '8px 24px', borderRadius: 8,
              background: running ? '#1a2a1a' : '#14532d',
              border: `1px solid ${running ? '#2a3a2a' : '#22c55e'}`,
              color: running ? '#4a7a4a' : '#4ade80',
              fontWeight: 700, fontSize: 14,
              cursor: running ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s',
            }}
          >
            {running ? '⏳ Running...' : '▶ Run Grid Search'}
          </button>
        </div>
      </div>

      {dryRun && (
        <div style={{
          marginTop: 10, padding: '6px 12px', borderRadius: 6,
          background: '#1a1500', border: '1px solid #fbbf24',
          fontSize: 12, color: '#fbbf24',
        }}>⚠ Dry-run activ — nu se vor scrie configurații reale</div>
      )}
    </div>
  );
}

function ResultsTable({
  results,
  onViewHeatmap,
}: {
  results: Record<string, PairResult>;
  onViewHeatmap: (pair: string) => void;
}) {
  const entries = Object.entries(results);
  if (entries.length === 0) return null;

  return (
    <div style={{ marginBottom: 24 }}>
      <SectionTitle>📊 Rezultate per Pereche</SectionTitle>
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%', borderCollapse: 'collapse',
          fontSize: 13, tableLayout: 'auto',
        }}>
          <thead>
            <tr style={{ color: '#555', fontSize: 11, textTransform: 'uppercase' }}>
              {['Pereche', 'entry_z', 'exit_z', 'stop_z', 'lookback',
                'OOS Sharpe', 'WFO Score', 'Status', 'Heatmap', 'Optimizat'].map(h => (
                <th key={h} style={{
                  padding: '8px 12px', textAlign: 'left',
                  borderBottom: '1px solid #1a1a2a', fontWeight: 600,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {entries
              .sort(([, a], [, b]) => b._oos_sharpe - a._oos_sharpe)
              .map(([pair, r]) => {
                const badge = wfoBadge(r._wfo_score, r._oos_sharpe);
                return (
                  <tr
                    key={pair}
                    style={{
                      borderBottom: '1px solid #141420',
                      transition: 'background 0.15s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = '#1a1a2e')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={{ padding: '9px 12px', fontWeight: 700, color: '#ccc' }}>{pair}</td>
                    <td style={{ padding: '9px 12px', color: '#8b5cf6', fontFamily: 'monospace' }}>{r.entry_z}</td>
                    <td style={{ padding: '9px 12px', color: '#8b5cf6', fontFamily: 'monospace' }}>{r.exit_z}</td>
                    <td style={{ padding: '9px 12px', color: '#8b5cf6', fontFamily: 'monospace' }}>{r.stop_z}</td>
                    <td style={{ padding: '9px 12px', color: '#8b5cf6', fontFamily: 'monospace' }}>{r.lookback}</td>
                    <td style={{
                      padding: '9px 12px', fontFamily: 'monospace', fontWeight: 700,
                      color: r._oos_sharpe >= 0.5 ? '#4ade80' : r._oos_sharpe >= 0 ? '#fbbf24' : '#f87171',
                    }}>{r._oos_sharpe.toFixed(3)}</td>
                    <td style={{
                      padding: '9px 12px', fontFamily: 'monospace', fontWeight: 700,
                      color: wfoColor(r._wfo_score),
                    }}>{r._wfo_score.toFixed(2)}</td>
                    <td style={{ padding: '9px 12px' }}>
                      <span style={{
                        background: badge.bg, color: badge.color,
                        padding: '2px 8px', borderRadius: 4,
                        fontSize: 11, fontWeight: 700,
                      }}>{badge.label}</span>
                    </td>
                    <td style={{ padding: '9px 12px' }}>
                      <button
                        onClick={() => onViewHeatmap(pair)}
                        style={{
                          padding: '3px 10px', borderRadius: 5,
                          background: '#16162a', border: '1px solid #3a3a6a',
                          color: '#8b5cf6', fontSize: 11, cursor: 'pointer',
                        }}
                      >🗺 View</button>
                    </td>
                    <td style={{ padding: '9px 12px', color: '#555', fontSize: 11 }}>
                      {fmt(r._optimized_at)}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HeatmapViewer({
  pair,
  onClose,
}: {
  pair: string;
  onClose: () => void;
}) {
  const url = `${API}/api/optimizer/heatmap/${pair}`;
  return (
    <div style={{ marginBottom: 24 }}>
      <SectionTitle>
        🗺 Heatmap — {pair}
        <button
          onClick={onClose}
          style={{
            marginLeft: 12, padding: '1px 8px', borderRadius: 4,
            background: '#2a2a4a', border: '1px solid #3a3a6a',
            color: '#888', fontSize: 10, cursor: 'pointer',
          }}
        >✕ Închide</button>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          style={{
            marginLeft: 8, padding: '1px 8px', borderRadius: 4,
            background: '#1a2a1a', border: '1px solid #22c55e',
            color: '#4ade80', fontSize: 10, textDecoration: 'none',
          }}
        >↗ Tab nou</a>
      </SectionTitle>
      <div style={{
        background: '#111', borderRadius: 8,
        border: '1px solid #2a2a4a', overflow: 'hidden',
      }}>
        <iframe
          src={url}
          style={{ width: '100%', height: 420, border: 'none', display: 'block' }}
          title={`Heatmap ${pair}`}
        />
      </div>
    </div>
  );
}

function HistoryTab({ history }: { history: HistoryEntry[] }) {
  if (history.length === 0) return (
    <div style={{ color: '#555', textAlign: 'center', padding: '40px 0' }}>
      Nicio rulare anterioară.
    </div>
  );

  return (
    <div>
      <SectionTitle>📋 Istoric Reoptimizări ({history.length} rulări)</SectionTitle>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {[...history].reverse().map((h, i) => (
          <div
            key={i}
            style={{
              background: '#16162a', borderRadius: 8,
              border: '1px solid #1e1e3a', padding: '12px 16px',
            }}
          >
            <div style={{ display: 'flex', gap: 20, alignItems: 'center', marginBottom: 8 }}>
              <span style={{ color: '#888', fontSize: 12 }}>{fmt(h.timestamp)}</span>
              {h.dry_run && (
                <span style={{
                  background: '#1a1500', border: '1px solid #fbbf24',
                  color: '#fbbf24', borderRadius: 4,
                  padding: '1px 6px', fontSize: 10,
                }}>DRY-RUN</span>
              )}
              <span style={{ marginLeft: 'auto', fontSize: 12, color: '#555' }}>
                {h.pairs_count} perechi
              </span>
            </div>
            <div style={{ display: 'flex', gap: 16, fontSize: 12, flexWrap: 'wrap' }}>
              <span>
                <span style={{ color: '#4ade80', fontWeight: 700 }}>✓ {h.applied.length}</span>
                <span style={{ color: '#555' }}> aplicate</span>
              </span>
              <span>
                <span style={{ color: '#fbbf24', fontWeight: 700 }}>⚠ {h.overfit.length}</span>
                <span style={{ color: '#555' }}> overfit</span>
              </span>
              <span>
                <span style={{ color: '#f87171', fontWeight: 700 }}>✕ {h.degraded.length}</span>
                <span style={{ color: '#555' }}> degradate</span>
              </span>
              {h.applied.length > 0 && (
                <span style={{ color: '#888' }}>
                  → {h.applied.map(p => (
                    <span key={p} style={{
                      background: '#0a2a0a', color: '#4ade80',
                      borderRadius: 3, padding: '0 5px',
                      marginRight: 3, fontSize: 11,
                    }}>{p}</span>
                  ))}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Run config type ───────────────────────────────────────────────────────────
interface RunCfg {
  pairs: string[];
  objective: string;
  gridType: string;
  days: number;
  dryRun: boolean;
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function OptimizerPage() {
  const [status, setStatus] = useState<OptimizerStatus | null>(null);
  const [results, setResults] = useState<Record<string, PairResult>>({});
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [activePairs, setActivePairs] = useState<string[]>([]);
  const [heatmapPair, setHeatmapPair] = useState<string | null>(null);
  const [tab, setTab] = useState<'results' | 'history'>('results');
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'err' } | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const showToast = useCallback((msg: string, type: 'ok' | 'err') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  }, []);

  // Poll status + results fiecare 1.5s
  useEffect(() => {
    async function poll() {
      try {
        const [st, res, hist] = await Promise.all([
          apiFetch<OptimizerStatus>('/api/optimizer/status'),
          apiFetch<{ results: Record<string, PairResult> }>('/api/optimizer/results'),
          apiFetch<{ history: HistoryEntry[] }>('/api/optimizer/history?limit=20'),
        ]);
        setStatus(st);
        setResults(res.results || {});
        setHistory(hist.history || []);
        setApiError(null);

        // Deduce perechi active din results + status
        const knownPairs = Object.keys(res.results);
        if (knownPairs.length > 0) setActivePairs(knownPairs);
      } catch (e: any) {
        setApiError(e.message);
      }
    }
    poll();
    pollRef.current = setInterval(poll, 1500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const handleRun = useCallback(async (cfg: RunCfg) => {
    try {
      const res = await fetch(`${API}/api/optimizer/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pairs: cfg.pairs.length > 0 ? cfg.pairs : null,
          days: cfg.days,
          objective: cfg.objective,
          grid_type: cfg.gridType,
          dry_run: cfg.dryRun,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Eroare' }));
        throw new Error(err.detail);
      }
      showToast('Grid search pornit! Urmărește progresul în status.', 'ok');
    } catch (e: any) {
      showToast(`Eroare: ${e.message}`, 'err');
    }
  }, [showToast]);

  const running = status?.running ?? false;

  // Fallback pairs daca API nu a returnat inca
  const pairs = activePairs.length > 0
    ? activePairs
    : ['BTCUSDT-ETHUSDT', 'SOLUSDT-AVAXUSDT'];

  return (
    <div style={{
      background: '#0f0f1a', minHeight: '100vh',
      padding: '28px 40px', color: '#e0e0ff',
      fontFamily: 'Inter, system-ui, sans-serif',
    }}>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <span style={{ fontSize: 26, fontWeight: 800 }}>🔬 Optimizer</span>
        <span style={{
          background: '#1a1a2e', border: '1px solid #2a2a4a',
          borderRadius: 6, padding: '2px 10px', fontSize: 12, color: '#888',
        }}>Grid Search WFO</span>
        {status?.auto_reoptimizer_active && (
          <span style={{
            background: '#0a2a0a', border: '1px solid #22c55e',
            borderRadius: 6, padding: '2px 10px', fontSize: 11, color: '#4ade80',
          }}>● Auto activ</span>
        )}
      </div>

      {apiError && (
        <div style={{
          background: '#2a0000', border: '1px solid #f87171',
          borderRadius: 8, padding: '10px 16px',
          marginBottom: 16, color: '#f87171', fontSize: 13,
        }}>⚠ API offline: {apiError} — pornește FastAPI pe {API}</div>
      )}

      {/* 1. Status */}
      <StatusBar status={status} />

      {/* 2. Run Now */}
      <RunNowPanel pairs={pairs} onRun={handleRun} running={running} />

      {/* 3. Heatmap viewer */}
      {heatmapPair && (
        <HeatmapViewer pair={heatmapPair} onClose={() => setHeatmapPair(null)} />
      )}

      {/* 4. Tabs: Results | History */}
      <div style={{ display: 'flex', gap: 2, marginBottom: 20 }}>
        {(['results', 'history'] as const).map(t => (
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
              textTransform: 'capitalize',
            }}
          >
            {t === 'results' ? `📊 Rezultate (${Object.keys(results).length})` : `📋 Istoric (${history.length})`}
          </button>
        ))}
      </div>
      <div style={{
        background: '#1a1a2e', borderRadius: '0 8px 8px 8px',
        border: '1px solid #2a2a4a', padding: '20px 24px',
        marginBottom: 24,
      }}>
        {tab === 'results'
          ? <ResultsTable results={results} onViewHeatmap={setHeatmapPair} />
          : <HistoryTab history={history} />
        }
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
        @keyframes progress-anim {
          0%   { margin-left: -40%; }
          100% { margin-left: 100%; }
        }
      `}</style>
    </div>
  );
}

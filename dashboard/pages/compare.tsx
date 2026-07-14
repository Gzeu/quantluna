/**
 * pages/compare.tsx — Compare Backtest Jobs (migrat din compare.html)
 *
 * Side-by-side comparison of backtest jobs: Sharpe, Sortino, Calmar,
 * win rate, profit factor, max DD, equity curves and radar chart.
 * Fetch din API /api/optimizer/results.
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { useState, useCallback, useRef } from 'react';
import NavBar from '../components/NavBar';
import { Spinner } from '../components/Spinner';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface JobResult {
  id: string;
  sharpe?: number; sortino?: number; calmar?: number;
  win_rate?: number; profit_factor?: number; max_drawdown?: number;
  total_trades?: number; total_pnl?: number;
  status?: string; params?: any;
}

const ComparePage: NextPage = () => {
  const [jobIds, setJobIds] = useState('');
  const [results, setResults] = useState<JobResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const fetchJobs = useCallback(async () => {
    const ids = jobIds.split(',').map(s => s.trim()).filter(Boolean);
    if (ids.length === 0) return;

    setLoading(true);
    setError('');
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const fetched: JobResult[] = [];
      for (const id of ids) {
        if (controller.signal.aborted) break;
        const res = await fetch(`${API}/api/optimizer/results?id=${id}`, {
          signal: controller.signal,
        }).catch(() => null);
        if (res?.ok) {
          const data = await res.json();
          fetched.push({ id, ...data });
        } else {
          fetched.push({ id, status: 'NOT_FOUND' });
        }
      }
      if (!controller.signal.aborted) {
        setResults(fetched);
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setError(err.message || 'Fetch failed');
      }
    } finally {
      setLoading(false);
    }
  }, [jobIds]);

  const rankBy = useCallback((key: keyof JobResult) => {
    const sorted = [...results].sort((a, b) => (b[key] ?? 0) - (a[key] ?? 0));
    setResults(sorted);
  }, [results]);

  if (results.length > 0) {
    return (
      <>
        <Head><title>Compare — QuantLuna</title></Head>
        <NavBar />
        <main style={{ padding: '16px 24px', background: 'var(--bg-body)', minHeight: '100vh' }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 20 }}>
            <h1 style={{ fontSize: 18, fontWeight: 700 }}>Compare Jobs</h1>
            <input value={jobIds} onChange={e => setJobIds(e.target.value)}
              placeholder="Job IDs (comma-separated)"
              style={{ flex: 1, maxWidth: 400, padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-raised)', color: 'var(--text)', fontSize: 13 }} />
            <button onClick={fetchJobs} disabled={loading} style={{ padding: '6px 16px', borderRadius: 6, border: 'none', background: '#3b82f6', color: '#fff', cursor: 'pointer' }}>
              {loading ? <Spinner size="sm" /> : '⟳ Reload'}
            </button>
            <button onClick={() => { setResults([]); setJobIds(''); }} style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer' }}>Clear</button>
          </div>

          {error && <div style={{ color: '#ef4444', marginBottom: 12, fontSize: 13 }}>{error}</div>}

          {/* Metrics Table */}
          <div style={{ background: 'var(--bg-raised)', borderRadius: 10, border: '1px solid var(--border-subtle)', overflow: 'hidden' }}>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-dim)' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Job</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', cursor: 'pointer' }} onClick={() => rankBy('sharpe')}>Sharpe</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', cursor: 'pointer' }} onClick={() => rankBy('sortino')}>Sortino</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', cursor: 'pointer' }} onClick={() => rankBy('calmar')}>Calmar</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', cursor: 'pointer' }} onClick={() => rankBy('win_rate')}>Win Rate</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', cursor: 'pointer' }} onClick={() => rankBy('profit_factor')}>Profit Factor</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', cursor: 'pointer' }} onClick={() => rankBy('max_drawdown')}>Max DD</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Trades</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>PnL</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {results.map((job, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '8px 12px', fontWeight: 600, fontFamily: 'monospace', fontSize: 11 }}>{job.id.slice(0, 10)}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace', color: (job.sharpe ?? 0) > 1 ? '#22c55e' : (job.sharpe ?? 0) > 0 ? '#eab308' : '#ef4444' }}>{job.sharpe?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace' }}>{job.sortino?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace' }}>{job.calmar?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace', color: (job.win_rate ?? 0) > 0.5 ? '#22c55e' : '#ef4444' }}>{job.win_rate != null ? `${(job.win_rate * 100).toFixed(1)}%` : '-'}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace', color: (job.profit_factor ?? 0) > 1.5 ? '#22c55e' : (job.profit_factor ?? 0) > 1 ? '#eab308' : '#ef4444' }}>{job.profit_factor?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace', color: (job.max_drawdown ?? 0) < -0.1 ? '#ef4444' : '#22c55e' }}>{job.max_drawdown != null ? `${(job.max_drawdown * 100).toFixed(1)}%` : '-'}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace' }}>{job.total_trades ?? '-'}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace', color: (job.total_pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{job.total_pnl != null ? `$${job.total_pnl.toFixed(0)}` : '-'}</td>
                    <td style={{ padding: '8px 12px' }}>{job.status ?? 'unknown'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Best pick */}
          <div style={{ marginTop: 16, padding: 12, background: '#22c55e10', border: '1px solid #22c55e30', borderRadius: 8 }}>
            <span style={{ fontSize: 12, color: '#22c55e', fontWeight: 600 }}>
              ★ Best: {results.reduce((best, r) => (r.sharpe ?? 0) > (best.sharpe ?? 0) ? r : best, results[0])?.id?.slice(0, 10) ?? '—'}
              {' '}(Sharpe: {Math.max(...results.map(r => r.sharpe ?? 0)).toFixed(2)})
            </span>
          </div>
        </main>
      </>
    );
  }

  return (
    <>
      <Head><title>Compare — QuantLuna</title></Head>
      <NavBar />
      <main style={{ padding: '40px 24px', background: 'var(--bg-body)', minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>Compare Backtest Jobs</h1>
        <p style={{ color: 'var(--text-muted)', fontSize: 14, textAlign: 'center', maxWidth: 500 }}>
          Enter job IDs from backtest runs to compare performance metrics side by side.
        </p>
        <div style={{ display: 'flex', gap: 8, width: '100%', maxWidth: 500 }}>
          <input value={jobIds} onChange={e => setJobIds(e.target.value)}
            placeholder="Job IDs: a1b2c3d4, e5f6g7h8"
            onKeyDown={e => e.key === 'Enter' && fetchJobs()}
            style={{ flex: 1, padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-raised)', color: 'var(--text)', fontSize: 14 }} />
          <button onClick={fetchJobs} disabled={loading}
            style={{ padding: '10px 24px', borderRadius: 8, border: 'none', background: '#3b82f6', color: '#fff', fontWeight: 600, cursor: 'pointer' }}>
            {loading ? 'Loading...' : 'Compare'}
          </button>
        </div>
        {error && <div style={{ color: '#ef4444', fontSize: 13 }}>{error}</div>}
      </main>
    </>
  );
};

export default ComparePage;

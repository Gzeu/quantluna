/**
 * pages/portfolio.tsx — Portfolio & Risk Dashboard (S48 P0)
 * Real equity, positions by ownership, drawdown, health status.
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { useEffect, useState, useCallback, useRef } from 'react';
import NavBar from '../components/NavBar';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface Position {
  symbol: string; side: string; size: number;
  entry_price: number; mark_price: number; unrealized_pnl: number;
  ownership: string; managed_by: string; notional: number;
}
interface AccountData {
  status: string; wallet: any; positions: any; health: any; errors: string[];
  age_seconds: number;
}

const PortfolioPage: NextPage = () => {
  const [data, setData] = useState<AccountData | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [accRes, posRes] = await Promise.all([
        fetch(`${API}/api/account/summary`).then(r => r.json()),
        fetch(`${API}/api/positions`).then(r => r.json()),
      ]);
      if (accRes.status !== 'no_snapshot') setData(accRes);
      if (posRes.positions) setPositions(posRes.positions);
      setError('');
    } catch {
      setError('API unavailable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    timer.current = setInterval(fetchData, 8000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [fetchData]);

  if (loading) return <><Head><title>Portfolio — QuantLuna</title></Head><NavBar /><div style={{ padding: 40, color: '#9ca3af' }}>Loading...</div></>;

  return (
    <>
      <Head><title>Portfolio — QuantLuna</title></Head>
      <NavBar />
      <main style={{ padding: '16px 24px 40px', background: 'var(--bg-body)', minHeight: '100vh' }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 16 }}>Portfolio & Risk</h1>
        {error && <div style={{ padding: 12, background: '#7f1d1d20', border: '1px solid #7f1d1d40', borderRadius: 8, marginBottom: 16, color: '#fca5a5' }}>{error}</div>}

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
          <KPI label="Equity" value={data?.wallet?.equity ?? 0} fmt="usd" />
          <KPI label="Available" value={data?.wallet?.available ?? 0} fmt="usd" />
          <KPI label="Margin Used" value={data?.wallet?.margin ?? 0} fmt="usd" />
          <KPI label="Snapshot Age" value={data?.age_seconds ?? 0} fmt="s" />
        </div>

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
          {[{ l: 'Managed', c: data?.positions?.managed ?? 0, cl: '#22c55e' },
            { l: 'Adopted', c: data?.positions?.adopted ?? 0, cl: '#3b82f6' },
            { l: 'External', c: data?.positions?.external ?? 0, cl: '#f59e0b' },
            { l: 'Orphaned', c: data?.positions?.orphaned ?? 0, cl: '#ef4444' },
            { l: 'Unprotected', c: data?.positions?.unprotected ?? 0, cl: '#dc2626' }]
            .map(c => (
              <div key={c.l} style={{ padding: '10px 16px', background: 'var(--bg-raised)', borderRadius: 8, border: '1px solid var(--border-subtle)', textAlign: 'center' }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: c.cl }}>{c.c}</div>
                <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase' }}>{c.l}</div>
              </div>
            ))}
        </div>

        <div style={{ background: 'var(--bg-raised)', borderRadius: 10, border: '1px solid var(--border-subtle)', overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-subtle)', fontWeight: 600, fontSize: 14 }}>Positions ({positions.length})</div>
          {positions.length === 0 ? (
            <div style={{ padding: 24, color: 'var(--text-dim)', fontSize: 13, textAlign: 'center' }}>No positions. Run an account sync.</div>
          ) : (
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-dim)' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Symbol</th><th style={{ textAlign: 'left', padding: '8px 12px' }}>Side</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Size</th><th style={{ textAlign: 'right', padding: '8px 12px' }}>Entry</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Mark</th><th style={{ textAlign: 'right', padding: '8px 12px' }}>PnL</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Ownership</th>
              </tr></thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '8px 12px', fontWeight: 600 }}>{p.symbol}</td>
                    <td style={{ padding: '8px 12px', color: p.side === 'Buy' ? '#22c55e' : '#ef4444' }}>{p.side}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace' }}>{p.size.toFixed(4)}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace' }}>${p.entry_price.toFixed(4)}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', fontFamily: 'monospace' }}>${p.mark_price.toFixed(4)}</td>
                    <td style={{ textAlign: 'right', padding: '8px 12px', color: p.unrealized_pnl >= 0 ? '#22c55e' : '#ef4444', fontFamily: 'monospace' }}>${p.unrealized_pnl.toFixed(2)}</td>
                    <td style={{ padding: '8px 12px' }}><OBadge o={p.ownership} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {data?.health && (
          <div style={{ marginTop: 16, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <HBadge label="REST" ok={data.health.rest_latency_ms < 1000} detail={`${data.health.rest_latency_ms}ms`} />
            <HBadge label="WS Public" ok={data.health.ws_public} />
            <HBadge label="WS Private" ok={data.health.ws_private} />
            <HBadge label="Snapshot" ok={data.status === 'READY'} detail={data.status} />
          </div>
        )}

        {data?.errors && data.errors.length > 0 && (
          <div style={{ marginTop: 16, padding: 12, background: '#7f1d1d20', border: '1px solid #7f1d1d40', borderRadius: 8 }}>
            <div style={{ color: '#fca5a5', fontWeight: 600, fontSize: 13 }}>Errors</div>
            {data.errors.map((e: string, i: number) => <div key={i} style={{ color: '#fca5a5', fontSize: 11, fontFamily: 'monospace' }}>{e}</div>)}
          </div>
        )}
      </main>
    </>
  );
};

export default PortfolioPage;

function KPI({ label, value, fmt }: { label: string; value: number; fmt: string }) {
  const s = fmt === 'usd' ? `$${value.toFixed(2)}` : fmt === 's' ? `${value.toFixed(0)}s` : String(value);
  return <div style={{ padding: '12px 16px', background: 'var(--bg-raised)', borderRadius: 8, border: '1px solid var(--border-subtle)', minWidth: 120 }}>
    <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'monospace' }}>{s}</div>
  </div>;
}

function OBadge({ o }: { o: string }) {
  const cc: Record<string, string> = { MANAGED: '#22c55e', ADOPTED: '#3b82f6', EXTERNAL_OBSERVED: '#f59e0b', ORPHANED: '#ef4444', UNPROTECTED: '#dc2626', CLOSING: '#9ca3af', ERROR: '#dc2626' };
  return <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, background: `${cc[o] || '#9ca3af'}20`, color: cc[o] || '#9ca3af', fontWeight: 600 }}>{o}</span>;
}

function HBadge({ label, ok, detail }: { label: string; ok: boolean; detail?: string }) {
  return <span style={{ fontSize: 11, padding: '4px 10px', borderRadius: 4, background: ok ? '#22c55e20' : '#ef444420', color: ok ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
    {ok ? '●' : '○'} {label}{detail ? ` (${detail})` : ''}
  </span>;
}

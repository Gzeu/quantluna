/**
 * dashboard/pages/portfolio.tsx  -  QuantLuna Portfolio Dashboard v1.0
 *
 * Sprint S36 (2026-07-12):
 *   Pagina /portfolio cu:
 *     - Card-uri: Equity total, PnL azi, PnL ieri
 *     - Equity Curve (line chart - recharts)
 *     - PnL zilnic per strategie (bar chart)
 *     - Tabel transferuri interne recente
 *     - Tabel retrageri recente
 *
 * Necesita: recharts (deja in package.json de obicei)
 */

import React, { useEffect, useState } from 'react';

// ---- Types ----
interface DailySummary {
  date: string;
  total_equity_usdt: number;
  realised_pnl_usdt: number;
  realised_pnl_pct: number;
  total_trades: number;
  total_fees: number;
  strategies: { name: string; pnl: number; trades: number }[];
}

interface EquityPoint {
  date: string;
  equity_usdt: number;
  pnl_usdt: number;
  pnl_pct: number;
  strategy: string;
}

interface Transfer {
  transfer_id: string;
  from_wallet: string;
  to_wallet: string;
  amount: number;
  asset: string;
  reason: string;
  status: string;
  created_at: string;
}

// ---- API base ----
const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ---- Stat Card ----
function StatCard({
  label, value, sub, color,
}: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div style={{
      background: '#1a1a2e', borderRadius: 12, padding: '20px 24px',
      minWidth: 180, flex: 1, border: '1px solid #2a2a4a',
    }}>
      <div style={{ color: '#888', fontSize: 13, marginBottom: 6 }}>{label}</div>
      <div style={{
        fontSize: 28, fontWeight: 700,
        color: color || '#e0e0ff',
      }}>{value}</div>
      {sub && <div style={{ color: '#666', fontSize: 12, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

// ---- Simple table ----
function DataTable({ cols, rows }: { cols: string[]; rows: string[][] }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr>
          {cols.map(c => (
            <th key={c} style={{
              textAlign: 'left', padding: '8px 12px',
              background: '#1a1a3e', color: '#aaa',
              borderBottom: '1px solid #2a2a4a',
            }}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j} style={{
                padding: '7px 12px',
                borderBottom: '1px solid #1a1a3a',
                color: '#ccc',
              }}>{cell}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---- Main Page ----
export default function PortfolioPage() {
  const [summary, setSummary] = useState<{ today: DailySummary; yesterday: DailySummary; pnl_vs_yesterday: number } | null>(null);
  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([]);
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [s, ec, tr] = await Promise.all([
          fetchJson<any>('/api/portfolio/summary'),
          fetchJson<any>(`/api/portfolio/equity-curve?days=${days}`),
          fetchJson<any>('/api/portfolio/transfers?limit=10'),
        ]);
        setSummary(s);
        setEquityCurve(ec.points || []);
        setTransfers(tr.transfers || []);
        setError(null);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }
    load();
    const interval = setInterval(load, 30_000); // refresh 30s
    return () => clearInterval(interval);
  }, [days]);

  const todayPnl = summary?.today?.realised_pnl_usdt ?? 0;
  const equity = summary?.today?.total_equity_usdt ?? 0;
  const pnlColor = todayPnl >= 0 ? '#4ade80' : '#f87171';

  // Mini equity chart (SVG)
  function MiniChart() {
    if (equityCurve.length < 2) return <div style={{ color: '#666', padding: 20 }}>Date insuficiente</div>;
    const W = 600, H = 160;
    const vals = equityCurve.map(p => p.equity_usdt);
    const minV = Math.min(...vals);
    const maxV = Math.max(...vals);
    const range = maxV - minV || 1;
    const pts = vals.map((v, i) => {
      const x = (i / (vals.length - 1)) * W;
      const y = H - ((v - minV) / range) * (H - 20) - 10;
      return `${x},${y}`;
    }).join(' ');
    const lastColor = vals[vals.length - 1] >= vals[0] ? '#4ade80' : '#f87171';
    return (
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
        <polyline
          points={pts}
          fill="none"
          stroke={lastColor}
          strokeWidth="2"
        />
        <text x={8} y={16} fill="#888" fontSize={11}>{maxV.toFixed(0)}</text>
        <text x={8} y={H - 4} fill="#888" fontSize={11}>{minV.toFixed(0)}</text>
      </svg>
    );
  }

  return (
    <div style={{
      background: '#0f0f1a', minHeight: '100vh',
      padding: '32px 40px', color: '#e0e0ff',
      fontFamily: 'Inter, system-ui, sans-serif',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 32 }}>
        <span style={{ fontSize: 28, fontWeight: 800 }}>📊 Portfolio</span>
        <span style={{ color: '#555', fontSize: 14 }}>
          {loading ? 'Se incarca...' : `Actualizat ${new Date().toLocaleTimeString()}`}
        </span>
      </div>

      {error && (
        <div style={{
          background: '#2a0000', border: '1px solid #f87171',
          borderRadius: 8, padding: '12px 16px', marginBottom: 24, color: '#f87171',
        }}>
          ⚠️ API offline: {error}
        </div>
      )}

      {/* Stat cards */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 32, flexWrap: 'wrap' }}>
        <StatCard
          label="Equity Total"
          value={equity > 0 ? `${equity.toLocaleString('en', { maximumFractionDigits: 2 })} USDT` : '—'}
          sub="Cont UNIFIED + Spot"
        />
        <StatCard
          label="PnL Azi"
          value={todayPnl !== 0 ? `${todayPnl >= 0 ? '+' : ''}${todayPnl.toFixed(2)} USDT` : '—'}
          sub={`${((summary?.today?.realised_pnl_pct ?? 0) * 100).toFixed(2)}%`}
          color={pnlColor}
        />
        <StatCard
          label="PnL Ieri"
          value={summary ? `${(summary.yesterday?.realised_pnl_usdt ?? 0) >= 0 ? '+' : ''}${(summary.yesterday?.realised_pnl_usdt ?? 0).toFixed(2)} USDT` : '—'}
          color={(summary?.yesterday?.realised_pnl_usdt ?? 0) >= 0 ? '#4ade80' : '#f87171'}
        />
        <StatCard
          label="Tranzactii Azi"
          value={String(summary?.today?.total_trades ?? '—')}
          sub={`Comisioane: ${(summary?.today?.total_fees ?? 0).toFixed(2)} USDT`}
        />
      </div>

      {/* Equity Curve */}
      <div style={{
        background: '#1a1a2e', borderRadius: 12,
        padding: '20px 24px', marginBottom: 24,
        border: '1px solid #2a2a4a',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <span style={{ fontWeight: 600 }}>📈 Equity Curve</span>
          <select
            value={days}
            onChange={e => setDays(Number(e.target.value))}
            style={{
              background: '#0f0f2a', color: '#ccc',
              border: '1px solid #2a2a4a', borderRadius: 6,
              padding: '4px 8px', fontSize: 13,
            }}
          >
            <option value={7}>7 zile</option>
            <option value={14}>14 zile</option>
            <option value={30}>30 zile</option>
            <option value={90}>90 zile</option>
          </select>
        </div>
        <MiniChart />
        <div style={{ color: '#555', fontSize: 12, marginTop: 8 }}>
          {equityCurve.length} puncte de date | Cumulativ: {equityCurve.reduce((a, p) => a + p.pnl_usdt, 0).toFixed(2)} USDT
        </div>
      </div>

      {/* Transferuri interne */}
      <div style={{
        background: '#1a1a2e', borderRadius: 12,
        padding: '20px 24px', marginBottom: 24,
        border: '1px solid #2a2a4a',
      }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>⇄ Transferuri Interne</div>
        {transfers.length === 0 ? (
          <div style={{ color: '#555' }}>Niciun transfer inca.</div>
        ) : (
          <DataTable
            cols={['Data', 'De la', 'Catre', 'Suma', 'Motiv', 'Status']}
            rows={transfers.map(t => [
              new Date(t.created_at).toLocaleString(),
              t.from_wallet,
              t.to_wallet,
              `${t.amount.toFixed(2)} ${t.asset}`,
              t.reason,
              t.status,
            ])}
          />
        )}
      </div>

      <div style={{ color: '#444', fontSize: 12, textAlign: 'center' }}>
        QuantLuna Portfolio v1.0 — refresh automat 30s
      </div>
    </div>
  );
}

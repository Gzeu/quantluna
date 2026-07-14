/**
 * pages/services.tsx — v4.0 CyberDark Pro
 * Modern service monitoring: live status grid, resource meters, quick actions
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import NavBar from '../components/NavBar';
import { StatsBar } from '../components/StatsBar';
import { MetricsBadge } from '../components/MetricsBadge';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Spinner } from '../components/ui/Spinner';
import { useServices, ServiceInfo } from '../hooks/useServices';
import { useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

/* ── Status indicator with color ─────────────────────────────── */
function StatusDot({ status }: { status: ServiceInfo['status'] }) {
  const colors = {
    running: 'var(--green)',
    stopped: 'var(--text-disabled)',
    unknown: 'var(--yellow)',
  };
  const anim = status === 'running' ? 'animate-live-pulse' : '';
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${anim}`}
      style={{
        background: colors[status] ?? 'var(--text-disabled)',
        boxShadow: status === 'running' ? `0 0 8px ${colors.running}` : 'none',
      }}
    />
  );
}

function StatusPill({ status }: { status: ServiceInfo['status'] }) {
  const map = { running: 'ql-pill-green', stopped: 'ql-pill-gray', unknown: 'ql-pill-yellow' };
  return <span className={`ql-pill ${map[status] ?? 'ql-pill-gray'}`}><StatusDot status={status} /> <span className="ml-1">{status}</span></span>;
}

/* ── Resource meter bar ─────────────────────────────────────── */
function Meter({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div className="w-full h-1.5 rounded-full bg-[var(--bg-elevated)] overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, background: color, boxShadow: `0 0 6px ${color}40` }}
      />
    </div>
  );
}

/* ── Single service card ────────────────────────────────────── */
function ServiceCard({ svc }: { svc: ServiceInfo }) {
  const cpu = svc.cpu ?? 0;
  const mem = svc.mem ?? 0;
  const cpuColor = cpu > 80 ? 'var(--red)' : cpu > 50 ? 'var(--yellow)' : 'var(--cyan)';
  const memColor = mem > 500 ? 'var(--red)' : mem > 200 ? 'var(--yellow)' : 'var(--green)';

  return (
    <div className="ql-card hover:border-[var(--border-strong)] transition-all duration-200 p-5 flex flex-col gap-3 min-h-[140px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="font-mono text-[13px] font-semibold text-[var(--text-primary)]">
          {svc.name}
        </span>
        <StatusPill status={svc.status} />
      </div>

      {/* PID + Uptime */}
      <div className="flex items-center gap-4 text-[11px] text-[var(--text-muted)]">
        {svc.pid !== undefined && (
          <span className="tabular">PID <span className="text-[var(--text-secondary)] font-mono">{svc.pid}</span></span>
        )}
        {svc.uptime && <span className="tabular">⏱ {svc.uptime}</span>}
      </div>

      {/* Resource meters */}
      <div className="space-y-2 mt-auto">
        <div className="flex items-center gap-2 text-[10px]">
          <span className="text-[var(--text-muted)] w-8">CPU</span>
          <Meter value={cpu} max={100} color={cpuColor} />
          <span className="text-[var(--text-secondary)] tabular w-10 text-right mono text-[10px]">
            {cpu.toFixed(1)}%
          </span>
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <span className="text-[var(--text-muted)] w-8">MEM</span>
          <Meter value={mem} max={512} color={memColor} />
          <span className="text-[var(--text-secondary)] tabular w-10 text-right mono text-[10px]">
            {mem.toFixed(0)}M
          </span>
        </div>
      </div>
    </div>
  );
}

/* ── Quick action bar ───────────────────────────────────────── */
function QuickActions() {
  const [loading, setLoading] = useState<string | null>(null);

  const action = async (path: string, label: string) => {
    setLoading(label);
    try {
      const res = await fetch(`${API}${path}`, { method: 'POST' });
      const data = await res.json();
      console.log(`${label}:`, data);
    } catch (e) {
      console.error(`${label} failed:`, e);
    }
    setTimeout(() => setLoading(null), 500);
  };

  return (
    <div className="flex flex-wrap gap-3">
      <button className="ql-btn ql-btn-primary ql-btn-sm" onClick={() => action('/api/services/restart', 'Restart')} disabled={!!loading}>
        {loading === 'Restart' ? '⟳ Restarting…' : '⟳ Restart All'}
      </button>
      <button className="ql-btn ql-btn-ghost ql-btn-sm" onClick={() => action('/api/services/stop', 'Stop')} disabled={!!loading}>
        ■ Stop All
      </button>
      <button className="ql-btn ql-btn-outline ql-btn-sm" onClick={() => action('/api/optimizer/run', 'Optimize')} disabled={!!loading}>
        ⚡ Run Optimizer
      </button>
    </div>
  );
}

/* ── Service grid ───────────────────────────────────────────── */
function ServiceGrid() {
  const { data, loading, error } = useServices(5_000);
  const { services, running, total } = data;

  if (error) {
    return (
      <div className="ql-card p-10 flex flex-col items-center gap-3 text-center">
        <span className="text-3xl">⚠️</span>
        <p className="text-[var(--text-muted)] text-sm">Backend unavailable: {error}</p>
        <p className="text-[var(--text-disabled)] text-xs">Ensure the Python API server is running on {API}</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3, 4, 5, 6].map(i => (
          <div key={i} className="ql-card p-5 space-y-3">
            <div className="skeleton h-5 w-32" />
            <div className="skeleton h-3 w-24" />
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-4 w-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 stagger">
      {services.map(svc => (
        <ServiceCard key={svc.name} svc={svc} />
      ))}
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────── */
const ServicesPage: NextPage = () => (
  <>
    <Head><title>Services — QuantLuna</title></Head>
    <NavBar />
    <StatsBar />
    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-body)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '20px 28px 48px',
        maxWidth: 1440,
        margin: '0 auto',
        width: '100%',
      }}
    >
      {/* Top row: metrics + quick actions */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <span className="live-dot active" />
          <h1 className="text-lg font-bold text-[var(--text-primary)]">Service Control Panel</h1>
          <span className="ql-pill ql-pill-purple text-[9px]">v4.0</span>
        </div>
        <QuickActions />
      </div>

      <div className="mb-6">
        <MetricsBadge />
      </div>

      <ServiceGrid/>
    </main>
  </>
);

export default ServicesPage;

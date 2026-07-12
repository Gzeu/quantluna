/**
 * pages/services.tsx — S37 continue
 * Pagina dedicata serviciilor. Previne 404 la G+S shortcut.
 * Afiseaza ServiceList (daca exista) sau placeholder elegant.
 */
import type { NextPage }  from 'next';
import Head               from 'next/head';
import { StatsBar }       from '../components/StatsBar';
import { MetricsBadge }   from '../components/MetricsBadge';
import { useRiskMetrics } from '../hooks/useRiskMetrics';
import { useState, useEffect } from 'react';
import { Card }           from '../components/ui/Card';
import { Badge }          from '../components/ui/Badge';
import { Spinner }        from '../components/ui/Spinner';

function RiskMetricsLoader() { useRiskMetrics(); return null; }

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface Service {
  name: string;
  status: 'running' | 'stopped' | 'error';
  pid?: number;
  uptime?: string;
  cpu?: number;
  mem?: number;
}

function ServiceRow({ svc }: { svc: Service }) {
  const color = svc.status === 'running' ? 'green' : svc.status === 'error' ? 'red' : 'gray';
  return (
    <tr className="border-b border-[var(--border)] hover:bg-[var(--bg-elevated)] transition-colors">
      <td className="py-3 pr-4 font-mono text-[var(--text-primary)] text-sm">{svc.name}</td>
      <td className="py-3 pr-4">
        <Badge variant={color as any} dot pulse={svc.status === 'running'}>
          {svc.status}
        </Badge>
      </td>
      <td className="py-3 pr-4 text-[var(--text-muted)] text-xs tabular">{svc.pid ?? '—'}</td>
      <td className="py-3 pr-4 text-[var(--text-muted)] text-xs tabular">{svc.uptime ?? '—'}</td>
      <td className="py-3 pr-4 text-right text-[var(--text-secondary)] text-xs tabular">
        {svc.cpu !== undefined ? `${svc.cpu.toFixed(1)}%` : '—'}
      </td>
      <td className="py-3 text-right text-[var(--text-secondary)] text-xs tabular">
        {svc.mem !== undefined ? `${svc.mem.toFixed(0)}MB` : '—'}
      </td>
    </tr>
  );
}

function ServiceList() {
  const [services, setServices] = useState<Service[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      fetch(`${API}/api/services/list`)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then((d: { services: Service[] }) => { setServices(d.services ?? []); setError(null); })
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
    load();
    const id = setInterval(load, 5_000);
    return () => clearInterval(id);
  }, []);

  const running = services.filter(s => s.status === 'running').length;

  return (
    <Card>
      <Card.Header>
        <div className="flex items-center gap-2">
          <Card.Title>Services</Card.Title>
          {!loading && (
            <span className="text-[10px] bg-[var(--bg-elevated)] text-[var(--text-muted)]
                             rounded-full px-2 py-0.5 border border-[var(--border)]">
              {running}/{services.length} running
            </span>
          )}
          {loading && <Spinner size="sm" />}
        </div>
        <Badge variant={error ? 'red' : running > 0 ? 'green' : 'gray'} dot pulse={running > 0}>
          {error ? 'API error' : loading ? 'Loading…' : 'Live'}
        </Badge>
      </Card.Header>

      {error ? (
        <div className="py-10 flex flex-col items-center gap-3">
          <span className="text-3xl">⚠️</span>
          <p className="text-[var(--text-muted)] text-sm text-center">
            Backend unavailable: {error}
          </p>
          <p className="text-[var(--text-disabled)] text-xs">
            Asigura-te ca serverul Python ruleaza pe {API}
          </p>
        </div>
      ) : loading ? (
        <div className="space-y-2">
          {[1,2,3,4,5].map(i => <div key={i} className="skeleton h-10 rounded" />)}
        </div>
      ) : services.length === 0 ? (
        <div className="py-10 flex flex-col items-center gap-3">
          <span className="text-3xl">⚙️</span>
          <p className="text-[var(--text-muted)] text-sm">Niciun serviciu raportat de backend.</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="ql-table w-full">
            <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-card)' }}>
              <tr>
                {['Service','Status','PID','Uptime','CPU','MEM'].map(h => (
                  <th key={h} className={`py-2 ${h === 'CPU' || h === 'MEM' ? 'text-right' : 'text-left'}
                                         pr-4 text-[9px] uppercase tracking-wider
                                         text-[var(--text-muted)]`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {services.map(svc => <ServiceRow key={svc.name} svc={svc} />)}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

const ServicesPage: NextPage = () => (
  <>
    <Head><title>Services — QuantLuna</title></Head>
    <RiskMetricsLoader />
    <StatsBar />
    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-base)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      <MetricsBadge />
      <ServiceList />
    </main>
  </>
);

export default ServicesPage;

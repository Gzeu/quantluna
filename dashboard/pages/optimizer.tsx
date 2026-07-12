/**
 * pages/optimizer.tsx
 * Optimizer page complet — status, run/stop, best params, iteratii.
 * Foloseste useOptimizer hook.
 */
import type { NextPage } from 'next';
import Head              from 'next/head';
import NavBar            from '../components/NavBar';
import { StatsBar }      from '../components/StatsBar';
import { MetricsBadge }  from '../components/MetricsBadge';
import { Spinner }       from '../components/ui/Spinner';
import { useOptimizer }  from '../hooks/useOptimizer';

const OptimizerPage: NextPage = () => {
  const { status, loading, error, run, stop } = useOptimizer(3_000);

  const lastRun = status.last_run_ts
    ? new Date(status.last_run_ts * 1000).toLocaleString('ro-RO')
    : '—';

  return (
    <>
      <Head><title>Optimizer — QuantLuna</title></Head>
      <NavBar />
      <StatsBar />
      <main
        className="animate-fade-in"
        style={{
          background: 'var(--bg-body)',
          minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
          padding: '16px 20px 40px',
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          maxWidth: 1200,
          margin: '0 auto',
          width: '100%',
        }}
      >
        <MetricsBadge />

        {/* Status card */}
        <div className="ql-card p-6">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <span className="text-2xl">🔬</span>
              <div>
                <h1 className="text-base font-bold text-[var(--text-primary)]">Optimizer</h1>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">
                  Ultimul run: {lastRun}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {loading && !status.running && <Spinner size="sm" />}
              {/* Status pill */}
              <span className={`ql-pill ${
                status.running            ? 'ql-pill-green'
                : status.auto_reoptimizer_active ? 'ql-pill-purple'
                : 'ql-pill-gray'
              }`}>
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                {status.running ? 'Running'
                  : status.auto_reoptimizer_active ? 'Auto'
                  : 'Idle'}
              </span>
              {/* Run / Stop */}
              {status.running ? (
                <button onClick={stop} className="ql-btn ql-btn-danger">
                  ⏹ Stop
                </button>
              ) : (
                <button onClick={() => run()} className="ql-btn ql-btn-primary">
                  ▶ Run
                </button>
              )}
            </div>
          </div>

          {error && (
            <div className="rounded-lg px-4 py-3 mb-4 text-sm text-red-400"
                 style={{ background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.2)' }}>
              ⚠ {error}
            </div>
          )}

          {/* Stats row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: 'Iteratii',    value: status.iterations ?? 0,   color: 'var(--cyan)' },
              { label: 'Best Score',  value: status.best_score != null
                  ? status.best_score.toFixed(4) : '—',                color: 'var(--green)' },
              { label: 'Status',      value: status.running ? 'Running' : 'Idle',
                color: status.running ? 'var(--green)' : 'var(--text-muted)' },
              { label: 'Auto Reopt.', value: status.auto_reoptimizer_active ? 'ON' : 'OFF',
                color: status.auto_reoptimizer_active ? 'var(--purple)' : 'var(--text-muted)' },
            ].map(({ label, value, color }) => (
              <div key={label}
                className="rounded-xl p-4"
                style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
              >
                <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-1">
                  {label}
                </div>
                <div className="text-xl font-bold mono" style={{ color }}>
                  {String(value)}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Best params */}
        {status.best_params && Object.keys(status.best_params).length > 0 && (
          <div className="ql-card p-6">
            <h2 className="text-xs font-bold text-[var(--text-muted)] uppercase tracking-wider mb-4">
              Best Parameters
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {Object.entries(status.best_params).map(([k, v]) => (
                <div key={k}
                  className="rounded-lg px-3 py-2"
                  style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
                >
                  <div className="text-[9px] text-[var(--text-muted)] uppercase tracking-wider">{k}</div>
                  <div className="text-sm font-bold mono text-[var(--cyan)] mt-0.5">{v}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* No params yet */}
        {!status.best_params && !loading && (
          <div className="ql-card p-10 flex flex-col items-center gap-3 text-center">
            <span className="text-3xl opacity-40">🔬</span>
            <p className="text-sm text-[var(--text-muted)]">
              Niciun parametru optimizat încă. Apasa Run pentru a porni optimizarea.
            </p>
          </div>
        )}
      </main>
    </>
  );
};

export default OptimizerPage;

/**
 * pages/optimizer.tsx — placeholder
 * Previne 404 la G+O shortcut. Va fi completat in sprint urmator.
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { StatsBar }     from '../components/StatsBar';
import { MetricsBadge } from '../components/MetricsBadge';

const OptimizerPage: NextPage = () => (
  <>
    <Head><title>Optimizer — QuantLuna</title></Head>
    <StatsBar />
    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-base)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
      }}
    >
      <section className="mb-6">
        <MetricsBadge />
      </section>
      <div className="ql-card p-10 flex flex-col items-center justify-center gap-4"
           style={{ minHeight: 320 }}>
        <span className="text-5xl">🔬</span>
        <h1 className="text-white font-bold text-2xl">Optimizer</h1>
        <p className="text-[var(--text-muted)] text-sm text-center max-w-sm">
          Panoul de optimizare va fi disponibil in sprint-ul urmator.
          Poti rula optimizari manual din backend.
        </p>
        <div className="ql-divider w-full" />
        <p className="text-[var(--text-disabled)] text-xs">
          G + O → Optimizer
        </p>
      </div>
    </main>
  </>
);

export default OptimizerPage;

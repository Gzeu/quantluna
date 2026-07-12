/**
 * pages/portfolio.tsx — placeholder
 * Previne 404 la G+P shortcut. Va fi completat in sprint urmator.
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { StatsBar }      from '../components/StatsBar';
import { MetricsBadge }  from '../components/MetricsBadge';
import { BalanceTracker } from '../components/BalanceTracker';

const PortfolioPage: NextPage = () => (
  <>
    <Head><title>Portfolio — QuantLuna</title></Head>
    <StatsBar />
    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-base)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
      }}
    >
      <section className="mb-4">
        <MetricsBadge />
      </section>
      <section>
        <BalanceTracker />
      </section>
    </main>
  </>
);

export default PortfolioPage;

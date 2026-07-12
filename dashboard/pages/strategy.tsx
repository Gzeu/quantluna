/**
 * pages/strategy.tsx
 * Strategy page — NavBar + StatsBar + MetricsBadge + StrategyScores.
 */
import type { NextPage } from 'next';
import Head              from 'next/head';
import NavBar            from '../components/NavBar';
import { StatsBar }       from '../components/StatsBar';
import { MetricsBadge }   from '../components/MetricsBadge';
import { StrategyScores } from '../components/StrategyScores';

const StrategyPage: NextPage = () => (
  <>
    <Head><title>Strategy — QuantLuna</title></Head>
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
      }}
    >
      <MetricsBadge />
      <StrategyScores fullPage />
    </main>
  </>
);

export default StrategyPage;

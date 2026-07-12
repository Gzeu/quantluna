/**
 * pages/strategy.tsx — S37 UI/UX
 * NavBar din layout, StatsBar, MetricsBadge + StrategyScores full-page
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { StatsBar }       from '../components/StatsBar';
import { MetricsBadge }   from '../components/MetricsBadge';
import { StrategyScores } from '../components/StrategyScores';

const StrategyPage: NextPage = () => (
  <>
    <Head><title>Strategy — QuantLuna</title></Head>
    <StatsBar />
    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-base)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
      }}
    >
      <section className="mb-4"><MetricsBadge /></section>
      <StrategyScores fullPage />
    </main>
  </>
);

export default StrategyPage;

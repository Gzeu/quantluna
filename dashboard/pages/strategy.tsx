/**
 * pages/strategy.tsx — S37 polish
 * Fără NavBar manual (vine din layout).
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { StatsBar }       from '../components/StatsBar';
import { MetricsBadge }   from '../components/MetricsBadge';
import { StrategyScores } from '../components/StrategyScores';

const StrategyPage: NextPage = () => (
  <>
    <Head><title>Strategy Scores — QuantLuna</title></Head>
    <StatsBar />
    <main className="min-h-screen bg-gray-950 text-white px-4 pt-4 pb-10 md:px-8">
      <div className="mb-5"><MetricsBadge /></div>
      <StrategyScores fullPage />
    </main>
  </>
);

export default StrategyPage;

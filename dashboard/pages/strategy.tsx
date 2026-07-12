/**
 * pages/strategy.tsx — S37 (review fix: folosește NavBar existent)
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { NavBar }         from '../components/NavBar';
import { StrategyScores } from '../components/StrategyScores';
import { MetricsBadge }   from '../components/MetricsBadge';

const StrategyPage: NextPage = () => (
  <>
    <Head><title>Strategy Scores — QuantLuna</title></Head>

    <NavBar activePage="strategy" />

    <main className="min-h-screen bg-gray-950 text-white px-4 pt-20 pb-8 md:px-8">
      <div className="mb-6"><MetricsBadge /></div>
      <StrategyScores />
    </main>
  </>
);

export default StrategyPage;

/**
 * pages/strategy.tsx — S37
 * Pagina dedicata Strategy Scores (full-width)
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import Link from 'next/link';
import { StrategyScores } from '../components/StrategyScores';
import { MetricsBadge }   from '../components/MetricsBadge';

const StrategyPage: NextPage = () => (
  <>
    <Head>
      <title>Strategy Scores — QuantLuna</title>
    </Head>
    <main className="min-h-screen bg-gray-950 text-white px-4 py-6 md:px-8">
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-bold">
          Quant<span className="text-cyan-400">Luna</span>
          <span className="text-gray-400 font-normal text-lg ml-3">/ Strategy Scores</span>
        </h1>
        <nav className="flex gap-4 text-sm">
          <Link href="/"         className="text-gray-400 hover:text-white transition-colors">Dashboard</Link>
          <Link href="/watchdog" className="text-gray-400 hover:text-white transition-colors">Watchdog</Link>
          <Link href="/strategy" className="text-cyan-400 font-medium">Strategy</Link>
        </nav>
      </div>
      <div className="mb-6"><MetricsBadge /></div>
      <StrategyScores />
    </main>
  </>
);

export default StrategyPage;

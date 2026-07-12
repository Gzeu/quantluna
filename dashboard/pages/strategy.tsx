/**
 * pages/strategy.tsx — S37 improved
 * Fix: NavBar default import, eliminat activePage
 * Notă: /strategy nu apare în NavBar links — e accesat din StrategyScores panel
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import NavBar           from '../components/NavBar';
import { MetricsBadge }  from '../components/MetricsBadge';
import { StrategyScores} from '../components/StrategyScores';

const StrategyPage: NextPage = () => (
  <>
    <Head><title>Strategy Scores — QuantLuna</title></Head>
    <NavBar />
    <main className="min-h-screen bg-gray-950 text-white px-4 pt-16 pb-10 md:px-8">
      <div className="mb-5"><MetricsBadge /></div>
      <StrategyScores fullPage />
    </main>
  </>
);

export default StrategyPage;

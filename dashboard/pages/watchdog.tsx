/**
 * pages/watchdog.tsx — S37 polish
 * Fără NavBar manual (vine din layout).
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { StatsBar }      from '../components/StatsBar';
import { MetricsBadge }  from '../components/MetricsBadge';
import { WatchdogPanel } from '../components/WatchdogPanel';

const WatchdogPage: NextPage = () => (
  <>
    <Head><title>Watchdog — QuantLuna</title></Head>
    <StatsBar />
    <main className="min-h-screen bg-gray-950 text-white px-4 pt-4 pb-10 md:px-8">
      <div className="mb-5"><MetricsBadge /></div>
      <WatchdogPanel fullPage />
    </main>
  </>
);

export default WatchdogPage;

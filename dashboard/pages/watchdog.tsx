/**
 * pages/watchdog.tsx — S37 (review fix: folosește NavBar existent)
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { NavBar }        from '../components/NavBar';
import { WatchdogPanel } from '../components/WatchdogPanel';
import { MetricsBadge }  from '../components/MetricsBadge';

const WatchdogPage: NextPage = () => (
  <>
    <Head><title>Watchdog — QuantLuna</title></Head>

    <NavBar activePage="watchdog" />

    <main className="min-h-screen bg-gray-950 text-white px-4 pt-20 pb-8 md:px-8">
      <div className="mb-6"><MetricsBadge /></div>
      <WatchdogPanel />
    </main>
  </>
);

export default WatchdogPage;

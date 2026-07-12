/**
 * pages/watchdog.tsx — S37 improved
 * Fix: NavBar default import, eliminat activePage
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import NavBar          from '../components/NavBar';
import { MetricsBadge } from '../components/MetricsBadge';
import { WatchdogPanel} from '../components/WatchdogPanel';

const WatchdogPage: NextPage = () => (
  <>
    <Head><title>Watchdog — QuantLuna</title></Head>
    <NavBar />
    <main className="min-h-screen bg-gray-950 text-white px-4 pt-16 pb-10 md:px-8">
      <div className="mb-5"><MetricsBadge /></div>
      <WatchdogPanel fullPage />
    </main>
  </>
);

export default WatchdogPage;

/**
 * pages/watchdog.tsx
 * Watchdog page — NavBar + StatsBar + MetricsBadge + WatchdogPanel.
 */
import type { NextPage } from 'next';
import Head              from 'next/head';
import NavBar            from '../components/NavBar';
import { StatsBar }      from '../components/StatsBar';
import { MetricsBadge }  from '../components/MetricsBadge';
import { WatchdogPanel } from '../components/WatchdogPanel';
import { ThresholdEditor } from '../components/ThresholdEditor';

const WatchdogPage: NextPage = () => (
  <>
    <Head><title>Watchdog — QuantLuna</title></Head>
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
      <WatchdogPanel fullPage />
      <ThresholdEditor fullPage />
    </main>
  </>
);

export default WatchdogPage;

/**
 * pages/index.tsx — S37 (review fix: folosește NavBar existent, elimina nav inline duplicat)
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { NavBar }          from '../components/NavBar';
import { MetricsBadge }    from '../components/MetricsBadge';
import { PnlChart }        from '../components/PnlChart';
import { StrategyScores }  from '../components/StrategyScores';
import { WatchdogPanel }   from '../components/WatchdogPanel';

const Home: NextPage = () => (
  <>
    <Head>
      <title>QuantLuna Dashboard</title>
      <meta name="description" content="QuantLuna — Crypto Pairs Trading Live Dashboard" />
    </Head>

    <NavBar activePage="dashboard" />

    <main className="min-h-screen bg-gray-950 text-white px-4 pt-20 pb-8 md:px-8">
      {/* KPI row */}
      <section className="mb-6">
        <MetricsBadge />
      </section>

      {/* PnL live chart */}
      <section className="mb-6">
        <PnlChart maxPoints={300} />
      </section>

      {/* Grid: Strategy scores + Watchdog */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <StrategyScores />
        <WatchdogPanel />
      </section>
    </main>
  </>
);

export default Home;

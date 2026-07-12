/**
 * pages/index.tsx — S37 polish
 * - Fără <NavBar /> manual (vine din app/layout.tsx care wrapează toate rutele)
 * - Init useQuantLunaWS() — pornește simulatorul + WS real
 * - StatsBar sub nav, then grid principal
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { useQuantLunaWS }  from '../hooks/useQuantLunaWS';
import { StatsBar }        from '../components/StatsBar';
import { MetricsBadge }    from '../components/MetricsBadge';
import { PnlChart }        from '../components/PnlChart';
import { StrategyScores }  from '../components/StrategyScores';
import { WatchdogPanel }   from '../components/WatchdogPanel';

const Home: NextPage = () => {
  useQuantLunaWS(); // init simulator + WS real (idempotent dacă e deja pornit)

  return (
    <>
      <Head>
        <title>QuantLuna Dashboard</title>
        <meta name="description" content="QuantLuna — Crypto Pairs Trading Live Dashboard" />
      </Head>

      {/* StatsBar: equity, daily P&L, pairs active, regime, latency, circuit breaker */}
      <StatsBar />

      <main className="min-h-[calc(100vh-52px-36px)] bg-gray-950 text-white px-4 py-5 md:px-8">
        {/* KPI badges */}
        <section className="mb-5">
          <MetricsBadge />
        </section>

        {/* PnL chart */}
        <section className="mb-5">
          <PnlChart maxPoints={300} />
        </section>

        {/* Strategy + Watchdog grid */}
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <StrategyScores />
          <WatchdogPanel />
        </section>
      </main>
    </>
  );
};

export default Home;

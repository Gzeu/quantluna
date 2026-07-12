/**
 * pages/index.tsx — S37
 * Pagina principala: MetricsBadge + PnlChart + StrategyScores + WatchdogPanel
 */
import type { NextPage } from 'next';
import Head from 'next/head';
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

    <main className="min-h-screen bg-gray-950 text-white px-4 py-6 md:px-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Quant<span className="text-cyan-400">Luna</span>
          </h1>
          <p className="text-gray-500 text-sm mt-0.5">Crypto Pairs Trading Engine v0.32.0</p>
        </div>
        <nav className="flex gap-4 text-sm">
          <a href="/"         className="text-cyan-400 font-medium">Dashboard</a>
          <a href="/watchdog" className="text-gray-400 hover:text-white transition-colors">Watchdog</a>
          <a href="/strategy" className="text-gray-400 hover:text-white transition-colors">Strategy</a>
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer"
             className="text-gray-400 hover:text-white transition-colors">API Docs</a>
        </nav>
      </div>

      {/* KPI row */}
      <section className="mb-6">
        <MetricsBadge />
      </section>

      {/* PnL chart */}
      <section className="mb-6">
        <PnlChart maxPoints={300} />
      </section>

      {/* Bottom grid: Strategy scores + Watchdog */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <StrategyScores />
        <WatchdogPanel />
      </section>
    </main>
  </>
);

export default Home;

/**
 * pages/index.tsx — S37 UI/UX complet
 * Layout masonry: toate componentele existente integrate in ordine logica.
 * NavBar vine din app/layout.tsx — nu e re-adaugat.
 * useQuantLunaWS() este init in _app.tsx — nu e re-apelat.
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { StatsBar }           from '../components/StatsBar';
import { MetricsBadge }       from '../components/MetricsBadge';
import { PnlChart }           from '../components/PnlChart';
import { StrategyScores }     from '../components/StrategyScores';
import { WatchdogPanel }      from '../components/WatchdogPanel';
import { SpreadMonitorPanel } from '../components/SpreadMonitorPanel';
import { ArbitragePanel }     from '../components/ArbitragePanel';
import { ExecutionLog }       from '../components/ExecutionLog';
import { MarketHeatmap }      from '../components/MarketHeatmap';
import { BalanceTracker }     from '../components/BalanceTracker';

const Home: NextPage = () => (
  <>
    <Head>
      <title>QuantLuna — Dashboard</title>
      <meta name="description" content="QuantLuna live pairs-trading dashboard" />
    </Head>

    <StatsBar />

    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-base)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
      }}
    >
      {/* Row 1: KPI badges — stagger mount */}
      <section className="mb-4">
        <MetricsBadge />
      </section>

      {/* Row 2: PnL chart — full width */}
      <section className="mb-4">
        <PnlChart maxPoints={300} />
      </section>

      {/* Row 3: Spread Monitor + Arbitrage */}
      <section
        className="mb-4 grid gap-4"
        style={{ gridTemplateColumns: 'minmax(0,1.3fr) minmax(0,1fr)' }}
      >
        <SpreadMonitorPanel />
        <ArbitragePanel />
      </section>

      {/* Row 4: Strategy Scores + Watchdog */}
      <section className="mb-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <StrategyScores />
        <WatchdogPanel />
      </section>

      {/* Row 5: Balance Tracker + Market Heatmap */}
      <section className="mb-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BalanceTracker />
        <MarketHeatmap />
      </section>

      {/* Row 6: Execution Log — full width */}
      <section>
        <ExecutionLog />
      </section>
    </main>
  </>
);

export default Home;

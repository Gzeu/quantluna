/**
 * pages/index.tsx — S37 metrics expansion
 * Layout masonry 6 rânduri. TradeBreakdown adăugat.
 * useRiskMetrics montat la nivel de pagină pentru a popula store.
 */
import type { NextPage } from 'next';
import Head            from 'next/head';
import { StatsBar }        from '../components/StatsBar';
import { MetricsBadge }    from '../components/MetricsBadge';
import { PnlChart }        from '../components/PnlChart';
import { TradeBreakdown }  from '../components/TradeBreakdown';
import { StrategyScores }  from '../components/StrategyScores';
import { WatchdogPanel }   from '../components/WatchdogPanel';
import { BalanceTracker }  from '../components/BalanceTracker';
import { useRiskMetrics }  from '../hooks/useRiskMetrics';

/** Mount hook la nivel de pagina — populeaza store pentru StatsBar + TradeBreakdown */
function RiskMetricsLoader() {
  useRiskMetrics();
  return null;
}

const Dashboard: NextPage = () => (
  <>
    <Head><title>Dashboard — QuantLuna</title></Head>
    <RiskMetricsLoader />
    <StatsBar />
    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-base)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
        gridAutoRows: 'min-content',
        gap: 16,
        alignItems: 'start',
      }}
    >
      {/* Row 1: MetricsBadge full-width */}
      <section style={{ gridColumn: '1/-1' }}>
        <MetricsBadge />
      </section>

      {/* Row 2: PnlChart — span 2 */}
      <section style={{ gridColumn: 'span 2' }}>
        <PnlChart />
      </section>

      {/* Row 2b: WatchdogPanel */}
      <section>
        <WatchdogPanel />
      </section>

      {/* Row 3: TradeBreakdown full-width */}
      <section style={{ gridColumn: '1/-1' }}>
        <TradeBreakdown />
      </section>

      {/* Row 4: StrategyScores full-width */}
      <section style={{ gridColumn: '1/-1' }}>
        <StrategyScores />
      </section>

      {/* Row 5: BalanceTracker */}
      <section style={{ gridColumn: '1/-1' }}>
        <BalanceTracker />
      </section>
    </main>
  </>
);

export default Dashboard;

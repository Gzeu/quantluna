/**
 * pages/index.tsx — S37 polish
 * Dashboard complet — toate componentele existente in grid masonry.
 * NavBar inclus + RiskMetricsLoader + toate paneluri.
 */
import type { NextPage } from 'next';
import Head              from 'next/head';
import NavBar            from '../components/NavBar';
import {
  StatsBar,
  MetricsBadge,
  PnlChart,
  TradeBreakdown,
  StrategyScores,
  WatchdogPanel,
  BalanceTracker,
  ArbitragePanel,
  SpreadMonitorPanel,
  ExecutionLog,
  MarketHeatmap,
  CandlestickChart
} from '../components';
import { useRiskMetrics }    from '../hooks/useRiskMetrics';

function RiskMetricsLoader() {
  useRiskMetrics();
  return null;
}

const grid = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
  gridAutoRows: 'min-content',
  gap: 16,
  alignItems: 'start',
} as const;

const full  = { gridColumn: '1/-1' } as const;
const span2 = { gridColumn: 'span 2' } as const;

const Dashboard: NextPage = () => (
  <>
    <Head><title>Dashboard — QuantLuna</title></Head>
    <RiskMetricsLoader />
    <NavBar />
    <StatsBar />
    <main
      className="animate-fade-in"
      style={{
        background: 'var(--bg-body)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
        ...grid,
      }}
    >
      {/* 1 — MetricsBadge full-width */}
      <section style={full}>
        <MetricsBadge />
      </section>

      {/* 2 — PnL chart span 2 + Watchdog */}
      <section style={span2}>
        <PnlChart />
      </section>
      <section>
        <WatchdogPanel />
      </section>

      {/* 3 — Spread Monitor + Arbitrage Panel */}
      <section>
        <SpreadMonitorPanel />
      </section>
      <section>
        <ArbitragePanel />
      </section>

      {/* 4 — TradeBreakdown full-width */}
      <section style={full}>
        <TradeBreakdown />
      </section>

      {/* 5 — StrategyScores full-width */}
      <section style={full}>
        <StrategyScores />
      </section>

      {/* 6 — Candlestick span2 + MarketHeatmap */}
      <section style={span2}>
        <CandlestickChart />
      </section>
      <section>
        <MarketHeatmap />
      </section>

      {/* 7 — ExecutionLog full-width */}
      <section style={full}>
        <ExecutionLog />
      </section>

      {/* 8 — BalanceTracker full-width */}
      <section style={full}>
        <BalanceTracker />
      </section>
    </main>
  </>
);

export default Dashboard;

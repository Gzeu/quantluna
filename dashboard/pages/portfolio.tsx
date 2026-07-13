/**
 * pages/portfolio.tsx
 * Portfolio page — NavBar + StatsBar + MetricsBadge + BalanceTracker.
 */
import type { NextPage } from 'next';
import Head             from 'next/head';
import NavBar           from '../components/NavBar';
import {
  StatsBar,
  MetricsBadge,
  BalanceTracker,
  PnlChart,
  TradeBreakdown
} from '../components';

const PortfolioPage: NextPage = () => (
  <>
    <Head><title>Portfolio — QuantLuna</title></Head>
    <NavBar />
    <StatsBar />
    <main
      className="animate-fade-in stagger"
      style={{
        background: 'var(--bg-body)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
        gap: 16,
        alignItems: 'start',
      }}
    >
      <section style={{ gridColumn: '1/-1' }}>
        <MetricsBadge />
      </section>
      <section style={{ gridColumn: 'span 2' }}>
        <PnlChart />
      </section>
      <section style={{ gridColumn: '1/-1' }}>
        <BalanceTracker />
      </section>
      <section style={{ gridColumn: '1/-1' }}>
        <TradeBreakdown />
      </section>
    </main>
  </>
);

export default PortfolioPage;

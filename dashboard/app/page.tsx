/**
 * app/page.tsx — Dashboard principal QuantLuna
 */
'use client';

import { useEffect } from 'react';
import NavBar from '../components/NavBar';
import StatsBar from '../components/StatsBar';
import PnlChart from '../components/PnlChart';
import WatchdogPanel from '../components/WatchdogPanel';
import SpreadMonitorPanel from '../components/SpreadMonitorPanel';
import ArbitragePanel from '../components/ArbitragePanel';
import TradeBreakdown from '../components/TradeBreakdown';
import StrategyScores from '../components/StrategyScores';
import CandlestickChart from '../components/CandlestickChart';
import MarketHeatmap from '../components/MarketHeatmap';
import ExecutionLog from '../components/ExecutionLog';
import BalanceTracker from '../components/BalanceTracker';
import MetricsBadge from '../components/MetricsBadge';
import { useQuantLunaWS } from '../hooks/useQuantLunaWS';

function WSInit() {
  useQuantLunaWS();
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

export default function Dashboard() {
  return (
    <>
      <WSInit />
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
        <section style={full}>
          <MetricsBadge />
        </section>
        <section style={span2}>
          <PnlChart />
        </section>
        <section>
          <WatchdogPanel />
        </section>
        <section>
          <SpreadMonitorPanel />
        </section>
        <section>
          <ArbitragePanel />
        </section>
        <section style={full}>
          <TradeBreakdown />
        </section>
        <section style={full}>
          <StrategyScores />
        </section>
        <section style={span2}>
          <CandlestickChart />
        </section>
        <section>
          <MarketHeatmap />
        </section>
        <section style={full}>
          <ExecutionLog />
        </section>
        <section style={full}>
          <BalanceTracker />
        </section>
      </main>
    </>
  );
}

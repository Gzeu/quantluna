/**
 * pages/index.tsx — v4.0 CyberDark Pro Dashboard
 * Glassmorphism grid • Live pulse indicators • Sparklines • Staggered animation
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import NavBar from '../components/NavBar';
import {
  StatsBar, MetricsBadge, PnlChart, DrawdownChart,
  TradeBreakdown, StrategyScores, WatchdogPanel,
  BalanceTracker, ArbitragePanel, SpreadMonitorPanel,
  ExecutionLog, MarketHeatmap, CandlestickChart
} from '../components';
import { useRiskMetrics } from '../hooks/useRiskMetrics';

function RiskMetricsLoader() {
  useRiskMetrics();
  return null;
}

/* ── CSS-in-JS grid system ──────────────────────────────────────── */
const S = {
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
    gridAutoRows: 'min-content',
    gap: 18,
    alignItems: 'start',
  } as const,
  full:  { gridColumn: '1 / -1' } as const,
  span2: { gridColumn: 'span 2' } as const,
  hero: {
    gridColumn: '1 / -1',
    background: 'var(--grad-brand-subtle)',
    border: '1px solid var(--border-glow)',
    borderRadius: 'var(--radius-lg)',
    padding: '24px 28px',
    boxShadow: 'var(--shadow-glow-purple)',
    position: 'relative',
    overflow: 'hidden',
  } as const,
};

const GlassRow = ({ children, style }: { children: React.ReactNode; style?: object }) => (
  <section
    className="ql-card-glass animate-fade-up"
    style={{
      padding: '20px 24px',
      minHeight: 200,
      display: 'flex', flexDirection: 'column',
      ...style,
    }}
  >
    {children}
  </section>
);

const Dashboard: NextPage = () => (
  <>
    <Head><title>Dashboard — QuantLuna</title></Head>
    <RiskMetricsLoader />
    <NavBar />
    <StatsBar />

    <main
      className="animate-fade-in stagger"
      style={{
        background: 'var(--bg-body)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '20px 28px 48px',
        ...S.grid,
      }}
    >
      {/* ── Hero metrics bar ────────────────────────────────── */}
      <section style={S.hero}>
        <div className="flex items-center gap-3 mb-2">
          <span className="live-dot active" />
          <span className="text-xs font-bold uppercase tracking-widest text-purple-bright">Live Trading Dashboard</span>
          <span className="ql-pill ql-pill-green text-[9px]">● Paper Mode</span>
        </div>
        <MetricsBadge />
      </section>

      {/* ── Row 1: PnL Chart (2x) + Watchdog ───────────────── */}
      <GlassRow style={S.span2}>
        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">PnL Performance</span>
        <PnlChart />
      </GlassRow>
      <GlassRow>
        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">Watchdog Status</span>
        <WatchdogPanel />
      </GlassRow>

      {/* ── Row 2: Drawdown Chart (2x) ─────────────────────── */}
      <GlassRow style={S.span2}>
        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">Drawdown Profile</span>
        <DrawdownChart />
      </GlassRow>

      {/* ── Row 3: Spread + Arbitrage ──────────────────────── */}
      <GlassRow>
        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">Spread Monitor</span>
        <SpreadMonitorPanel />
      </GlassRow>
      <GlassRow>
        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">Arbitrage Signals</span>
        <ArbitragePanel />
      </GlassRow>

      {/* ── Row 4: Trade Breakdown (full) ──────────────────── */}
      <section style={S.full} className="ql-card animate-fade-up" >
        <div style={{ padding: '20px 24px' }}>
          <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">Trade Breakdown</span>
          <TradeBreakdown />
        </div>
      </section>

      {/* ── Row 5: Strategy Scores (full) ──────────────────── */}
      <section style={S.full} className="ql-card animate-fade-up" >
        <div style={{ padding: '20px 24px' }}>
          <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">Strategy Scores</span>
          <StrategyScores />
        </div>
      </section>

      {/* ── Row 6: Candlestick (2x) + Heatmap ──────────────── */}
      <GlassRow style={S.span2}>
        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">Candlestick Chart</span>
        <CandlestickChart />
      </GlassRow>
      <GlassRow>
        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] mb-3">Market Heatmap</span>
        <MarketHeatmap />
      </GlassRow>

      {/* ── Row 7: Execution Log (full) ────────────────────── */}
      <section style={S.full} className="ql-card animate-fade-up" >
        <div style={{ padding: '20px 24px' }}>
          <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">Execution Log</span>
          <ExecutionLog />
        </div>
      </section>

      {/* ── Row 8: Balance Tracker (full) ──────────────────── */}
      <section style={S.full} className="ql-card animate-fade-up" >
        <div style={{ padding: '20px 24px' }}>
          <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">Balance Tracker</span>
          <BalanceTracker />
        </div>
      </section>
    </main>
  </>
);

export default Dashboard;

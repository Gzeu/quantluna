/**
 * QuantLuna Dashboard — Main Page
 * Sprint 30
 */
import { Suspense } from 'react';
import { RiskMetrics }  from './components/RiskMetrics';
import { EquityCurve }  from './components/EquityCurve';
import { PairsGrid }    from './components/PairsGrid';
import { AlertFeed }    from './components/AlertFeed';
import { getEquityCurve } from './lib/api';

export const dynamic    = 'force-dynamic';
export const revalidate = 0;

export default async function DashboardPage() {
  // Fetch initial equity history SSR
  let equityHistory = [];
  try {
    equityHistory = await getEquityCurve();
  } catch { /* API offline la build time */ }

  return (
    <main className="min-h-screen bg-surface text-white">
      {/* Header */}
      <header className="border-b border-border px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold text-brand">QuantLuna</span>
          <span className="text-xs text-slate-500 font-mono">v0.30.0</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-success animate-pulse" />
          <span className="text-xs text-slate-400">Live</span>
        </div>
      </header>

      {/* Body */}
      <div className="px-6 py-5 space-y-5 max-w-screen-2xl mx-auto">
        {/* Row 1: Metric Cards */}
        <Suspense fallback={<div className="h-24 bg-card rounded-xl animate-pulse" />}>
          <RiskMetrics />
        </Suspense>

        {/* Row 2: Equity Curve + Alert Feed */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2">
            <Suspense fallback={<div className="h-72 bg-card rounded-xl animate-pulse" />}>
              <EquityCurve history={equityHistory} />
            </Suspense>
          </div>
          <div>
            <Suspense fallback={<div className="h-72 bg-card rounded-xl animate-pulse" />}>
              <AlertFeed />
            </Suspense>
          </div>
        </div>

        {/* Row 3: Pairs Grid */}
        <Suspense fallback={<div className="h-48 bg-card rounded-xl animate-pulse" />}>
          <PairsGrid />
        </Suspense>
      </div>
    </main>
  );
}

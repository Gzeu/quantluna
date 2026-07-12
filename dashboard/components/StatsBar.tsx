/**
 * StatsBar.tsx — S37 polish (NOU)
 * Bară statistică globală sub NavBar:
 * uptime server, trades azi, P&L zilnic, pairs active, latency WS
 * Date din useQuantLunaStore (sync cu simulatorul existent)
 */
import React from 'react';
import { useQuantLunaStore } from '../store/quantlunaStore';

function Stat({
  label, value, sub, color,
}: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="flex flex-col items-center px-4 py-1.5 border-r border-gray-800 last:border-r-0">
      <span className="text-gray-500 text-[10px] uppercase tracking-widest">{label}</span>
      <span className={`text-sm font-bold tabular-nums ${
        color ?? 'text-white'
      }`}>{value}</span>
      {sub && <span className="text-[10px] text-gray-600">{sub}</span>}
    </div>
  );
}

export function StatsBar() {
  const pnl    = useQuantLunaStore(s => s.pnl);
  const regime = useQuantLunaStore(s => s.regime);
  const pairs  = useQuantLunaStore(s => s.pairs);

  const dailyPnl  = pnl?.dailyPnl  ?? 0;
  const dailyPct  = pnl?.dailyPct  ?? 0;
  const total     = pnl?.total     ?? 0;
  const latency   = regime?.latencyMs ?? null;
  const activePairs = pairs.filter(p => p.position !== 'FLAT').length;

  const pnlColor  = dailyPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const latColor  = latency === null ? 'text-gray-600'
                  : latency < 80    ? 'text-green-400'
                  : latency < 200   ? 'text-yellow-400'
                  : 'text-red-400';

  return (
    <div className="w-full bg-gray-950 border-b border-gray-800 flex items-stretch overflow-x-auto">
      <Stat label="Equity"
            value={`$${total.toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
            sub="total" />
      <Stat label="Daily P&L"
            value={`${dailyPnl >= 0 ? '+' : ''}$${dailyPnl.toFixed(2)}`}
            sub={`${dailyPct >= 0 ? '+' : ''}${dailyPct.toFixed(3)}%`}
            color={pnlColor} />
      <Stat label="Active Pairs"
            value={`${activePairs} / ${pairs.length}`}
            sub="in position" />
      <Stat label="Regime"
            value={regime?.regime ?? '—'}
            color={
              regime?.regime === 'LOW'    ? 'text-green-400'
            : regime?.regime === 'HIGH'   ? 'text-yellow-400'
            : regime?.regime === 'EXTREME'? 'text-red-400'
            : 'text-gray-300'
            } />
      <Stat label="WS Latency"
            value={latency !== null ? `${latency}ms` : '—'}
            color={latColor} />
      <Stat label="Circuit Breaker"
            value={regime?.cbOpen ? 'OPEN' : 'OK'}
            color={regime?.cbOpen ? 'text-red-400 animate-pulse' : 'text-green-400'} />
    </div>
  );
}

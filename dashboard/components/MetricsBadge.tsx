/**
 * MetricsBadge.tsx — S37 UI/UX
 * Card per metric cu accent border-top, mini progress bar, delta sub-label.
 * Skeleton shimmer la loading. Stagger la mount. Flash la equity change.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Spinner } from './ui/Spinner';

interface RiskMetrics {
  rolling_sharpe:   number;
  drawdown_current: number;
  win_rate:         number;
  exposure_usd:     number;
  equity_usd:       number;
}

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

function MetricSkeleton() {
  return (
    <div className="flex flex-wrap gap-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="skeleton rounded-xl" style={{ width: 130, height: 76 }} />
      ))}
    </div>
  );
}

function ProgressBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div
      className="w-full rounded-full overflow-hidden mt-2"
      style={{ height: 2, background: 'rgba(255,255,255,0.06)' }}
    >
      <div
        className={`h-full rounded-full ${color}`}
        style={{
          width: `${Math.min(100, Math.max(0, pct))}%`,
          transition: 'width 0.7s ease',
        }}
      />
    </div>
  );
}

interface MetricCardProps {
  label:     string;
  value:     string;
  sub?:      string;
  pct?:      number;
  barColor?: string;
  accent:    string;
  flash?:    boolean;
  tooltip:   string;
}

function MetricCard({
  label, value, sub, pct, barColor, accent, flash, tooltip,
}: MetricCardProps) {
  return (
    <div
      data-tooltip={tooltip}
      className={`
        ql-card flex flex-col px-4 py-3 min-w-[128px] cursor-default
        border-t-2 ${accent}
        transition-all duration-300
        ${flash ? 'ring-1 ring-cyan-500/40 ring-offset-1 ring-offset-[var(--bg-base)]' : ''}
      `}
    >
      <span className="text-[9px] uppercase tracking-widest text-[var(--text-muted)] mb-1">
        {label}
      </span>
      <span className={`text-xl font-bold tabular leading-none ${
        flash ? 'text-cyan-300' : 'text-white'
      }`}>
        {value}
      </span>
      {sub && (
        <span className="text-[10px] text-[var(--text-muted)] mt-0.5 leading-tight">{sub}</span>
      )}
      {pct !== undefined && barColor && (
        <ProgressBar pct={pct} color={barColor} />
      )}
    </div>
  );
}

export function MetricsBadge() {
  const [m,     setM]     = useState<RiskMetrics | null>(null);
  const [err,   setErr]   = useState(false);
  const [flash, setFlash] = useState(false);
  const prevEquity = useRef<number | null>(null);

  useEffect(() => {
    const load = () =>
      fetch(`${API}/risk/dashboard`)
        .then(r => r.json())
        .then((next: RiskMetrics) => {
          if (
            prevEquity.current !== null &&
            Math.abs(next.equity_usd - prevEquity.current) > 0.01
          ) {
            setFlash(true);
            setTimeout(() => setFlash(false), 800);
          }
          prevEquity.current = next.equity_usd;
          setM(next);
        })
        .catch(() => setErr(true));
    load();
    const id = setInterval(load, 5_000);
    return () => clearInterval(id);
  }, []);

  if (err) return (
    <div className="flex items-center gap-2 text-red-400 text-sm">
      <span>⚠️</span> Risk dashboard unavailable
    </div>
  );
  if (!m) return <MetricSkeleton />;

  const fmt = (n: number) =>
    n.toLocaleString('en-US', { maximumFractionDigits: 0 });

  const ddPct  = m.drawdown_current * 100;
  const wrPct  = m.win_rate * 100;
  const expPct = m.equity_usd > 0 ? (m.exposure_usd / m.equity_usd) * 100 : 0;

  return (
    <div className="flex flex-wrap gap-3 stagger">
      <MetricCard
        label="Equity" value={`$${fmt(m.equity_usd)}`}
        accent="border-cyan-600"
        tooltip="Total equity USD — polling 5s"
        flash={flash}
        pct={100} barColor="bg-cyan-500"
      />
      <MetricCard
        label="Sharpe 30d" value={m.rolling_sharpe.toFixed(2)}
        accent={m.rolling_sharpe > 1 ? 'border-green-600'
              : m.rolling_sharpe > 0 ? 'border-yellow-600'
              : 'border-red-600'}
        tooltip="Sharpe ratio rolling 30 zile; verde >1, galben >0"
        pct={Math.min(100, m.rolling_sharpe * 50)}
        barColor={m.rolling_sharpe > 1 ? 'bg-green-500'
                : m.rolling_sharpe > 0 ? 'bg-yellow-500'
                : 'bg-red-500'}
      />
      <MetricCard
        label="Drawdown" value={`${ddPct.toFixed(1)}%`}
        sub={ddPct > 10 ? '⚠ above limit' : undefined}
        accent={ddPct > 10 ? 'border-red-600'
              : ddPct > 5  ? 'border-yellow-600'
              : 'border-green-600'}
        tooltip="Drawdown curent; rosu >10%, galben >5%"
        pct={ddPct * 5} barColor={ddPct > 10 ? 'bg-red-500' : ddPct > 5 ? 'bg-yellow-500' : 'bg-green-500'}
      />
      <MetricCard
        label="Win Rate" value={`${wrPct.toFixed(1)}%`}
        accent={wrPct >= 55 ? 'border-green-600' : wrPct >= 45 ? 'border-yellow-600' : 'border-red-600'}
        tooltip="Win rate trade-uri inchise"
        pct={wrPct}
        barColor={wrPct >= 55 ? 'bg-green-500' : wrPct >= 45 ? 'bg-yellow-500' : 'bg-red-500'}
      />
      <MetricCard
        label="Exposure" value={`$${fmt(m.exposure_usd)}`}
        sub={`${expPct.toFixed(1)}% of equity`}
        accent="border-purple-600"
        tooltip="Valoare totala pozitii deschise"
        pct={expPct} barColor="bg-purple-500"
      />
    </div>
  );
}

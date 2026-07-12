/**
 * MetricsBadge.tsx — S37 improved
 * Skeleton loading + fade-in la update + tooltip pe fiecare badge
 */
import React, { useEffect, useState, useRef } from 'react';

interface RiskMetrics {
  rolling_sharpe:   number;
  drawdown_current: number;
  win_rate:         number;
  exposure_usd:     number;
  equity_usd:       number;
}

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

function Skeleton() {
  return (
    <div className="flex flex-wrap gap-3">
      {[...Array(5)].map((_, i) => (
        <div key={i} className="animate-pulse bg-gray-800 rounded-xl w-28 h-16" />
      ))}
    </div>
  );
}

interface BadgeProps {
  label:   string;
  value:   string;
  color:   string;
  tooltip: string;
  flash?:  boolean;
}

function Badge({ label, value, color, tooltip, flash }: BadgeProps) {
  return (
    <div
      title={tooltip}
      className={`
        flex flex-col items-center px-4 py-3 rounded-xl min-w-[110px] cursor-default
        transition-all duration-300 ${color}
        ${flash ? 'ring-1 ring-cyan-400 ring-offset-1 ring-offset-gray-950' : ''}
      `}
    >
      <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">{label}</span>
      <span className="text-xl font-bold text-white mt-1 tabular-nums">{value}</span>
    </div>
  );
}

export function MetricsBadge() {
  const [m,    setM]    = useState<RiskMetrics | null>(null);
  const [err,  setErr]  = useState(false);
  const [flash,setFlash]= useState(false);
  const prev = useRef<RiskMetrics | null>(null);

  useEffect(() => {
    const fetch_ = () =>
      fetch(`${API}/risk/dashboard`)
        .then(r => r.json())
        .then((next: RiskMetrics) => {
          if (prev.current !== null &&
              Math.abs(next.equity_usd - prev.current.equity_usd) > 0.01) {
            setFlash(true);
            setTimeout(() => setFlash(false), 800);
          }
          prev.current = next;
          setM(next);
        })
        .catch(() => setErr(true));
    fetch_();
    const id = setInterval(fetch_, 5_000);
    return () => clearInterval(id);
  }, []);

  if (err) return <p className="text-red-400 text-sm">Risk dashboard unavailable</p>;
  if (!m)  return <Skeleton />;

  const ddColor = m.drawdown_current > 0.10 ? 'bg-red-900'
                : m.drawdown_current > 0.05 ? 'bg-yellow-900'
                : 'bg-green-900';
  const srColor = m.rolling_sharpe   > 1.0  ? 'bg-green-900'
                : m.rolling_sharpe   > 0    ? 'bg-yellow-900'
                : 'bg-red-900';

  const fmt = (n: number) =>
    n.toLocaleString('en-US', { maximumFractionDigits: 0 });

  return (
    <div className="flex flex-wrap gap-3">
      <Badge label="Equity"   value={`$${fmt(m.equity_usd)}`}
             color="bg-gray-800" flash={flash}
             tooltip="Total equity USD — actualizat la 5s" />
      <Badge label="Sharpe"   value={m.rolling_sharpe.toFixed(2)}
             color={srColor}
             tooltip="Sharpe ratio rolling 30 zile; verde >1.0, galben >0" />
      <Badge label="Drawdown" value={`${(m.drawdown_current*100).toFixed(1)}%`}
             color={ddColor}
             tooltip="Drawdown curent; roșu >10%, galben >5%" />
      <Badge label="Win Rate" value={`${(m.win_rate*100).toFixed(1)}%`}
             color="bg-gray-800"
             tooltip="Win rate trade-uri închise" />
      <Badge label="Exposure" value={`$${fmt(m.exposure_usd)}`}
             color="bg-gray-800"
             tooltip="Expunere totală USD (valoare poziții deschise)" />
    </div>
  );
}

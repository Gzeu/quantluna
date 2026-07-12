/**
 * MetricsBadge.tsx — S37
 * KPI badges: Sharpe rolling, Drawdown current, Win Rate, Exposure USD
 * Date din /risk/dashboard (polling 5s)
 */
import React, { useEffect, useState } from 'react';

interface RiskMetrics {
  rolling_sharpe: number;
  drawdown_current: number;
  win_rate: number;
  exposure_usd: number;
  equity_usd: number;
}

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

function badge(label: string, value: string, color: string) {
  return (
    <div className={`flex flex-col items-center px-4 py-3 rounded-xl ${color} min-w-[110px]`}>
      <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">{label}</span>
      <span className="text-xl font-bold text-white mt-1">{value}</span>
    </div>
  );
}

export function MetricsBadge() {
  const [m, setM] = useState<RiskMetrics | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    const fetch_ = () =>
      fetch(`${API}/risk/dashboard`)
        .then(r => r.json())
        .then(setM)
        .catch(() => setErr(true));
    fetch_();
    const id = setInterval(fetch_, 5_000);
    return () => clearInterval(id);
  }, []);

  if (err) return <p className="text-red-400 text-sm">Risk dashboard unavailable</p>;
  if (!m)  return <p className="text-gray-500 text-sm">Loading metrics…</p>;

  const ddColor = m.drawdown_current > 0.1 ? 'bg-red-900' : m.drawdown_current > 0.05 ? 'bg-yellow-900' : 'bg-green-900';
  const srColor = m.rolling_sharpe  > 1.0  ? 'bg-green-900' : m.rolling_sharpe > 0 ? 'bg-yellow-900' : 'bg-red-900';

  return (
    <div className="flex flex-wrap gap-3">
      {badge('Equity', `$${m.equity_usd.toLocaleString('en-US', { maximumFractionDigits: 0 })}`, 'bg-gray-800')}
      {badge('Sharpe', m.rolling_sharpe.toFixed(2), srColor)}
      {badge('Drawdown', `${(m.drawdown_current * 100).toFixed(1)}%`, ddColor)}
      {badge('Win Rate', `${(m.win_rate * 100).toFixed(1)}%`, 'bg-gray-800')}
      {badge('Exposure', `$${m.exposure_usd.toLocaleString('en-US', { maximumFractionDigits: 0 })}`, 'bg-gray-800')}
    </div>
  );
}

'use client';
/**
 * QuantLuna Dashboard — Risk Metrics Cards
 * Sprint 30
 *
 * 6 metric cards: Equity, PnL, Sharpe, Drawdown, Win Rate, Exposure.
 * Date live via SSE /risk/stream.
 */
import { useSSE } from '../hooks/useSSE';
import type { RiskSnapshot } from '../lib/api';
import { TrendingUp, TrendingDown, Activity, AlertTriangle, Target, Layers } from 'lucide-react';

const INITIAL: RiskSnapshot = {
  timestamp: '', equity_usdt: 0, pnl_usdt: 0,
  sharpe_rolling: 0, max_drawdown: 0, current_drawdown: 0,
  win_rate: 0, total_trades: 0, open_positions: 0, exposure_pct: 0,
};

interface MetricCardProps {
  label:    string;
  value:    string;
  sub?:     string;
  color?:   string;
  icon:     React.ReactNode;
}

function MetricCard({ label, value, sub, color = 'text-white', icon }: MetricCardProps) {
  return (
    <div className="bg-card rounded-xl p-4 border border-border flex flex-col gap-2">
      <div className="flex items-center justify-between text-slate-400">
        <span className="text-xs uppercase tracking-wider">{label}</span>
        <span className="opacity-60">{icon}</span>
      </div>
      <p className={`text-2xl font-mono font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export function RiskMetrics() {
  const d = useSSE<RiskSnapshot>('/risk/stream', INITIAL);

  const pnlColor     = d.pnl_usdt >= 0      ? 'text-success' : 'text-danger';
  const sharpeColor  = d.sharpe_rolling >= 1 ? 'text-success' : d.sharpe_rolling >= 0 ? 'text-warning' : 'text-danger';
  const ddColor      = d.current_drawdown <= 0.05 ? 'text-success' : d.current_drawdown <= 0.10 ? 'text-warning' : 'text-danger';
  const wrColor      = d.win_rate >= 0.55    ? 'text-success' : d.win_rate >= 0.45 ? 'text-warning' : 'text-danger';

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <MetricCard
        label="Equity"
        value={`$${d.equity_usdt.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
        sub={`${d.open_positions} pozitii deschise`}
        icon={<Layers size={16} />}
      />
      <MetricCard
        label="PnL Total"
        value={`${d.pnl_usdt >= 0 ? '+' : ''}${d.pnl_usdt.toFixed(2)} USDT`}
        color={pnlColor}
        sub={`${d.total_trades} trades totale`}
        icon={d.pnl_usdt >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
      />
      <MetricCard
        label="Sharpe Rolling"
        value={d.sharpe_rolling.toFixed(3)}
        color={sharpeColor}
        sub="fereastra 30 zile"
        icon={<Activity size={16} />}
      />
      <MetricCard
        label="Drawdown Curent"
        value={`${(d.current_drawdown * 100).toFixed(2)}%`}
        color={ddColor}
        sub={`Max: ${(d.max_drawdown * 100).toFixed(2)}%`}
        icon={<AlertTriangle size={16} />}
      />
      <MetricCard
        label="Win Rate"
        value={`${(d.win_rate * 100).toFixed(1)}%`}
        color={wrColor}
        sub={`${d.total_trades} trades`}
        icon={<Target size={16} />}
      />
      <MetricCard
        label="Expunere"
        value={`${(d.exposure_pct * 100).toFixed(1)}%`}
        color={d.exposure_pct > 0.80 ? 'text-danger' : 'text-white'}
        sub="din capital total"
        icon={<Layers size={16} />}
      />
    </div>
  );
}

'use client';
/**
 * QuantLuna Dashboard — Equity Curve Chart
 * Sprint 30
 *
 * Recharts LineChart — consuma SSE /risk/stream pentru update live.
 * Afiseaza: equity USDT + PnL cumulat pe doua axe.
 */
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { format, parseISO } from 'date-fns';
import { useSSE } from '../hooks/useSSE';
import type { RiskSnapshot, EquityPoint } from '../lib/api';

interface Props {
  history: EquityPoint[];
}

export function EquityCurve({ history }: Props) {
  const live = useSSE<RiskSnapshot | null>('/risk/stream', null);

  // Append live point la history
  const data = live
    ? [...history, { ts: live.timestamp, equity: live.equity_usdt, pnl: live.pnl_usdt }]
    : history;

  const fmt = (ts: string) => {
    try { return format(parseISO(ts), 'MM/dd HH:mm'); }
    catch { return ts; }
  };

  const lastEquity = data.at(-1)?.equity ?? 0;
  const firstEquity = data[0]?.equity ?? lastEquity;
  const pctChange   = firstEquity > 0 ? ((lastEquity - firstEquity) / firstEquity) * 100 : 0;
  const isUp        = pctChange >= 0;

  return (
    <div className="bg-card rounded-xl p-5 border border-border">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-white">Equity Curve</h2>
        <span className={`text-sm font-mono font-bold ${
          isUp ? 'text-success' : 'text-danger'
        }`}>
          {isUp ? '▲' : '▼'} {Math.abs(pctChange).toFixed(2)}%
        </span>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis
            dataKey="ts"
            tickFormatter={fmt}
            tick={{ fill: '#94a3b8', fontSize: 11 }}
            tickLine={false}
          />
          <YAxis
            yAxisId="equity"
            orientation="left"
            tick={{ fill: '#94a3b8', fontSize: 11 }}
            tickFormatter={(v) => `$${(v as number).toLocaleString()}`}
            tickLine={false}
          />
          <YAxis
            yAxisId="pnl"
            orientation="right"
            tick={{ fill: '#94a3b8', fontSize: 11 }}
            tickFormatter={(v) => `${(v as number) >= 0 ? '+' : ''}${(v as number).toFixed(0)}`}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
            labelFormatter={fmt}
            formatter={(value: number, name: string) => [
              name === 'equity' ? `$${value.toLocaleString()}` : `${value >= 0 ? '+' : ''}${value.toFixed(2)} USDT`,
              name === 'equity' ? 'Equity' : 'PnL',
            ]}
          />
          <Legend wrapperStyle={{ color: '#94a3b8', fontSize: 12 }} />
          <ReferenceLine yAxisId="pnl" y={0} stroke="#475569" strokeDasharray="4 4" />
          <Line
            yAxisId="equity"
            type="monotone"
            dataKey="equity"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: '#6366f1' }}
          />
          <Line
            yAxisId="pnl"
            type="monotone"
            dataKey="pnl"
            stroke="#22c55e"
            strokeWidth={1.5}
            dot={false}
            strokeDasharray="4 2"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * PnlChart.tsx — S37 improved
 * Recharts ComposedChart: equity AreaChart + net_pnl BarChart
 * Butoane: zoom ultim N puncte (50/100/200/ALL) + export CSV
 */
'use client';
import React, { useState, useMemo } from 'react';
import {
  ComposedChart, Area, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Legend,
} from 'recharts';
import { usePnlStream } from '../hooks/usePnlStream';

const ZOOM_OPTIONS = [50, 100, 200, 0] as const;
const ZOOM_LABELS  = ['50', '100', '200', 'ALL'];

interface Props { maxPoints?: number; }

function exportCsv(data: { ts: number; equity: number; net_pnl?: number }[]) {
  const rows = ['ts,equity,net_pnl', ...data.map(d =>
    `${new Date(d.ts).toISOString()},${d.equity},${d.net_pnl ?? ''}`
  )].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([rows], { type: 'text/csv' }));
  a.download = `pnl_${Date.now()}.csv`;
  a.click();
}

export function PnlChart({ maxPoints = 200 }: Props) {
  const { data, connected, error } = usePnlStream(maxPoints);
  const [zoom, setZoom] = useState<number>(0); // 0 = ALL

  const visible = useMemo(() =>
    zoom > 0 ? data.slice(-zoom) : data,
    [data, zoom]
  );

  const firstEquity = visible[0]?.equity;

  return (
    <div className="bg-gray-900 rounded-2xl p-5 w-full">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-white font-semibold text-lg">Live PnL — Equity Curve</h2>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            connected ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
          }`}>
            {connected ? '◉ LIVE' : error ? '✕ ERROR' : '○ Connecting…'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Zoom buttons */}
          <div className="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
            {ZOOM_OPTIONS.map((z, i) => (
              <button
                key={z}
                onClick={() => setZoom(z)}
                className={`px-2.5 py-1 transition-colors ${
                  zoom === z ? 'bg-cyan-800 text-cyan-200' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >{ZOOM_LABELS[i]}</button>
            ))}
          </div>
          {/* Export CSV */}
          <button
            onClick={() => exportCsv(visible)}
            disabled={visible.length === 0}
            className="text-xs px-3 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 transition-colors disabled:opacity-40"
          >↓ CSV</button>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="h-52 flex items-center justify-center text-gray-500 text-sm">
          Waiting for stream data…
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={visible} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#22d3ee" stopOpacity={0.30} />
                <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="ts"
              tick={{ fill: '#6b7280', fontSize: 10 }}
              tickFormatter={v => new Date(v as number).toLocaleTimeString()}
              minTickGap={45}
            />
            <YAxis
              yAxisId="equity"
              tick={{ fill: '#6b7280', fontSize: 10 }}
              tickFormatter={v => `$${(v as number).toLocaleString()}`}
              width={78}
            />
            <YAxis
              yAxisId="pnl"
              orientation="right"
              tick={{ fill: '#6b7280', fontSize: 10 }}
              tickFormatter={v => `$${(v as number).toLocaleString()}`}
              width={70}
            />
            <Tooltip
              contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
              labelFormatter={(v: number) => new Date(v).toLocaleTimeString()}
              formatter={(v: number, name: string) => [
                `$${v.toLocaleString('en-US', { minimumFractionDigits: 2 })}`,
                name === 'equity' ? 'Equity' : 'Net PnL',
              ]}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, color: '#9ca3af', paddingTop: 8 }}
              formatter={v => v === 'equity' ? 'Equity' : 'Net PnL'}
            />
            {firstEquity !== undefined && (
              <ReferenceLine yAxisId="equity" y={firstEquity} stroke="#374151" strokeDasharray="4 2" />
            )}
            <Area
              yAxisId="equity"
              type="monotone"
              dataKey="equity"
              stroke="#22d3ee"
              strokeWidth={2}
              fill="url(#pnlGrad)"
              dot={false}
              isAnimationActive={false}
            />
            <Bar
              yAxisId="pnl"
              dataKey="net_pnl"
              fill="#818cf8"
              opacity={0.5}
              isAnimationActive={false}
              radius={[2, 2, 0, 0]}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

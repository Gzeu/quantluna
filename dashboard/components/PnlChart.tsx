/**
 * PnlChart.tsx — S37
 * Live PnL equity curve via SSE /risk/stream
 * Recharts AreaChart cu gradient + tooltips
 */
'use client';
import React from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { usePnlStream } from '../hooks/usePnlStream';

interface Props {
  maxPoints?: number;
}

export function PnlChart({ maxPoints = 200 }: Props) {
  const { data, connected, error } = usePnlStream(maxPoints);

  return (
    <div className="bg-gray-900 rounded-2xl p-5 w-full">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg">Live PnL — Equity Curve</h2>
        <span className={`text-xs px-2 py-1 rounded-full font-medium ${
          connected ? 'bg-green-800 text-green-300' : 'bg-red-800 text-red-300'
        }`}>
          {connected ? '● LIVE' : error ? '✕ ERROR' : '○ Connecting…'}
        </span>
      </div>

      {data.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-gray-500 text-sm">
          Waiting for stream data…
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#22d3ee" stopOpacity={0.35} />
                <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="ts"
              tick={{ fill: '#9ca3af', fontSize: 11 }}
              tickFormatter={v => new Date(v).toLocaleTimeString()}
              minTickGap={40}
            />
            <YAxis
              tick={{ fill: '#9ca3af', fontSize: 11 }}
              tickFormatter={v => `$${v.toLocaleString()}`}
              width={72}
            />
            <Tooltip
              contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8 }}
              labelStyle={{ color: '#9ca3af', fontSize: 11 }}
              formatter={(v: number) => [`$${v.toLocaleString('en-US', { minimumFractionDigits: 2 })}`, 'Equity']}
              labelFormatter={v => new Date(v).toLocaleTimeString()}
            />
            <ReferenceLine y={data[0]?.equity} stroke="#374151" strokeDasharray="4 2" />
            <Area
              type="monotone"
              dataKey="equity"
              stroke="#22d3ee"
              strokeWidth={2}
              fill="url(#pnlGrad)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

/**
 * PnlChart.tsx — S38 interactivity improvements
 * Migrat la design system: ql-card, ql-btn-ghost, CSS vars.
 * Skeleton shimmer in waiting state (in loc de text gri).
 * Sursa: store -> fallback SSE.
 * 
 * S38 improvements:
 * - Brush zoom (zoom selectiv pe perioadă)
 * - Pan (drag să deplasezi chart-ul)
 * - Tooltip îmbunătățit cu mai multe informații
 * - Data series toggle (equity, PnL, etc.)
 */
'use client';
import React, { useState, useMemo } from 'react';
import {
  ComposedChart, Area, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Legend, Brush,
} from 'recharts';
import { useQuantLunaStore } from '../store/quantlunaStore';
import { usePnlStream }      from '../hooks/usePnlStream';
import type { PnlPoint }     from '../types/dashboard';
import { Card }              from './ui/Card';
import { Badge }             from './ui/Badge';

const ZOOM_OPTIONS = [50, 100, 200, 0] as const;
const ZOOM_LABELS  = ['50', '100', '200', 'ALL'];

function exportCsv(data: PnlPoint[]) {
  const rows = [
    'ts,equity,net_pnl',
    ...data.map(d =>
      `${new Date(d.ts).toISOString()},${d.equity},${d.net_pnl ?? ''}`
    ),
  ].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([rows], { type: 'text/csv' }));
  a.download = `pnl_${Date.now()}.csv`;
  a.click();
}

/* Skeleton pentru waiting state */
function ChartSkeleton({ connected }: { connected: boolean }) {
  return (
    <div className="h-[320px] flex flex-col gap-3 pt-2">
      {/* Y-axis mock */}
      <div className="flex gap-3 h-full">
        <div className="flex flex-col justify-between py-2" style={{ width: 56 }}>
          {[1,2,3,4,5].map(i => (
            <div key={i} className="skeleton h-3 rounded" style={{ width: '80%' }} />
          ))}
        </div>
        <div className="flex-1 flex flex-col gap-2">
          {/* Skeleton bars */}
          <div className="flex items-end gap-1 h-full pb-6">
            {Array.from({ length: 24 }).map((_, i) => (
              <div
                key={i}
                className="skeleton flex-1 rounded-sm"
                style={{ height: `${20 + Math.sin(i * 0.7) * 15 + 40}%` }}
              />
            ))}
          </div>
        </div>
      </div>
      {/* Status */}
      <p className="text-center text-[var(--text-muted)] text-xs">
        {connected ? 'Waiting for data…' : 'Connecting to stream…'}
      </p>
    </div>
  );
}

interface Props { maxPoints?: number; }

export function PnlChart({ maxPoints = 200 }: Props) {
  const storePnl = useQuantLunaStore(s => s.pnl);
  const storeHistory: PnlPoint[] = useMemo(() => {
    if (!storePnl?.equityHistory?.length) return [];
    return (storePnl.equityHistory as { ts: number; equity: number; net_pnl?: number }[])
      .slice(-maxPoints);
  }, [storePnl, maxPoints]);

  const { data: sseData, connected: sseConnected } = usePnlStream(
    storeHistory.length > 0 ? 0 : maxPoints
  );

  const rawData: PnlPoint[]  = storeHistory.length > 0 ? storeHistory : sseData;
  const connected            = storeHistory.length > 0 ? true : sseConnected;
  const source               = storeHistory.length > 0 ? 'WS' : 'SSE';

  const [zoom, setZoom] = useState<number>(0);
  const [brushData, setBrushData] = useState<PnlPoint[]>(rawData);
  const [showPnl, setShowPnl] = useState<boolean>(true);
  
  const visible = useMemo(
    () => zoom > 0 ? rawData.slice(-zoom) : (brushData.length > 0 ? brushData : rawData),
    [rawData, zoom, brushData]
  );
  const firstEquity = visible[0]?.equity;

  return (
    <Card>
      <Card.Header>
        <div className="flex items-center gap-3">
          <Card.Title>Live PnL — Equity Curve</Card.Title>
          <Badge
            variant={connected ? 'green' : 'red'}
            dot pulse={connected}
          >
            {connected ? `LIVE · ${source}` : 'Connecting…'}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          {/* Zoom group */}
          <div className="flex rounded-lg overflow-hidden border border-[var(--border)]">
            {ZOOM_OPTIONS.map((z, i) => (
              <button
                key={z}
                onClick={() => {
                  setZoom(z);
                  if (z === 0) setBrushData([]);
                }}
                className={`ql-btn rounded-none border-0 px-2.5 py-1 text-xs transition-colors ${
                  zoom === z
                    ? 'bg-[var(--cyan-dim)] text-cyan-200'
                    : 'bg-[var(--bg-elevated)] text-[var(--text-muted)] hover:bg-[var(--bg-card-hover)]'
                }`}
              >
                {ZOOM_LABELS[i]}
              </button>
            ))}
          </div>
          {/* PnL toggle */}
          <button
            onClick={() => setShowPnl(!showPnl)}
            className={`ql-btn ql-btn-ghost px-2 py-1 text-xs ${
              showPnl ? 'bg-[var(--purple-dim)] text-purple-200' : ''
            }`}
          >
            PnL
          </button>
          <button
            onClick={() => exportCsv(visible)}
            disabled={visible.length === 0}
            className="ql-btn ql-btn-ghost"
          >
            ↓ CSV
          </button>
        </div>
      </Card.Header>

      {visible.length === 0 ? (
        <ChartSkeleton connected={connected} />
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart
            data={visible}
            margin={{ top: 4, right: 16, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#22d3ee" stopOpacity={0.28} />
                <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="ts"
              tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
              tickFormatter={v => new Date(v as number).toLocaleTimeString()}
              minTickGap={50}
            />
            <YAxis
              yAxisId="equity"
              tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
              tickFormatter={v => `$${(v as number).toLocaleString()}`}
              width={78}
            />
            <YAxis
              yAxisId="pnl"
              orientation="right"
              tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
              tickFormatter={v => `$${(v as number).toFixed(0)}`}
              width={64}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                fontSize: 12,
              }}
              labelFormatter={(v: number) => new Date(v).toLocaleString()}
              formatter={(v: number, name: string, props: any) => {
                if (name === 'equity') {
                  const point = props.payload;
                  const pnl = point.net_pnl ?? 0;
                  return [
                    `$${v.toLocaleString('en-US', { minimumFractionDigits: 2 })}`,
                    'Equity',
                    `PnL: $${pnl.toFixed(2)}`,
                  ];
                }
                return [
                  `$${v.toLocaleString('en-US', { minimumFractionDigits: 2 })}`,
                  'Net PnL',
                ];
              }}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, color: 'var(--text-muted)', paddingTop: 8 }}
              formatter={v => v === 'equity' ? 'Equity' : 'Net PnL'}
            />
            {firstEquity !== undefined && (
              <ReferenceLine
                yAxisId="equity" y={firstEquity}
                stroke="var(--border)" strokeDasharray="4 2"
              />
            )}
            <Area
              yAxisId="equity" type="monotone" dataKey="equity"
              stroke="var(--cyan)" strokeWidth={2} fill="url(#pnlGrad)"
              dot={false} isAnimationActive={false}
            />
            {showPnl && (
              <Bar
                yAxisId="pnl" dataKey="net_pnl"
                fill="var(--purple)" opacity={0.45}
                isAnimationActive={false} radius={[2,2,0,0]}
              />
            )}
            <Brush
              dataKey="ts"
              height={30}
              stroke="var(--border)"
              fill="var(--bg-elevated)"
              travellerWidth={10}
              onChange={(e: any) => {
                if (e && e.startIndex !== undefined && e.endIndex !== undefined) {
                  const sliced = rawData.slice(e.startIndex, e.endIndex + 1);
                  setBrushData(sliced);
                }
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

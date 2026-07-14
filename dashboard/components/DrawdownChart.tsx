/**
 * DrawdownChart.tsx — S38 drawdown chart
 * Componentă pentru vizualizarea istoricului drawdown cu AreaChart.
 * Sursa: /risk/drawdown_history API endpoint.
 */
'use client';
import React, { useState, useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, Brush,
} from 'recharts';
import { Card } from './ui/Card';
import { Badge } from './ui/Badge';
import { Spinner } from './ui/Spinner';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface DrawdownPoint {
  ts: number;
  drawdown: number;
  equity: number;
  peak: number;
}

interface DrawdownResponse {
  history: DrawdownPoint[];
}

const ZOOM_OPTIONS = [50, 100, 200, 0] as const;
const ZOOM_LABELS  = ['50', '100', '200', 'ALL'];

interface Props { maxPoints?: number; }

export function DrawdownChart({ maxPoints = 200 }: Props) {
  const [data, setData] = useState<DrawdownPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number>(0);
  const [brushData, setBrushData] = useState<DrawdownPoint[]>([]);

  const loadData = React.useCallback(async () => {
    try {
      const r = await fetch(`${API}/risk/drawdown_history?limit=${maxPoints}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const json: DrawdownResponse = await r.json();
      setData(json.history || []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fetch error');
    } finally {
      setLoading(false);
    }
  }, [maxPoints]);

  React.useEffect(() => {
    loadData();
    const id = setInterval(loadData, 5000); // refresh la 5s
    return () => clearInterval(id);
  }, [loadData]);

  const visible = useMemo(
    () => zoom > 0 ? data.slice(-zoom) : (brushData.length > 0 ? brushData : data),
    [data, zoom, brushData]
  );

  const maxDrawdown = useMemo(
    () => Math.max(...visible.map(d => d.drawdown), 0),
    [visible]
  );

  const currentDrawdown = visible.length > 0 ? visible[visible.length - 1].drawdown : 0;

  /* Skeleton pentru waiting state */
  function ChartSkeleton() {
    return (
      <div className="h-[320px] flex flex-col gap-3 pt-2">
        <div className="flex gap-3 h-full">
          <div className="flex flex-col justify-between py-2" style={{ width: 56 }}>
            {[1,2,3,4,5].map(i => (
              <div key={i} className="skeleton h-3 rounded" style={{ width: '80%' }} />
            ))}
          </div>
          <div className="flex-1 flex flex-col gap-2">
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
        <p className="text-center text-[var(--text-muted)] text-xs">
          Loading drawdown history…
        </p>
      </div>
    );
  }

  return (
    <Card>
      <Card.Header>
        <div className="flex items-center gap-3">
          <Card.Title>Drawdown History</Card.Title>
          <Badge
            variant={currentDrawdown > 0.05 ? 'red' : currentDrawdown > 0.02 ? 'yellow' : 'green'}
            dot
          >
            {currentDrawdown > 0 ? `${(currentDrawdown * 100).toFixed(2)}%` : '0%'}
          </Badge>
          {loading && <Spinner size="sm" />}
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
                    ? 'bg-[var(--purple-dim)] text-purple-200'
                    : 'bg-[var(--bg-elevated)] text-[var(--text-muted)] hover:bg-[var(--bg-card-hover)]'
                }`}
              >
                {ZOOM_LABELS[i]}
              </button>
            ))}
          </div>
        </div>
      </Card.Header>

      {error && <p className="text-red-400 text-sm mb-2">{error}</p>}

      {loading && visible.length === 0 ? (
        <ChartSkeleton />
      ) : visible.length === 0 ? (
        <p className="text-[var(--text-muted)] text-sm py-6 text-center">
          No drawdown data yet.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart
            data={visible}
            margin={{ top: 4, right: 16, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#ef4444" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#ef4444" stopOpacity={0.02} />
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
              tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
              tickFormatter={v => `${(v as number * 100).toFixed(1)}%`}
              width={60}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                fontSize: 12,
              }}
              labelFormatter={(v: number) => new Date(v).toLocaleTimeString()}
              formatter={(v: number, name: string) => [
                name === 'drawdown' ? `${(v * 100).toFixed(2)}%` : `$${v.toLocaleString()}`,
                name === 'drawdown' ? 'Drawdown' : name === 'equity' ? 'Equity' : 'Peak',
              ]}
            />
            {maxDrawdown > 0 && (
              <ReferenceLine
                y={maxDrawdown}
                stroke="#ef4444"
                strokeDasharray="4 2"
                label={{ value: `Max DD ${(maxDrawdown * 100).toFixed(2)}%`, fill: '#ef4444', fontSize: 10 }}
              />
            )}
            <Area
              type="monotone"
              dataKey="drawdown"
              stroke="#ef4444"
              strokeWidth={2}
              fill="url(#ddGrad)"
              dot={false}
              isAnimationActive={false}
            />
            <Brush
              dataKey="ts"
              height={30}
              stroke="var(--border)"
              fill="var(--bg-elevated)"
              travellerWidth={10}
              onChange={(e: any) => {
                if (e && e.startIndex !== undefined && e.endIndex !== undefined) {
                  const sliced = data.slice(e.startIndex, e.endIndex + 1);
                  setBrushData(sliced);
                }
              }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

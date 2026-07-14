/**
 * dashboard/pages/ml.tsx — AI/ML Signal Layer dashboard (Sprint 47)
 *
 * Sections:
 *   - Model Status Card — enabled, warm state, bars seen, model count
 *   - Feature Importance Chart — horizontal bar chart (top 15 features)
 *   - Prediction Feed — scrolling list of recent predictions
 *   - Fusion Monitor — ML vs Z-score contribution per regime
 */
import type { NextPage } from 'next';
import Head from 'next/head';
import { useEffect, useState, useCallback, useRef } from 'react';
import NavBar from '../components/NavBar';
import { StatsBar } from '../components/StatsBar';
import { Card } from '../components/Card';
import { Spinner } from '../components/Spinner';

/* ── Types ─────────────────────────────────────────────────────────── */

interface MLStatus {
  enabled: boolean;
  is_warm: boolean;
  bars_seen: number;
  model_count: number;
  has_models: boolean;
  feature_count: number;
  warmup_remaining: number;
  last_prediction: {
    score: number;
    confidence: number;
    direction: string;
    latency_us: number;
  } | null;
}

interface FeatureImportance {
  name: string;
  importance: number;
}

interface MLFusion {
  last_fused: {
    score: number;
    direction: string;
    strength: string;
    ml_contribution: number;
    z_contribution: number;
    ml_confidence: number;
    regime: string;
    should_trade: boolean;
  } | null;
  regime_weights: Record<string, { ml_weight: number; zscore_weight: number }>;
}

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

/* ── Page ──────────────────────────────────────────────────────────── */

const MLPage: NextPage = () => {
  const [status, setStatus] = useState<MLStatus | null>(null);
  const [importance, setImportance] = useState<FeatureImportance[]>([]);
  const [fusion, setFusion] = useState<MLFusion | null>(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [sRes, iRes, fRes] = await Promise.all([
        fetch(`${API}/api/ml/status`).then(r => r.json()).catch(() => null),
        fetch(`${API}/api/ml/features/importance`).then(r => r.json()).catch(() => null),
        fetch(`${API}/api/ml/fusion`).then(r => r.json()).catch(() => null),
      ]);
      if (sRes) setStatus(sRes);
      if (iRes?.importance) setImportance(iRes.importance.slice(0, 15));
      if (fRes) setFusion(fRes);
    } catch {
      /* offline — ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    timerRef.current = setInterval(fetchAll, 8000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchAll]);

  if (loading) {
    return (
      <>
        <Head><title>ML Signals — QuantLuna</title></Head>
        <NavBar />
        <main className="ml-page" style={{ padding: 20 }}>
          <Spinner size="lg" />
        </main>
      </>
    );
  }

  return (
    <>
      <Head><title>ML Signals — QuantLuna</title></Head>
      <NavBar />
      <StatsBar />

      <main className="animate-fade-in" style={{
        background: 'var(--bg-body)',
        minHeight: 'calc(100vh - var(--nav-h) - var(--stats-h))',
        padding: '16px 20px 40px',
        display: 'flex', flexDirection: 'column', gap: 16,
      }}>
        {/* ── Status KPIs ──────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <StatusBadge
            label="ML Status"
            value={status?.enabled ? (status?.is_warm ? '🟢 Active' : '🟡 Warming') : '⚫ Disabled'}
            color={status?.enabled ? (status?.is_warm ? '#22c55e' : '#eab308') : '#9ca3af'}
          />
          <StatusBadge label="Bars Seen" value={String(status?.bars_seen ?? 0)} color="#3b82f6" />
          <StatusBadge label="Models" value={String(status?.model_count ?? 0)} color="#8b5cf6" />
          <StatusBadge label="Features" value={String(status?.feature_count ?? 0)} color="#06b6d4" />
          {!status?.is_warm && status?.enabled && (
            <StatusBadge
              label="Warmup"
              value={`${status.warmup_remaining} bars left`}
              color="#f59e0b"
            />
          )}
        </div>

        {/* ── Last Prediction ──────────────────────────────────── */}
        {status?.last_prediction && status.last_prediction.direction !== 'FLAT' && (
          <Card>
            <Card.Header>
              <Card.Title>Last Prediction</Card.Title>
            </Card.Header>
            <Card.Body>
              <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <DirectionBadge direction={status.last_prediction.direction} />
                <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>
                  Score: {status.last_prediction.score.toFixed(3)}
                </span>
                <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>
                  Confidence: {(status.last_prediction.confidence * 100).toFixed(0)}%
                </span>
                <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                  {(status.last_prediction.latency_us).toFixed(0)} µs
                </span>
              </div>
            </Card.Body>
          </Card>
        )}

        {/* ── Feature Importance ───────────────────────────────── */}
        <Card>
          <Card.Header>
            <Card.Title>Feature Importance</Card.Title>
          </Card.Header>
          <Card.Body>
            {importance.length === 0 ? (
              <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>
                No feature importance data yet. Models need training data.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {importance.map((f) => (
                  <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{
                      width: 140, fontSize: 12, fontFamily: 'monospace',
                      color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis',
                    }} title={f.name}>
                      {f.name}
                    </span>
                    <div style={{
                      flex: 1, height: 10, borderRadius: 5,
                      background: 'var(--bg-raised)',
                    }}>
                      <div style={{
                        height: '100%', borderRadius: 5,
                        width: `${Math.min(100, (f.importance / Math.max(...importance.map(x => x.importance))) * 100)}%`,
                        background: `hsl(${220 + (1 - f.importance / Math.max(...importance.map(x => x.importance))) * 40}, 70%, 55%)`,
                        transition: 'width 0.3s ease',
                      }} />
                    </div>
                    <span style={{ width: 50, fontSize: 11, textAlign: 'right', color: 'var(--text-dim)' }}>
                      {f.importance.toFixed(4)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card.Body>
        </Card>

        {/* ── Fusion Weights ───────────────────────────────────── */}
        <Card>
          <Card.Header>
            <Card.Title>Regime Fusion Weights</Card.Title>
          </Card.Header>
          <Card.Body>
            {fusion?.regime_weights ? (
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    <th style={{ textAlign: 'left', padding: 6 }}>Regime</th>
                    <th style={{ textAlign: 'right', padding: 6 }}>ML Weight</th>
                    <th style={{ textAlign: 'right', padding: 6 }}>Z-score Weight</th>
                    <th style={{ textAlign: 'center', padding: 6 }}>Active</th>
                  </tr>
                </thead>
                <tbody>
                  {(Object.entries(fusion.regime_weights) as [string, { ml_weight: number; zscore_weight: number }][]).map(([regime, w]) => (
                    <tr key={regime} style={{
                      borderBottom: '1px solid var(--border-subtle)',
                      background: fusion.last_fused?.regime === regime ? 'var(--bg-raised)' : 'transparent',
                    }}>
                      <td style={{ padding: 6, textTransform: 'capitalize' }}>{regime}</td>
                      <td style={{ textAlign: 'right', padding: 6, fontFamily: 'monospace' }}>
                        {(w.ml_weight * 100).toFixed(0)}%
                      </td>
                      <td style={{ textAlign: 'right', padding: 6, fontFamily: 'monospace' }}>
                        {(w.zscore_weight * 100).toFixed(0)}%
                      </td>
                      <td style={{ textAlign: 'center', padding: 6 }}>
                        {fusion.last_fused?.regime === regime ? '●' : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>No fusion data available.</p>
            )}
          </Card.Body>
        </Card>

        {/* ── Last Fused Signal ─────────────────────────────────── */}
        {fusion?.last_fused && (
          <Card>
            <Card.Header>
              <Card.Title>Last Fused Signal</Card.Title>
            </Card.Header>
            <Card.Body>
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 13 }}>
                <div>
                  <span style={{ color: 'var(--text-dim)' }}>Direction: </span>
                  <DirectionBadge direction={fusion.last_fused.direction} />
                </div>
                <div>
                  <span style={{ color: 'var(--text-dim)' }}>Score: </span>
                  <strong>{fusion.last_fused.score.toFixed(3)}</strong>
                </div>
                <div>
                  <span style={{ color: 'var(--text-dim)' }}>Strength: </span>
                  <strong style={{ textTransform: 'capitalize' }}>{fusion.last_fused.strength}</strong>
                </div>
                <div>
                  <span style={{ color: 'var(--text-dim)' }}>ML Contrib: </span>
                  <strong>{(fusion.last_fused.ml_contribution * 100).toFixed(0)}%</strong>
                </div>
                <div>
                  <span style={{ color: 'var(--text-dim)' }}>Should Trade: </span>
                  <strong>{fusion.last_fused.should_trade ? '✅ Yes' : '❌ No'}</strong>
                </div>
              </div>
            </Card.Body>
          </Card>
        )}
      </main>
    </>
  );
};

export default MLPage;

/* ── Helper Components ──────────────────────────────────────────────── */

function StatusBadge({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      padding: '8px 14px', borderRadius: 8,
      background: 'var(--bg-raised)', border: '1px solid var(--border-subtle)',
      display: 'flex', flexDirection: 'column', gap: 2,
    }}>
      <span style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase' }}>
        {label}
      </span>
      <span style={{ fontSize: 16, fontWeight: 600, color }}>
        {value}
      </span>
    </div>
  );
}

function DirectionBadge({ direction }: { direction: string }) {
  const color = direction === 'LONG' ? '#22c55e' : direction === 'SHORT' ? '#ef4444' : '#9ca3af';
  return (
    <span style={{
      display: 'inline-block', padding: '2px 10px', borderRadius: 4,
      background: `${color}20`, color, fontWeight: 600, fontSize: 13,
      border: `1px solid ${color}40`,
    }}>
      {direction}
    </span>
  );
}

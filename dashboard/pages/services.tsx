/**
 * dashboard/pages/services.tsx  -  QuantLuna Services Control Panel v1.0
 *
 * Sprint S41 (2026-07-12):
 *   Panou de control complet pentru toate serviciile bot:
 *     - Card per serviciu cu status live (WebSocket 1s)
 *     - Butoane START / STOP / RESTART per serviciu
 *     - Uptime, last error, restart count
 *     - Header global: cate servicii running / total
 *     - Confirmare modala pentru STOP (sa nu se opreasca accidental)
 *     - Indicatoare vizuale: verde=running, rosu=stopped, galben=starting/stopping
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const WS_URL = (API.replace('http', 'ws')) + '/api/services/ws';

// ---- Types ----
interface Service {
  name: string;
  display_name: string;
  description: string;
  status: 'running' | 'stopped' | 'error' | 'starting' | 'stopping';
  enabled: boolean;
  can_toggle: boolean;
  uptime_s: number | null;
  uptime_human: string;
  last_error: string | null;
  restart_count: number;
}

// ---- Status colors ----
const STATUS_COLOR: Record<string, string> = {
  running:  '#4ade80',
  stopped:  '#6b7280',
  error:    '#f87171',
  starting: '#fbbf24',
  stopping: '#fbbf24',
};

const STATUS_ICON: Record<string, string> = {
  running:  '●',
  stopped:  '○',
  error:    '✕',
  starting: '◌',
  stopping: '◌',
};

// ---- API helpers ----
async function callService(name: string, action: 'start' | 'stop' | 'restart') {
  const res = await fetch(`${API}/api/services/${name}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Eroare necunoscuta' }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---- Confirm modal ----
function ConfirmModal({
  message, onConfirm, onCancel,
}: {
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        background: '#1a1a2e', borderRadius: 12, padding: '28px 32px',
        border: '1px solid #f87171', maxWidth: 400, width: '90%',
      }}>
        <div style={{ fontSize: 18, marginBottom: 16 }}>⚠️ Confirmare</div>
        <div style={{ color: '#ccc', marginBottom: 24 }}>{message}</div>
        <div style={{ display: 'flex', gap: 12 }}>
          <button
            onClick={onConfirm}
            style={{
              flex: 1, padding: '10px 0', borderRadius: 8,
              background: '#f87171', border: 'none', color: '#000',
              fontWeight: 700, cursor: 'pointer', fontSize: 14,
            }}
          >Da, opreste</button>
          <button
            onClick={onCancel}
            style={{
              flex: 1, padding: '10px 0', borderRadius: 8,
              background: '#2a2a4a', border: '1px solid #3a3a5a',
              color: '#ccc', cursor: 'pointer', fontSize: 14,
            }}
          >Anuleaza</button>
        </div>
      </div>
    </div>
  );
}

// ---- Service Card ----
function ServiceCard({
  svc,
  onAction,
  loading,
}: {
  svc: Service;
  onAction: (name: string, action: 'start' | 'stop' | 'restart') => void;
  loading: string | null;
}) {
  const isLoading = loading === svc.name;
  const color = STATUS_COLOR[svc.status] || '#888';
  const icon = STATUS_ICON[svc.status] || '?';
  const isRunning = svc.status === 'running';
  const isTransient = svc.status === 'starting' || svc.status === 'stopping';

  return (
    <div style={{
      background: '#1a1a2e',
      borderRadius: 12,
      padding: '18px 20px',
      border: `1px solid ${isRunning ? '#2a3a2a' : svc.status === 'error' ? '#3a1a1a' : '#2a2a4a'}`,
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
      transition: 'border-color 0.3s',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            fontSize: 18,
            color,
            animation: isTransient ? 'pulse 1s infinite' : undefined,
          }}>{icon}</span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>{svc.display_name}</div>
            <div style={{ color: '#666', fontSize: 11, marginTop: 1 }}>{svc.name}</div>
          </div>
        </div>
        <span style={{
          background: isRunning ? '#0a2a0a' : svc.status === 'error' ? '#2a0a0a' : '#1a1a2a',
          color,
          borderRadius: 20,
          padding: '3px 10px',
          fontSize: 11,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: 1,
        }}>{svc.status}</span>
      </div>

      {/* Description */}
      <div style={{ color: '#888', fontSize: 12 }}>{svc.description}</div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 16, fontSize: 12 }}>
        <span style={{ color: '#555' }}>
          ⏱ <span style={{ color: '#aaa' }}>{svc.uptime_human}</span>
        </span>
        {svc.restart_count > 0 && (
          <span style={{ color: '#555' }}>
            🔄 <span style={{ color: '#aaa' }}>{svc.restart_count} restart</span>
          </span>
        )}
      </div>

      {/* Error */}
      {svc.last_error && (
        <div style={{
          background: '#2a0a0a', borderRadius: 6,
          padding: '6px 10px', fontSize: 11, color: '#f87171',
          fontFamily: 'monospace', wordBreak: 'break-all',
        }}>
          ⚠ {svc.last_error}
        </div>
      )}

      {/* Actions */}
      {svc.can_toggle && (
        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          {!isRunning ? (
            <button
              disabled={isLoading || isTransient}
              onClick={() => onAction(svc.name, 'start')}
              style={{
                flex: 1, padding: '8px 0', borderRadius: 8,
                background: isLoading ? '#1a3a1a' : '#14532d',
                border: '1px solid #22c55e',
                color: '#4ade80', fontWeight: 700,
                cursor: isLoading || isTransient ? 'not-allowed' : 'pointer',
                fontSize: 13, opacity: isLoading ? 0.7 : 1,
              }}
            >
              {isLoading ? '...' : '▶ START'}
            </button>
          ) : (
            <>
              <button
                disabled={isLoading}
                onClick={() => onAction(svc.name, 'stop')}
                style={{
                  flex: 1, padding: '8px 0', borderRadius: 8,
                  background: '#2a0a0a', border: '1px solid #f87171',
                  color: '#f87171', fontWeight: 700,
                  cursor: isLoading ? 'not-allowed' : 'pointer',
                  fontSize: 13, opacity: isLoading ? 0.7 : 1,
                }}
              >
                {isLoading ? '...' : '■ STOP'}
              </button>
              <button
                disabled={isLoading}
                onClick={() => onAction(svc.name, 'restart')}
                style={{
                  padding: '8px 14px', borderRadius: 8,
                  background: '#1a1a3a', border: '1px solid #3a3a6a',
                  color: '#aaa', fontWeight: 600,
                  cursor: isLoading ? 'not-allowed' : 'pointer',
                  fontSize: 13, opacity: isLoading ? 0.7 : 1,
                }}
              >
                🔄
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Main Page ----
export default function ServicesPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'err' } | null>(null);
  const [confirmStop, setConfirmStop] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // WebSocket live updates
  useEffect(() => {
    function connect() {
      try {
        const ws = new WebSocket(WS_URL);
        wsRef.current = ws;

        ws.onopen = () => setWsConnected(true);
        ws.onclose = () => {
          setWsConnected(false);
          // Reconecteaza dupa 3s
          setTimeout(connect, 3000);
        };
        ws.onerror = () => ws.close();
        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data);
            if (data.services) setServices(data.services);
          } catch {}
        };
      } catch {
        setTimeout(connect, 3000);
      }
    }
    connect();
    return () => wsRef.current?.close();
  }, []);

  // Fallback polling daca WS nu e disponibil
  useEffect(() => {
    if (wsConnected) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/services/list`);
        const data = await res.json();
        if (data.services) setServices(data.services);
        setError(null);
      } catch (e: any) {
        setError(e.message);
      }
    }, 2000);
    return () => clearInterval(id);
  }, [wsConnected]);

  const showToast = useCallback((msg: string, type: 'ok' | 'err') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3500);
  }, []);

  const handleAction = useCallback(async (
    name: string,
    action: 'start' | 'stop' | 'restart'
  ) => {
    if (action === 'stop') {
      setConfirmStop(name);
      return;
    }
    setLoading(name);
    try {
      await callService(name, action);
      showToast(`${name} → ${action} OK`, 'ok');
    } catch (e: any) {
      showToast(`Eroare ${name}: ${e.message}`, 'err');
    } finally {
      setLoading(null);
    }
  }, [showToast]);

  const handleConfirmStop = useCallback(async () => {
    if (!confirmStop) return;
    const name = confirmStop;
    setConfirmStop(null);
    setLoading(name);
    try {
      await callService(name, 'stop');
      showToast(`${name} oprit`, 'ok');
    } catch (e: any) {
      showToast(`Eroare stop ${name}: ${e.message}`, 'err');
    } finally {
      setLoading(null);
    }
  }, [confirmStop, showToast]);

  const running = services.filter(s => s.status === 'running').length;
  const total = services.length;

  // Grupeaza serviciile pe categorii
  const groups: Record<string, Service[]> = {
    'Trading Core': services.filter(s =>
      ['futures_runner', 'spot_runner', 'margin_guard'].includes(s.name)
    ),
    'Capital & Risk': services.filter(s =>
      ['capital_allocator', 'withdrawal_guard', 'internal_transfer'].includes(s.name)
    ),
    'Optimizer': services.filter(s =>
      s.name.includes('reoptimizer') || s.name.includes('optimizer')
    ),
    'Hedge Managers': services.filter(s => s.name.startsWith('hedge_')),
    'Altele': services.filter(s =>
      !['futures_runner','spot_runner','margin_guard',
        'capital_allocator','withdrawal_guard','internal_transfer'].includes(s.name)
      && !s.name.includes('reoptimizer') && !s.name.includes('optimizer')
      && !s.name.startsWith('hedge_')
    ),
  };

  return (
    <div style={{
      background: '#0f0f1a', minHeight: '100vh',
      padding: '32px 40px', color: '#e0e0ff',
      fontFamily: 'Inter, system-ui, sans-serif',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 32 }}>
        <span style={{ fontSize: 28, fontWeight: 800 }}>⚙️ Servicii</span>
        <div style={{
          background: running > 0 ? '#0a2a0a' : '#1a1a2a',
          border: `1px solid ${running > 0 ? '#22c55e' : '#3a3a5a'}`,
          borderRadius: 20, padding: '4px 14px', fontSize: 13,
        }}>
          <span style={{ color: running > 0 ? '#4ade80' : '#666' }}>
            {running}/{total} running
          </span>
        </div>
        <div style={{
          marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 12, color: wsConnected ? '#4ade80' : '#f87171',
        }}>
          <span style={{ fontSize: 8 }}>●</span>
          {wsConnected ? 'WebSocket live' : 'Polling 2s'}
        </div>
      </div>

      {error && (
        <div style={{
          background: '#2a0000', border: '1px solid #f87171',
          borderRadius: 8, padding: '10px 16px',
          marginBottom: 20, color: '#f87171', fontSize: 13,
        }}>⚠️ API offline: {error}</div>
      )}

      {total === 0 && (
        <div style={{
          textAlign: 'center', color: '#555', padding: '60px 0', fontSize: 16,
        }}>
          Se incarca serviciile...<br/>
          <span style={{ fontSize: 13, marginTop: 8, display: 'block' }}>
            Asigura-te ca FastAPI ruleaza pe {API}
          </span>
        </div>
      )}

      {/* Grupe servicii */}
      {Object.entries(groups).map(([groupName, svcs]) => {
        if (svcs.length === 0) return null;
        return (
          <div key={groupName} style={{ marginBottom: 32 }}>
            <div style={{
              fontSize: 13, fontWeight: 600, color: '#888',
              textTransform: 'uppercase', letterSpacing: 1,
              marginBottom: 12, paddingBottom: 6,
              borderBottom: '1px solid #1a1a2a',
            }}>{groupName}</div>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
              gap: 16,
            }}>
              {svcs.map(svc => (
                <ServiceCard
                  key={svc.name}
                  svc={svc}
                  onAction={handleAction}
                  loading={loading}
                />
              ))}
            </div>
          </div>
        );
      })}

      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 32, right: 32,
          background: toast.type === 'ok' ? '#0a2a0a' : '#2a0a0a',
          border: `1px solid ${toast.type === 'ok' ? '#22c55e' : '#f87171'}`,
          borderRadius: 10, padding: '12px 20px',
          color: toast.type === 'ok' ? '#4ade80' : '#f87171',
          fontSize: 14, fontWeight: 600,
          boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
          zIndex: 999,
          animation: 'fadeIn 0.2s ease',
        }}>
          {toast.type === 'ok' ? '✓' : '✕'} {toast.msg}
        </div>
      )}

      {/* Confirm modal */}
      {confirmStop && (
        <ConfirmModal
          message={`Esti sigur ca vrei sa opresti serviciul "${confirmStop}"?`}
          onConfirm={handleConfirmStop}
          onCancel={() => setConfirmStop(null)}
        />
      )}

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
      `}</style>
    </div>
  );
}

/**
 * Toast.tsx — S37 polish
 * Sistem notificări ușor: success / warn / error / info
 * useToast() hook — import în orice componentă
 * <ToastContainer /> — adaugă o dată în _app.tsx
 */
import React, { createContext, useCallback, useContext, useState } from 'react';

export type ToastLevel = 'success' | 'warn' | 'error' | 'info';

export interface ToastItem {
  id:      number;
  level:   ToastLevel;
  message: string;
}

interface ToastCtx {
  toast: (msg: string, level?: ToastLevel, durationMs?: number) => void;
}

const Ctx = createContext<ToastCtx>({ toast: () => {} });

let _id = 0;

const ICONS: Record<ToastLevel, string> = {
  success: '✔',
  warn:    '⚠️',
  error:   '✕',
  info:    'ℹ️',
};

const COLORS: Record<ToastLevel, string> = {
  success: 'bg-green-900 border-green-700 text-green-200',
  warn:    'bg-yellow-900 border-yellow-700 text-yellow-200',
  error:   'bg-red-900 border-red-700 text-red-200',
  info:    'bg-gray-800 border-gray-700 text-gray-200',
};

export function ToastContainer() {
  const [items, setItems] = useState<ToastItem[]>([]);

  const toast = useCallback(
    (message: string, level: ToastLevel = 'info', durationMs = 3500) => {
      const id = ++_id;
      setItems(prev => [...prev, { id, level, message }]);
      setTimeout(() => setItems(prev => prev.filter(t => t.id !== id)), durationMs);
    },
    []
  );

  return (
    <Ctx.Provider value={{ toast }}>
      {/* Portal-like: fixed bottom-right */}
      <div className="fixed bottom-5 right-5 z-[999] flex flex-col gap-2 pointer-events-none">
        {items.map(t => (
          <div
            key={t.id}
            className={`
              flex items-start gap-2 px-4 py-3 rounded-xl border text-sm
              shadow-lg pointer-events-auto
              animate-in slide-in-from-right-4 fade-in duration-200
              ${COLORS[t.level]}
            `}
            style={{ minWidth: 220, maxWidth: 340 }}
          >
            <span className="shrink-0">{ICONS[t.level]}</span>
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastCtx['toast'] {
  return useContext(Ctx).toast;
}

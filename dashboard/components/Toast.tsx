/**
 * Toast.tsx — S37 UI/UX
 * - Provider pattern: <ToastContainer>{children}</ToastContainer>
 * - Progress bar countdown per toast
 * - Max 5 simultane; click-to-dismiss
 * - aria-live pentru accesibilitate
 * - Variante: success / warn / error / info
 */
import React, {
  createContext, useCallback, useContext,
  useEffect, useRef, useState,
} from 'react';

export type ToastLevel = 'success' | 'warn' | 'error' | 'info';

interface ToastItem {
  id:        number;
  level:     ToastLevel;
  message:   string;
  duration:  number;
}

type ToastFn = (msg: string, level?: ToastLevel, duration?: number) => void;
const Ctx = createContext<ToastFn>(() => {});

let _id = 0;

const ICON: Record<ToastLevel, string> = {
  success: '✔', warn: '⚠️', error: '✕', info: 'ℹ️',
};
const STYLE: Record<ToastLevel, string> = {
  success: 'border-green-800/60  bg-green-950/90  text-green-200',
  warn:    'border-yellow-800/60 bg-yellow-950/90 text-yellow-200',
  error:   'border-red-800/60   bg-red-950/90    text-red-200',
  info:    'border-[var(--border)] bg-[var(--bg-card)]/90 text-gray-200',
};
const PROGRESS: Record<ToastLevel, string> = {
  success: 'bg-green-500',
  warn:    'bg-yellow-500',
  error:   'bg-red-500',
  info:    'bg-cyan-500',
};

function ToastRow({
  item, onDismiss,
}: { item: ToastItem; onDismiss: (id: number) => void }) {
  const [pct, setPct] = useState(100);
  const start = useRef(Date.now());

  useEffect(() => {
    const id = setInterval(() => {
      const elapsed = Date.now() - start.current;
      setPct(Math.max(0, 100 - (elapsed / item.duration) * 100));
    }, 50);
    return () => clearInterval(id);
  }, [item.duration]);

  return (
    <div
      role="alert"
      onClick={() => onDismiss(item.id)}
      className={`
        relative overflow-hidden cursor-pointer select-none
        flex items-start gap-2.5 px-4 py-3 rounded-xl border text-sm
        shadow-lg backdrop-blur-md animate-slide-up
        ${STYLE[item.level]}
      `}
      style={{ minWidth: 240, maxWidth: 360 }}
    >
      <span className="shrink-0 text-base mt-0.5">{ICON[item.level]}</span>
      <span className="flex-1 leading-snug">{item.message}</span>
      <div
        className={`absolute bottom-0 left-0 h-0.5 ${PROGRESS[item.level]}`}
        style={{ width: `${pct}%`, transition: 'width 50ms linear' }}
      />
    </div>
  );
}

export function ToastContainer({ children }: { children?: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const toast = useCallback<ToastFn>(
    (message, level = 'info', duration = 3500) => {
      const id = ++_id;
      setItems(prev => [...prev.slice(-4), { id, level, message, duration }]);
      setTimeout(() => setItems(p => p.filter(t => t.id !== id)), duration);
    },
    []
  );

  const dismiss = useCallback((id: number) => {
    setItems(p => p.filter(t => t.id !== id));
  }, []);

  return (
    <Ctx.Provider value={toast}>
      {children}
      <div
        className="fixed bottom-5 right-5 z-[500] flex flex-col gap-2 items-end pointer-events-none"
        aria-live="polite"
        aria-label="Notifications"
      >
        {items.map(item => (
          <div key={item.id} className="pointer-events-auto">
            <ToastRow item={item} onDismiss={dismiss} />
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastFn {
  return useContext(Ctx);
}

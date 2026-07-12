/**
 * ConfirmModal.tsx
 * Modal generic de confirmare. Portabil, accesibil (ESC, focus-trap).
 * Folosit de: ServicesPage (restart), WatchdogPanel (reset), etc.
 */
'use client';
import React, { useEffect, useRef } from 'react';

interface Props {
  open:      boolean;
  title:     string;
  message:   string;
  confirmLabel?: string;
  cancelLabel?:  string;
  danger?:   boolean;
  onConfirm: () => void;
  onCancel:  () => void;
}

export default function ConfirmModal({
  open, title, message,
  confirmLabel = 'Confirm',
  cancelLabel  = 'Cancel',
  danger = false,
  onConfirm, onCancel,
}: Props) {
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      onClick={onCancel}
    >
      <div
        className="rounded-xl p-6 shadow-2xl min-w-[320px] max-w-[480px]"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
        }}
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-bold text-[var(--text-primary)] mb-2">{title}</h2>
        <p className="text-sm text-[var(--text-muted)] mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-1.5 rounded-lg text-sm text-[var(--text-muted)]
                       border border-[var(--border)] hover:border-[var(--text-muted)]
                       transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            ref={ref}
            onClick={onConfirm}
            className={`px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors
              ${ danger
                ? 'bg-red-600 hover:bg-red-500 text-white'
                : 'bg-[var(--purple)] hover:opacity-90 text-white'
              }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

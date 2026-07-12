/**
 * ShortcutsModal.tsx
 * Modal keyboard shortcuts (deschis cu tasta '?').
 * Folosit de NavBar button si useKeyboardShortcuts.
 */
'use client';
import React, { useEffect } from 'react';

interface Props {
  open:    boolean;
  onClose: () => void;
}

const SHORTCUTS = [
  { key: '?',     desc: 'Toggle shortcuts modal' },
  { key: 'Esc',   desc: 'Close any modal' },
  { key: 'R',     desc: 'Refresh page data' },
  { key: 'D',     desc: 'Go to Dashboard' },
  { key: 'P',     desc: 'Go to Portfolio' },
  { key: 'S',     desc: 'Go to Services' },
  { key: 'O',     desc: 'Go to Optimizer' },
  { key: 'W',     desc: 'Go to Watchdog' },
  { key: 'T',     desc: 'Go to Strategy' },
];

export default function ShortcutsModal({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === '?') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)' }}
      onClick={onClose}
    >
      <div
        className="rounded-xl p-6 shadow-2xl w-[360px]"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-bold text-[var(--text-primary)] mb-4">
          ⌨ Keyboard Shortcuts
        </h2>
        <ul className="space-y-2">
          {SHORTCUTS.map(({ key, desc }) => (
            <li key={key} className="flex items-center justify-between">
              <span className="text-xs text-[var(--text-muted)]">{desc}</span>
              <kbd
                className="px-2 py-0.5 rounded text-[10px] mono font-bold
                           bg-[var(--bg-body)] border border-[var(--border)]
                           text-[var(--text-secondary)]"
              >{key}</kbd>
            </li>
          ))}
        </ul>
        <div className="flex justify-end mt-6">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-sm text-[var(--text-muted)]
                       border border-[var(--border)] hover:border-[var(--text-muted)]"
          >Close</button>
        </div>
      </div>
    </div>
  );
}

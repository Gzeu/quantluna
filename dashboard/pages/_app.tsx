/**
 * pages/_app.tsx — S37 UI/UX complet
 * - Import globals.css → Tailwind + design system in pages/
 * - useQuantLunaWS() init global (o singura data)
 * - Page transition fade 150ms
 * - Keyboard shortcuts: G+D/W/S/O/P + ? help modal + Esc
 * - ErrorBoundary + ToastContainer wrapping
 */
import type { AppProps } from 'next/app';
import { useRouter }    from 'next/router';
import { useEffect, useRef, useState, useCallback } from 'react';
import '../app/globals.css';
import { ErrorBoundary }            from '../components/ErrorBoundary';
import { ToastContainer, useToast } from '../components/Toast';
import { useQuantLunaWS }           from '../hooks/useQuantLunaWS';
import { Kbd }                      from '../components/ui/Kbd';

/* ── Keyboard shortcuts ─────────────────────────────────────────── */
const SHORTCUTS = [
  { keys: ['G', 'D'], desc: 'Go to Dashboard' },
  { keys: ['G', 'W'], desc: 'Go to Watchdog' },
  { keys: ['G', 'S'], desc: 'Go to Strategy' },
  { keys: ['G', 'O'], desc: 'Go to Optimizer' },
  { keys: ['G', 'P'], desc: 'Go to Portfolio' },
  { keys: ['?'],      desc: 'Toggle shortcuts help' },
  { keys: ['Esc'],    desc: 'Close modal' },
] as const;

const NAV: Record<string, string> = {
  d: '/', w: '/watchdog', s: '/strategy', o: '/optimizer', p: '/portfolio',
};

function HelpModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.72)', backdropFilter: 'blur(4px)' }}
      onClick={onClose}
    >
      <div
        className="ql-card p-6 w-full max-w-sm animate-slide-up"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-white font-bold">Keyboard Shortcuts</h2>
          <button
            onClick={onClose}
            className="ql-btn ql-btn-ghost text-lg leading-none px-2 py-0"
            aria-label="Close"
          >&times;</button>
        </div>
        <div className="space-y-3">
          {SHORTCUTS.map((s, i) => (
            <div key={i} className="flex items-center justify-between">
              <span className="text-[var(--text-secondary)] text-sm">{s.desc}</span>
              <div className="flex items-center gap-1">
                {s.keys.map(k => <Kbd key={k}>{k}</Kbd>)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Inner app (needs ToastContainer context) ───────────────────── */
function AppInner({ Component, pageProps }: AppProps) {
  useQuantLunaWS();
  const router = useRouter();
  const toast  = useToast();

  const gRef         = useRef(false);
  const [help, setHelp]             = useState(false);
  const [transitioning, setTrans]   = useState(false);

  /* Page transition */
  useEffect(() => {
    const start = () => setTrans(true);
    const done  = () => setTrans(false);
    router.events.on('routeChangeStart',    start);
    router.events.on('routeChangeComplete', done);
    router.events.on('routeChangeError',    done);
    return () => {
      router.events.off('routeChangeStart',    start);
      router.events.off('routeChangeComplete', done);
      router.events.off('routeChangeError',    done);
    };
  }, [router]);

  /* Keyboard shortcuts */
  const onKey = useCallback((e: KeyboardEvent) => {
    const tag = (e.target as HTMLElement).tagName;
    if (['INPUT','TEXTAREA','SELECT'].includes(tag)) return;

    if (e.key === '?')      { setHelp(h => !h); return; }
    if (e.key === 'Escape') { setHelp(false);   return; }

    if (e.key.toLowerCase() === 'g') { gRef.current = true; return; }
    if (gRef.current) {
      gRef.current = false;
      const dest = NAV[e.key.toLowerCase()];
      if (dest && router.pathname !== dest) {
        router.push(dest);
        const name = dest === '/' ? 'Dashboard' : dest.slice(1);
        toast(`→ ${name.charAt(0).toUpperCase() + name.slice(1)}`, 'info', 1400);
      }
    }
  }, [router, toast]);

  useEffect(() => {
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onKey]);

  return (
    <>
      {help && <HelpModal onClose={() => setHelp(false)} />}
      <div
        style={{
          opacity: transitioning ? 0 : 1,
          transition: 'opacity 150ms ease',
          pointerEvents: transitioning ? 'none' : undefined,
        }}
      >
        <Component {...pageProps} />
      </div>
    </>
  );
}

export default function App(props: AppProps) {
  return (
    <ErrorBoundary>
      <ToastContainer>
        <AppInner {...props} />
      </ToastContainer>
    </ErrorBoundary>
  );
}

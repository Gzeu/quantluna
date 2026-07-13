/**
 * pages/_app.tsx — S37 polish
 * - Import globals.css → Tailwind + design system
 * - useQuantLunaWS() init global (o singura data)
 * - Page transition fade 150ms
 * - Keyboard shortcuts: G+D/W/S/O/P/R/B + ? help modal + Esc
 * - ShortcutsModal (refolosit, nu duplicat)
 * - ErrorBoundary + ToastContainer
 */
import type { AppProps } from 'next/app';
import { useRouter }    from 'next/router';
import { useEffect, useRef, useState, useCallback } from 'react';
import '../styles/globals.css';
import { ErrorBoundary }            from '../components/ErrorBoundary';
import { ToastContainer, useToast } from '../components/Toast';
import { useQuantLunaWS }           from '../hooks/useQuantLunaWS';
import { ShortcutsModal }           from '../components/modals';

/* ── Nav map: G + key → route ────────────────────────────────── */
const NAV: Record<string, string> = {
  d: '/',
  p: '/portfolio',
  s: '/services',
  o: '/optimizer',
  w: '/watchdog',
  t: '/strategy',
  r: '/risk',
  b: '/backtest',
};

/* ── Inner app (needs ToastContainer context) ───────────────────── */
function AppInner({ Component, pageProps }: AppProps) {
  useQuantLunaWS();
  const router = useRouter();
  const toast  = useToast();

  const gRef                        = useRef(false);
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
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;

    if (e.key === '?')      { setHelp(h => !h); return; }
    if (e.key === 'Escape') { setHelp(false);   return; }

    if (e.key.toLowerCase() === 'g') { gRef.current = true; return; }
    if (gRef.current) {
      gRef.current = false;
      const dest = NAV[e.key.toLowerCase()];
      if (dest && router.pathname !== dest) {
        router.push(dest);
        const name = dest === '/' ? 'Dashboard' : dest.slice(1);
        toast(`→ ${name.charAt(0).toUpperCase() + name.slice(1)}`, 'info', 1_400);
      }
    }
  }, [router, toast]);

  useEffect(() => {
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onKey]);

  return (
    <>
      <ShortcutsModal open={help} onClose={() => setHelp(false)} />
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

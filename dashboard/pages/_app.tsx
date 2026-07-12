/**
 * pages/_app.tsx — S37 polish
 * - Import globals.css (Tailwind) — fără acesta, clasele Tailwind nu funcționează în pages/
 * - Wrap cu ErrorBoundary global
 * - Inject ToastContainer
 * - Init useQuantLunaWS (simulator + WS real)
 */
import type { AppProps } from 'next/app';
import '../app/globals.css';
import { ErrorBoundary }  from '../components/ErrorBoundary';
import { ToastContainer } from '../components/Toast';

export default function App({ Component, pageProps }: AppProps) {
  return (
    <ErrorBoundary>
      <Component {...pageProps} />
      <ToastContainer />
    </ErrorBoundary>
  );
}

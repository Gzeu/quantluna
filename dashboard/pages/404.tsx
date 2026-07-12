/**
 * pages/404.tsx
 * Pagina 404 custom cu branding QuantLuna.
 */
import type { NextPage } from 'next';
import Head              from 'next/head';
import Link              from 'next/link';
import NavBar            from '../components/NavBar';

const NotFound: NextPage = () => (
  <>
    <Head><title>404 — QuantLuna</title></Head>
    <NavBar />
    <main
      className="flex flex-col items-center justify-center animate-fade-in"
      style={{
        minHeight: 'calc(100vh - var(--nav-h))',
        background: 'var(--bg-body)',
        padding: '40px 20px',
      }}
    >
      {/* Glow orb */}
      <div
        className="mb-8 flex items-center justify-center w-32 h-32 rounded-full"
        style={{
          background: 'radial-gradient(circle, rgba(139,92,246,0.15) 0%, transparent 70%)',
          border: '1px solid rgba(139,92,246,0.2)',
          boxShadow: '0 0 60px rgba(139,92,246,0.1)',
        }}
      >
        <span className="text-5xl select-none">404</span>
      </div>

      <h1
        className="text-3xl font-extrabold mb-2"
        style={{
          background: 'linear-gradient(135deg, var(--purple), var(--cyan))',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
        }}
      >
        Pagina nu există
      </h1>

      <p className="text-[var(--text-muted)] text-sm mb-8 text-center max-w-xs">
        Ruta căutată nu a fost găsită în QuantLuna.
        Verifică URL-ul sau navighezi către dashboard.
      </p>

      <Link
        href="/"
        className="ql-btn ql-btn-primary px-6 py-2.5 text-sm"
      >
        ← Întoarce-te la Dashboard
      </Link>

      {/* Shortcut hint */}
      <p className="mt-6 text-[var(--text-disabled)] text-xs">
        Sau apasă <kbd className="ql-kbd">G</kbd> + <kbd className="ql-kbd">D</kbd> pentru Dashboard
      </p>
    </main>
  </>
);

export default NotFound;

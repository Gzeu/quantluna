/**
 * pages/_document.tsx
 * Custom Document: font preload, meta tags, dark background pe <html>.
 * Ruleaza server-side only — nu are acces la hooks/state.
 */
import { Html, Head, Main, NextScript } from 'next/document';

export default function Document() {
  return (
    <Html lang="ro" className="dark">
      <Head>
        {/* Charset + Viewport sunt gestionate automat de Next.js */}
        <meta charSet="utf-8" />
        <meta name="theme-color" content="#0a0a14" />
        <meta name="description" content="QuantLuna — Crypto Stat-Arb Trading Dashboard" />
        <meta name="robots" content="noindex,nofollow" />

        {/* Favicon */}
        <link rel="icon" href="/favicon.ico" />
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />

        {/* Preconnect Google Fonts */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />

        {/* Inter + JetBrains Mono */}
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap"
          rel="stylesheet"
        />

        {/* Prevent FOUC: bg-ul initial = bg-body */}
        <style dangerouslySetInnerHTML={{ __html: `
          html, body { background: #0a0a14; }
        ` }} />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}

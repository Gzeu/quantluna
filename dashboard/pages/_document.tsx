/**
 * pages/_document.tsx — S37 polish
 * HTML shell: lang, meta theme-color, preconnect API, favicon placeholder
 */
import { Html, Head, Main, NextScript } from 'next/document';

export default function Document() {
  const api = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
  return (
    <Html lang="ro">
      <Head>
        <meta name="theme-color" content="#0f0f1a" />
        <meta name="color-scheme" content="dark" />
        <link rel="preconnect" href={api} />
        {/* Favicon — înlocuiește cu SVG real */}
        <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>" />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}

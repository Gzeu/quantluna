/**
 * pages/_document.tsx — S37 UI/UX
 * HTML shell: lang ro, meta theme-color, preconnect API, favicon emoji
 */
import { Html, Head, Main, NextScript } from 'next/document';

export default function Document() {
  return (
    <Html lang="ro">
      <Head>
        <meta name="theme-color" content="#0a0a14" />
        <meta name="color-scheme" content="dark" />
        <link
          rel="preconnect"
          href={process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}
        />
        <link
          rel="icon"
          href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>"
        />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}

import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title:       'QuantLuna Dashboard',
  description: 'Crypto Pairs Trading Engine — Live Dashboard',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ro" className="dark">
      <body>{children}</body>
    </html>
  );
}

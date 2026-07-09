import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: '⟁ QUANTLUNA — Algorithmic Trading Dashboard',
  description: 'Stat-arb trading system: Kalman filter, cointegration, multi-exchange execution',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body style={{ background: '#08080F', overflow: 'hidden', height: '100vh' }}>
        {children}
      </body>
    </html>
  )
}

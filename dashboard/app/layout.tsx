/**
 * dashboard/app/layout.tsx  -  QuantLuna Root Layout v2.0
 * Sprint S43 (2026-07-12): Adauga NavBar in layout global
 *
 * NavBar apare pe toate paginile (persistent top).
 * SidebarPanel ramane doar pe pagina principala (index).
 */
import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import NavBar from '../components/NavBar'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'QuantLuna Dashboard',
  description: 'QuantLuna pairs-trading bot dashboard',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ro">
      <body className={inter.className} style={{
        margin: 0,
        background: '#0f0f1a',
        color: '#e0e0ff',
        minHeight: '100vh',
      }}>
        <NavBar />
        <main style={{ minHeight: 'calc(100vh - 52px)' }}>
          {children}
        </main>
      </body>
    </html>
  )
}

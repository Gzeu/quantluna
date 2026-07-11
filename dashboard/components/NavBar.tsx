/**
 * dashboard/components/NavBar.tsx  -  QuantLuna Navigation Bar v1.1
 * Sprint S45 (2026-07-12): adauga link Watchdog cu badge alerte recente
 */
'use client'
import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/router';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface NavBadge {
  servicesRunning:      number;
  servicesTotal:        number;
  optimizerRunning:     boolean;
  optimizerAutoActive:  boolean;
  watchdogActive:       boolean;
  watchdogAlertsRecent: number;   // alerte in ultimele 5min
}

export default function NavBar() {
  const router = useRouter();
  const [badge, setBadge] = useState<NavBadge>({
    servicesRunning: 0, servicesTotal: 0,
    optimizerRunning: false, optimizerAutoActive: false,
    watchdogActive: false, watchdogAlertsRecent: 0,
  });

  useEffect(() => {
    async function fetchBadges() {
      try {
        const [svc, opt, wd] = await Promise.all([
          fetch(`${API}/api/services/list`).then(r => r.json()).catch(() => null),
          fetch(`${API}/api/optimizer/status`).then(r => r.json()).catch(() => null),
          fetch(`${API}/api/watchdog/status`).then(r => r.json()).catch(() => null),
        ]);

        // Alerte recente (ultimele 5min)
        let alertsRecent = 0;
        if (wd?.recent_alerts) {
          const now = Date.now();
          alertsRecent = (wd.recent_alerts as any[]).filter(a => {
            return now - new Date(a.timestamp).getTime() < 5 * 60 * 1000;
          }).length;
        }

        setBadge({
          servicesRunning:      svc?.running  ?? 0,
          servicesTotal:        svc?.total    ?? 0,
          optimizerRunning:     opt?.running  ?? false,
          optimizerAutoActive:  opt?.auto_reoptimizer_active ?? false,
          watchdogActive:       wd?.running   ?? false,
          watchdogAlertsRecent: alertsRecent,
        });
      } catch {}
    }
    fetchBadges();
    const id = setInterval(fetchBadges, 3000);
    return () => clearInterval(id);
  }, []);

  const current = router.pathname;

  const links: Array<{
    href: string; label: string; badge?: React.ReactNode;
  }> = [
    { href: '/', label: '📈 Dashboard' },
    { href: '/portfolio', label: '💼 Portfolio' },
    {
      href: '/services',
      label: '⚙ Services',
      badge: badge.servicesTotal > 0 ? (
        <span style={{
          fontSize: 10, fontWeight: 700, marginLeft: 4,
          color: badge.servicesRunning > 0 ? '#4ade80' : '#666',
        }}>{badge.servicesRunning}/{badge.servicesTotal}</span>
      ) : undefined,
    },
    {
      href: '/optimizer',
      label: '🔬 Optimizer',
      badge: (
        <span style={{
          fontSize: 9, marginLeft: 5,
          color: badge.optimizerRunning    ? '#4ade80'
               : badge.optimizerAutoActive ? '#8b5cf6'
               : '#555',
        }}>
          {badge.optimizerRunning    ? '● run'
           : badge.optimizerAutoActive ? '● auto'
           : '○'}
        </span>
      ),
    },
    {
      href: '/watchdog',
      label: '👁 Watchdog',
      badge: (
        <span style={{
          fontSize: 9, marginLeft: 5,
          color: badge.watchdogAlertsRecent > 0 ? '#f87171'
               : badge.watchdogActive           ? '#4ade80'
               : '#555',
          fontWeight: badge.watchdogAlertsRecent > 0 ? 700 : 400,
        }}>
          {badge.watchdogAlertsRecent > 0
            ? `🚨 ${badge.watchdogAlertsRecent}`
            : badge.watchdogActive ? '●'
            : '○'}
        </span>
      ),
    },
  ];

  return (
    <nav style={{
      background: '#0d0d1a',
      borderBottom: '1px solid #1a1a2e',
      padding: '0 32px',
      display: 'flex',
      alignItems: 'center',
      height: 52,
      position: 'sticky',
      top: 0,
      zIndex: 100,
      backdropFilter: 'blur(8px)',
    }}>
      {/* Logo */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        marginRight: 32, flexShrink: 0,
      }}>
        <span style={{
          fontSize: 16, fontWeight: 800,
          background: 'linear-gradient(135deg, #8b5cf6, #22d3ee)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          letterSpacing: -0.5,
        }}>QuantLuna</span>
        <span style={{
          width: 6, height: 6, borderRadius: '50%',
          background: '#4ade80',
          boxShadow: '0 0 6px #4ade80',
          display: 'inline-block',
        }} />
      </div>

      {/* Links */}
      <div style={{ display: 'flex', gap: 2, flex: 1 }}>
        {links.map(({ href, label, badge }) => {
          const isActive = current === href
            || (href !== '/' && current.startsWith(href));
          return (
            <Link
              key={href}
              href={href}
              style={{
                display: 'flex', alignItems: 'center',
                padding: '0 14px', height: 52,
                textDecoration: 'none',
                fontSize: 13, fontWeight: isActive ? 700 : 400,
                color: isActive ? '#e0e0ff' : '#666',
                borderBottom: isActive ? '2px solid #8b5cf6' : '2px solid transparent',
                transition: 'color 0.15s, border-color 0.15s',
              }}
              onMouseEnter={e => {
                if (!isActive)
                  (e.currentTarget as HTMLAnchorElement).style.color = '#aaa';
              }}
              onMouseLeave={e => {
                if (!isActive)
                  (e.currentTarget as HTMLAnchorElement).style.color = '#666';
              }}
            >{label}{badge}</Link>
          );
        })}
      </div>

      <LiveClock />
    </nav>
  );
}

function LiveClock() {
  const [t, setT] = useState('');
  useEffect(() => {
    const update = () => setT(
      new Date().toLocaleTimeString('ro-RO', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      })
    );
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span style={{
      fontFamily: 'monospace', fontSize: 12,
      color: '#444', flexShrink: 0,
    }}>{t}</span>
  );
}

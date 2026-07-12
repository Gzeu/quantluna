/**
 * NavBar.tsx — S37 polish
 * Migrat la CSS vars (fara inline styles hardcodate).
 * Active route cu var(--purple) border-bottom.
 * Kbd ? hint in dreapta (shortcut help modal).
 * LiveClock monospaced.
 */
'use client';
import React, { useEffect, useState } from 'react';
import Link       from 'next/link';
import { useRouter } from 'next/router';
import { Kbd }    from './ui/Kbd';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface NavBadge {
  servicesRunning:      number;
  servicesTotal:        number;
  optimizerRunning:     boolean;
  optimizerAutoActive:  boolean;
  watchdogActive:       boolean;
  watchdogAlertsRecent: number;
}

export default function NavBar() {
  const router = useRouter();
  const [badge, setBadge] = useState<NavBadge>({
    servicesRunning: 0, servicesTotal: 0,
    optimizerRunning: false, optimizerAutoActive: false,
    watchdogActive: false, watchdogAlertsRecent: 0,
  });

  useEffect(() => {
    const fetchBadges = async () => {
      try {
        const [svc, opt, wd] = await Promise.all([
          fetch(`${API}/api/services/list`).then(r => r.json()).catch(() => null),
          fetch(`${API}/api/optimizer/status`).then(r => r.json()).catch(() => null),
          fetch(`${API}/api/watchdog/status`).then(r => r.json()).catch(() => null),
        ]);
        const now = Date.now();
        const alertsRecent = (wd?.recent_alerts as any[] ?? []).filter(
          (a: any) => now - new Date(a.timestamp).getTime() < 5 * 60_000
        ).length;
        setBadge({
          servicesRunning:      svc?.running  ?? 0,
          servicesTotal:        svc?.total    ?? 0,
          optimizerRunning:     opt?.running  ?? false,
          optimizerAutoActive:  opt?.auto_reoptimizer_active ?? false,
          watchdogActive:       wd?.running   ?? false,
          watchdogAlertsRecent: alertsRecent,
        });
      } catch {}
    };
    fetchBadges();
    const id = setInterval(fetchBadges, 3_000);
    return () => clearInterval(id);
  }, []);

  const current = router.pathname;

  const links: Array<{ href: string; label: string; badge?: React.ReactNode }> = [
    { href: '/',          label: '📈 Dashboard' },
    { href: '/portfolio', label: '💼 Portfolio' },
    {
      href: '/services',
      label: '⚙ Services',
      badge: badge.servicesTotal > 0 ? (
        <span className={`text-[10px] font-bold ml-1 ${
          badge.servicesRunning > 0 ? 'text-green-400' : 'text-[var(--text-muted)]'
        }`}>
          {badge.servicesRunning}/{badge.servicesTotal}
        </span>
      ) : undefined,
    },
    {
      href: '/optimizer',
      label: '🔬 Optimizer',
      badge: (
        <span className={`text-[9px] ml-1 ${
          badge.optimizerRunning    ? 'text-green-400'
          : badge.optimizerAutoActive ? 'text-purple-400'
          : 'text-[var(--text-disabled)]'
        }`}>
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
        <span className={`text-[9px] ml-1 font-bold ${
          badge.watchdogAlertsRecent > 0 ? 'text-red-400'
          : badge.watchdogActive         ? 'text-green-400'
          : 'text-[var(--text-disabled)]'
        }`}>
          {badge.watchdogAlertsRecent > 0
            ? `🚨 ${badge.watchdogAlertsRecent}`
            : badge.watchdogActive ? '●'
            : '○'}
        </span>
      ),
    },
    { href: '/strategy', label: '🧠 Strategy' },
  ];

  return (
    <nav
      className="flex items-center px-8 sticky top-0 z-[100]"
      style={{
        background: 'rgba(13,13,26,0.92)',
        borderBottom: '1px solid var(--border)',
        height: 'var(--nav-h)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
      }}
    >
      {/* Logo */}
      <div className="flex items-center gap-2 mr-8 shrink-0">
        <span
          className="text-base font-extrabold"
          style={{
            background: 'linear-gradient(135deg, var(--purple), var(--cyan))',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            letterSpacing: -0.5,
          }}
        >
          QuantLuna
        </span>
        <span
          className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse"
          style={{ boxShadow: '0 0 6px #4ade80' }}
        />
      </div>

      {/* Links */}
      <div className="flex flex-1 h-full">
        {links.map(({ href, label, badge }) => {
          const isActive = current === href
            || (href !== '/' && current.startsWith(href));
          return (
            <Link
              key={href}
              href={href}
              className={`
                flex items-center px-3.5 h-full text-[13px] no-underline
                transition-colors duration-150
                border-b-2 border-t-2 border-t-transparent
                ${
                  isActive
                    ? 'font-bold text-[var(--text-primary)] border-b-[var(--purple)]'
                    : 'font-normal text-[var(--text-muted)] border-b-transparent hover:text-[var(--text-secondary)]'
                }
              `}
            >
              {label}{badge}
            </Link>
          );
        })}
      </div>

      {/* Right: clock + kbd hint */}
      <div className="flex items-center gap-3 shrink-0">
        <button
          onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '?' }))}
          className="flex items-center gap-1 text-[var(--text-muted)] hover:text-[var(--text-secondary)]
                     transition-colors text-[10px] cursor-pointer"
          data-tooltip="Keyboard shortcuts"
          aria-label="Show keyboard shortcuts"
        >
          <span>shortcuts</span>
          <Kbd>?</Kbd>
        </button>
        <LiveClock />
      </div>
    </nav>
  );
}

function LiveClock() {
  const [t, setT] = useState('');
  useEffect(() => {
    const tick = () => setT(
      new Date().toLocaleTimeString('ro-RO', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      })
    );
    tick();
    const id = setInterval(tick, 1_000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="mono text-[11px] text-[var(--text-muted)] tabular">{t}</span>
  );
}

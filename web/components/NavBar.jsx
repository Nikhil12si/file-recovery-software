'use client';
import { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { isLiveMode, setLiveMode, getAgentUrl, checkAgentHealth } from '@/lib/api';
import { useAuth } from './AuthProvider';
import { Icon, classNames } from './ui';
import ThemeToggle from './ThemeToggle';

const LINKS = [
  { href: '/dashboard', label: 'Recover', icon: 'restore' },
  { href: '/history', label: 'History & Reports', icon: 'clock' },
  { href: '/manual', label: 'User Manual', icon: 'book' },
  { href: '/agent', label: 'Windows Setup & ZIP', icon: 'download' },
];

export default function NavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [live, setLive] = useState(false);
  const [agentUrl, setAgentUrl] = useState('http://127.0.0.1:8000');
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const updateMode = () => {
      setLive(isLiveMode());
      setAgentUrl(getAgentUrl());
    };
    updateMode();

    // Auto-detect if local agent is running if mode hasn't been chosen yet
    if (typeof window !== 'undefined') {
      const explicit = window.localStorage.getItem('frs_mode');
      if (!explicit) {
        checkAgentHealth().then((healthy) => {
          if (healthy) {
            setLiveMode(true);
            setLive(true);
          }
        });
      }
    }

    window.addEventListener('frs_mode_change', updateMode);
    window.addEventListener('storage', updateMode);
    return () => {
      window.removeEventListener('frs_mode_change', updateMode);
      window.removeEventListener('storage', updateMode);
    };
  }, []);

  async function handleGoLive() {
    setChecking(true);
    const healthy = await checkAgentHealth();
    setChecking(false);
    if (healthy) {
      setLiveMode(true);
    } else {
      router.push('/agent');
    }
  }

  const agentHost = agentUrl.replace(/^https?:\/\//i, '');

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200/80 bg-white/90 backdrop-blur transition-colors duration-200 dark:border-slate-800/90 dark:bg-[#0b1120]/95 dark:shadow-md dark:shadow-black/20">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold text-slate-900 transition-colors dark:text-white">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white shadow-sm shadow-brand-500/20"><Icon name="drive" /></span>
          <span className="hidden sm:inline font-bold">HDD File Recovery</span>
        </Link>
        <nav className="flex items-center gap-1">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href}
              className={classNames('flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
                pathname && pathname.startsWith(l.href)
                  ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/70 dark:text-brand-300 dark:border dark:border-brand-800/50'
                  : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800/80 dark:hover:text-white')}>
              <Icon name={l.icon} /> <span className="hidden md:inline">{l.label}</span>
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          {live ? (
            <div className="flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-50/90 px-2.5 py-1 text-xs font-semibold text-emerald-800 shadow-sm dark:border-emerald-500/30 dark:bg-emerald-950/50 dark:text-emerald-300">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500"></span>
              </span>
              <span>LIVE ENGINE</span>
              <span className="hidden text-[10px] font-normal opacity-80 sm:inline font-mono">({agentHost})</span>
              <button
                type="button"
                title="Switch to Simulation (Mock Data) Mode"
                onClick={() => setLiveMode(false)}
                className="ml-1 rounded bg-emerald-200/60 px-1 py-0.5 text-[10px] text-emerald-900 hover:bg-emerald-200 dark:bg-emerald-900/60 dark:text-emerald-200"
              >
                Simulate
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-50/90 px-2.5 py-1 text-xs font-semibold text-amber-800 shadow-sm dark:border-amber-500/30 dark:bg-amber-950/50 dark:text-amber-300">
              <span className="h-2 w-2 rounded-full bg-amber-500"></span>
              <span>SIMULATION (MOCK)</span>
              <button
                type="button"
                title="Connect to local Windows Recovery Agent"
                onClick={handleGoLive}
                disabled={checking}
                className="ml-1 rounded bg-amber-200 px-1.5 py-0.5 text-[10px] font-bold text-amber-900 hover:bg-amber-300 dark:bg-amber-900/70 dark:text-amber-200 transition-colors"
              >
                {checking ? 'Checking...' : 'Go Live'}
              </button>
            </div>
          )}
          <ThemeToggle />
          {user ? (
            <>
              <span className="hidden text-slate-600 lg:inline dark:text-slate-300 font-medium">{user.full_name || user.email}</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs uppercase text-slate-500 dark:bg-slate-800 dark:text-slate-400 dark:border dark:border-slate-700">{user.role}</span>
              <button type="button" className="btn-ghost px-3 py-1.5" onClick={logout}>Sign out</button>
            </>
          ) : (
            <Link href="/login" className="btn-primary px-3 py-1.5">Sign in</Link>
          )}
        </div>
      </div>
    </header>
  );
}

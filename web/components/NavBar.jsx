'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { usesLocalAgent, usingMocks } from '@/lib/api';
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
  const { user, logout } = useAuth();
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
          {usingMocks && (
            <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 dark:border dark:border-amber-800/40">
              MOCK DATA
            </span>
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

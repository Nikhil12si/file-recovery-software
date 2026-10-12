'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { usesLocalAgent, usingMocks } from '@/lib/api';
import { useAuth } from './AuthProvider';
import { Icon, classNames } from './ui';

const LINKS = [
  { href: '/dashboard', label: 'Recover', icon: 'restore' },
  { href: '/history', label: 'History & Reports', icon: 'clock' },
  { href: '/manual', label: 'User Manual', icon: 'book' },
  // Hosted build only: how to install the Recovery Agent on this PC.
  ...(usesLocalAgent ? [{ href: '/agent', label: 'Agent setup', icon: 'shield' }] : []),
];

export default function NavBar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold text-slate-900">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white"><Icon name="drive" /></span>
          <span className="hidden sm:inline">HDD File Recovery</span>
        </Link>
        <nav className="flex items-center gap-1">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href}
              className={classNames('flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium',
                pathname && pathname.startsWith(l.href) ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-100')}>
              <Icon name={l.icon} /> <span className="hidden md:inline">{l.label}</span>
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          {usingMocks && <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">MOCK DATA</span>}
          {user ? (
            <>
              <span className="hidden text-slate-600 lg:inline">{user.full_name || user.email}</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs uppercase text-slate-500">{user.role}</span>
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

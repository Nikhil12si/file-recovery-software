'use client';
/**
 * ui.jsx - small, dependency-free UI primitives shared by the dashboard,
 * history page and user manual (Track C).
 */
import { useEffect } from 'react';

export function formatBytes(n) {
  const v = Number(n || 0);
  if (v < 1024) return `${v} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let x = v / 1024;
  let i = 0;
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i += 1;
  }
  return `${x.toFixed(x >= 100 ? 0 : 1)} ${units[i]}`;
}

export function formatDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function classNames(...xs) {
  return xs.filter(Boolean).join(' ');
}

const ICONS = {
  drive: 'M4 6h16v8H4z M4 14l2 4h12l2-4 M7 10h.01',
  folder: 'M3 7h6l2 2h10v10H3z',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M21 21l-4.3-4.3',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  warning: 'M12 3l10 18H2z M12 10v4 M12 17h.01',
  check: 'M5 12l5 5L20 7',
  x: 'M6 6l12 12 M18 6L6 18',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  layers: 'M12 3l9 5-9 5-9-5z M3 13l9 5 9-5',
  book: 'M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z M20 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z',
  download: 'M12 3v12 M7 10l5 5 5-5 M4 20h16',
  restore: 'M3 12a9 9 0 1 0 3-6.7 M3 4v5h5',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  file: 'M6 2h8l6 6v14H6z M14 2v6h6',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 7v5l3 3',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 11v6 M12 7h.01',
  sun: 'M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z',
  moon: 'M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z',
};

export function Icon({ name, className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      className={className} aria-hidden="true">
      {(ICONS[name] || '').split(' M').map((d, i) => <path key={i} d={i === 0 ? d : `M${d}`} />)}
    </svg>
  );
}

export function Spinner({ className = 'h-4 w-4' }) {
  return (
    <svg className={classNames('animate-spin', className)} viewBox="0 0 24 24" fill="none" aria-label="loading">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" className="opacity-25" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="4" className="opacity-75" />
    </svg>
  );
}

const CONF_STYLES = {
  high: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950/60 dark:text-emerald-300 dark:ring-emerald-500/30',
  medium: 'bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-950/60 dark:text-amber-300 dark:ring-amber-500/30',
  low: 'bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-950/60 dark:text-red-300 dark:ring-red-500/30',
};

export function ConfidenceBadge({ level, score, reasons }) {
  const label = level ? level[0].toUpperCase() + level.slice(1) : 'Unknown';
  return (
    <span title={reasons && reasons.length ? reasons.join('\n') : undefined}
      className={classNames('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset',
        CONF_STYLES[level] || 'bg-slate-100 text-slate-600 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700')}>
      <span className={classNames('h-1.5 w-1.5 rounded-full',
        level === 'high' ? 'bg-emerald-500' : level === 'medium' ? 'bg-amber-500' : 'bg-red-500')} />
      {label}{score !== undefined && score !== null ? ` · ${score}` : ''}
    </span>
  );
}

export function Badge({ children, tone = 'slate' }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    blue: 'bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300',
    green: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300',
    amber: 'bg-amber-50 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
    red: 'bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300',
    purple: 'bg-violet-50 text-violet-700 dark:bg-violet-950/60 dark:text-violet-300',
  };
  return <span className={classNames('inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium', tones[tone])}>{children}</span>;
}

export function ProgressBar({ value, tone = 'brand', striped = false }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  const color = tone === 'green' ? 'bg-emerald-500' : tone === 'red' ? 'bg-red-500' : 'bg-brand-600 dark:bg-indigo-500';
  return (
    <div className="h-3 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800" role="progressbar" aria-valuenow={pct} aria-valuemin={0}
      aria-valuemax={100}>
      <div className={classNames('h-full rounded-full transition-all duration-500', color, striped && 'animate-pulse')}
        style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Alert({ tone = 'info', title, children, onClose }) {
  const styles = {
    info: 'border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-900/60 dark:bg-blue-950/50 dark:text-blue-200',
    warning: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/50 dark:text-amber-200',
    danger: 'border-red-200 bg-red-50 text-red-900 dark:border-red-900/60 dark:bg-red-950/50 dark:text-red-200',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-950/50 dark:text-emerald-200',
  };
  return (
    <div className={classNames('flex gap-3 rounded-lg border p-3 text-sm', styles[tone])} role={tone === 'danger' ? 'alert' : 'status'}>
      <Icon name={tone === 'success' ? 'check' : tone === 'info' ? 'info' : 'warning'} className="mt-0.5 h-4 w-4 flex-none" />
      <div className="flex-1">
        {title && <div className="font-semibold">{title}</div>}
        <div className={title ? 'mt-0.5' : ''}>{children}</div>
      </div>
      {onClose && (
        <button type="button" onClick={onClose} className="opacity-60 hover:opacity-100" aria-label="Dismiss">
          <Icon name="x" />
        </button>
      )}
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer, wide = false }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === 'Escape' && onClose && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/60 dark:bg-black/70 backdrop-blur-sm p-4 sm:items-center"
      onMouseDown={(e) => e.target === e.currentTarget && onClose && onClose()} role="dialog" aria-modal="true">
      <div className={classNames('card w-full', wide ? 'max-w-4xl' : 'max-w-xl')}>
        <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 px-5 py-3">
          <h3 className="truncate text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          <button type="button" className="btn-ghost px-2" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-200 dark:border-slate-800 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function StatCard({ label, value, hint, tone }) {
  const color = tone === 'green' ? 'text-emerald-600 dark:text-emerald-400'
    : tone === 'amber' ? 'text-amber-600 dark:text-amber-400'
    : tone === 'red' ? 'text-red-600 dark:text-red-400'
    : 'text-slate-900 dark:text-slate-100';
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</div>
      <div className={classNames('mt-1 text-2xl font-bold', color)}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{hint}</div>}
    </div>
  );
}

export function EmptyState({ title, children, icon = 'search' }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <div className="rounded-full bg-slate-100 dark:bg-slate-800 p-3 text-slate-500 dark:text-slate-400"><Icon name={icon} className="h-6 w-6" /></div>
      <div className="mt-3 font-semibold text-slate-800 dark:text-slate-200">{title}</div>
      {children && <div className="mt-1 max-w-md text-sm text-slate-500 dark:text-slate-400">{children}</div>}
    </div>
  );
}

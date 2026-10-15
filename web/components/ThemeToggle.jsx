'use client';

import { useEffect, useState } from 'react';
import { Icon } from './ui';

export default function ThemeToggle({ className = '' }) {
  const [isDark, setIsDark] = useState(true);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const savedTheme = localStorage.getItem('theme');
    // Default to dark mode as requested by user
    const shouldBeDark = savedTheme ? savedTheme === 'dark' : true;
    setIsDark(shouldBeDark);
    if (shouldBeDark) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, []);

  const toggleTheme = () => {
    const nextDark = !isDark;
    setIsDark(nextDark);
    if (nextDark) {
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  };

  // Prevent hydration mismatch
  if (!mounted) {
    return (
      <div className={`h-8 w-8 rounded-lg border border-slate-700/60 bg-slate-800/50 ${className}`} />
    );
  }

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
      aria-label={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
      className={`relative inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-brand-500/50 ${
        isDark
          ? 'border-slate-700/80 bg-slate-800/80 text-amber-300 hover:bg-slate-700/90 hover:text-amber-200 shadow-sm'
          : 'border-slate-300 bg-slate-100 text-slate-700 hover:bg-slate-200 hover:text-slate-900 shadow-sm'
      } ${className}`}
    >
      <Icon name={isDark ? 'sun' : 'moon'} className="h-4 w-4" />
      <span className="hidden sm:inline text-[11px] font-semibold">
        {isDark ? 'Light' : 'Dark'}
      </span>
    </button>
  );
}

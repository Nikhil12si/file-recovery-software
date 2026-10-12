'use client';
/**
 * AgentStatus.jsx - detects the local Recovery Agent (FastAPI backend + engine on this PC).
 *
 * In the hosted (Firebase) build the website is only the user interface: every scan and restore
 * runs in the agent on the visitor's own computer, reached at http://127.0.0.1:8000.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { api, usingMocks, usesLocalAgent } from '@/lib/api';
import { Alert } from './ui';

/** { state: 'checking' | 'connected' | 'missing', health } - re-checked every `intervalMs`. */
export function useAgentStatus(intervalMs = 4000) {
  const [status, setStatus] = useState({ state: 'checking', health: null });
  useEffect(() => {
    if (usingMocks) {
      setStatus({ state: 'connected', health: null });
      return undefined;
    }
    let alive = true;
    let timer = null;
    const check = async () => {
      try {
        const health = await api.health();
        if (alive) setStatus({ state: 'connected', health });
      } catch {
        if (alive) setStatus({ state: 'missing', health: null });
      }
      if (alive) timer = setTimeout(check, intervalMs);
    };
    check();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [intervalMs]);
  return status;
}

/** Shown under the navigation bar while the agent cannot be reached (hosted build only). */
export function AgentBanner() {
  const pathname = usePathname();
  const { state } = useAgentStatus(5000);
  if (!usesLocalAgent || state !== 'missing' || (pathname || '').startsWith('/agent')) return null;
  return (
    <div className="mx-auto max-w-7xl px-4 pt-4">
      <Alert tone="warning" title="Recovery Agent not detected on this computer">
        This website scans and restores through a small program that runs on your own PC, so your disks never leave it.{' '}
        <Link href="/agent" className="font-semibold underline">Set up the Recovery Agent</Link>
        {' '}(about 5 minutes, once).
      </Alert>
    </div>
  );
}

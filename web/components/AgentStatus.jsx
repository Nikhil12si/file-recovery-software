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
import { checkAgentHealth, isLiveMode, setLiveMode, getAgentUrl } from '@/lib/api';
import { Alert } from './ui';

/** { state: 'checking' | 'connected' | 'missing', health } - re-checked every `intervalMs`. */
export function useAgentStatus(intervalMs = 4000) {
  const [status, setStatus] = useState({ state: 'checking', health: null });
  useEffect(() => {
    let alive = true;
    let timer = null;
    const check = async () => {
      try {
        const healthy = await checkAgentHealth();
        if (alive) {
          setStatus({ state: healthy ? 'connected' : 'missing', health: healthy ? { status: 'ok' } : null });
        }
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

/** Shown under the navigation bar when live mode is active but agent cannot be reached. */
export function AgentBanner() {
  const pathname = usePathname();
  const { state } = useAgentStatus(6000);
  const [isLive, setIsLive] = useState(false);

  useEffect(() => {
    setIsLive(isLiveMode());
    const handler = () => setIsLive(isLiveMode());
    window.addEventListener('frs_mode_change', handler);
    return () => window.removeEventListener('frs_mode_change', handler);
  }, []);

  if (!isLive || state !== 'missing' || (pathname || '').startsWith('/agent')) return null;

  return (
    <div className="mx-auto max-w-7xl px-4 pt-4">
      <Alert tone="warning" title="Windows Recovery Agent disconnected">
        Cannot reach the recovery agent on this computer at {getAgentUrl()}. Please run <code className="font-mono text-xs">start-agent.bat</code> or{' '}
        <button
          type="button"
          onClick={() => setLiveMode(false)}
          className="font-semibold underline text-amber-900 dark:text-amber-200"
        >
          switch to Simulation Mode
        </button>.
      </Alert>
    </div>
  );
}

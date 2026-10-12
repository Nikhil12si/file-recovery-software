'use client';
/**
 * AgentSetup.jsx - /agent: how a visitor installs and starts the local Recovery Agent,
 * with a live connection indicator.
 */
import Link from 'next/link';
import { agentAddress, usesLocalAgent } from '@/lib/api';
import { useAgentStatus } from './AgentStatus';
import { Alert, Badge, Icon, Spinner } from './ui';

const AGENT_ZIP = '/downloads/recovery-agent.zip';

function Step({ n, title, children }) {
  return (
    <li className="flex gap-4">
      <span className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-brand-600 text-sm font-semibold text-white">{n}</span>
      <div className="min-w-0 flex-1 pb-6">
        <div className="font-semibold text-slate-900">{title}</div>
        <div className="mt-1 space-y-2 text-sm leading-6 text-slate-600">{children}</div>
      </div>
    </li>
  );
}

export default function AgentSetup() {
  const { state, health } = useAgentStatus(3000);
  const simulated = health && health.engine && health.engine.mock;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Set up the Recovery Agent</h1>
        <p className="mt-1 text-sm text-slate-500">
          This website is the control panel. The scanning and restoring is done by the Recovery Agent, a small program on your own
          Windows PC, so your drives and recovered files never leave your computer.
        </p>
      </div>

      <div className="card flex flex-wrap items-center gap-3 p-4">
        <span className="text-sm font-medium text-slate-700">Agent on this computer:</span>
        {state === 'checking' && <span className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> checking…</span>}
        {state === 'missing' && <Badge tone="red">not running</Badge>}
        {state === 'connected' && <Badge tone="green">connected</Badge>}
        {state === 'connected' && health && (
          <Badge tone={simulated ? 'amber' : 'green'}>{simulated ? 'simulation mode (engine not built)' : 'real disk engine ready'}</Badge>
        )}
        {state === 'connected' && <Link href="/dashboard" className="btn-primary ml-auto px-3 py-1.5">Start recovering</Link>}
        {state === 'missing' && <span className="ml-auto text-xs text-slate-500">This page checks again every 3 seconds.</span>}
      </div>

      {!usesLocalAgent && (
        <Alert tone="info">This copy of the website runs next to its own backend, so no separate agent is needed here.</Alert>
      )}

      <ol className="card p-6">
        <Step n={1} title="Install Python (once)">
          <p>
            Download Python 3.12 from <a className="font-medium text-brand-600 underline" href="https://www.python.org/downloads/" target="_blank" rel="noreferrer">python.org</a>.
            On the first installer screen tick <strong>Add python.exe to PATH</strong>, then click Install.
          </p>
        </Step>
        <Step n={2} title="Download and extract the agent">
          <p>
            <a className="btn-primary px-3 py-1.5" href={AGENT_ZIP} download><Icon name="download" /> Download recovery-agent.zip</a>
          </p>
          <p>Right-click the downloaded file, choose <strong>Extract All</strong>, and extract it to a folder such as <code>C:\RecoveryAgent</code>.</p>
        </Step>
        <Step n={3} title="Start the agent">
          <p>
            Open the extracted folder and double-click <strong>start-agent.bat</strong>. If Windows shows a security warning, click
            {' '}<strong>More info</strong> then <strong>Run anyway</strong> (or <strong>Run</strong>). Click <strong>Yes</strong> when it asks
            for Administrator rights: reading a disk sector by sector requires them.
          </p>
          <p>
            The first start installs its components (3 to 5 minutes, needs internet). It is ready when the blue window shows
            {' '}<code>Uvicorn running on http://127.0.0.1:8000</code>. Keep that window open while you use this website.
          </p>
        </Step>
        <Step n={4} title="Allow this website to reach the agent">
          <p>
            Your browser may ask whether this site may connect to apps or devices on your computer or local network. Click <strong>Allow</strong>:
            that is how the website talks to the agent at <code>{agentAddress || 'http://127.0.0.1:8000'}</code>.
          </p>
          <p>The status above turns green within a few seconds. Then create your account and start a scan.</p>
        </Step>
      </ol>

      <Alert tone="warning" title="Safety rules">
        Restore recovered files to a different drive than the one you are scanning (the agent refuses otherwise), and stop using the
        affected drive until recovery is finished. Without the real engine the agent runs in simulation mode with demo disks.
      </Alert>
    </div>
  );
}

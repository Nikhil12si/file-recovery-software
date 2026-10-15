'use client';
/**
 * AgentSetup.jsx - Windows Recovery Agent Download, Setup Guide & Live Verification.
 */
import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Icon, Alert, Badge, Spinner } from './ui';

const AGENT_ZIP = '/downloads/recovery-agent.zip';
const DEFAULT_AGENT_URL = 'http://127.0.0.1:8000';

export default function AgentSetup() {
  const [agentUrl, setAgentUrl] = useState(DEFAULT_AGENT_URL);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState({
    checked: false,
    connected: false,
    activeUrl: DEFAULT_AGENT_URL,
    health: null,
    system: null,
    error: null,
  });
  const [copiedCmd, setCopiedCmd] = useState(null);

  // Load saved agent URL on mount
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const saved = window.localStorage.getItem('frs_agent_url');
      if (saved) {
        setAgentUrl(saved);
        testConnection(saved);
        return;
      }
    }
    testConnection(DEFAULT_AGENT_URL);
  }, []);

  // Check connection to local Windows recovery agent
  async function testConnection(targetUrl = null) {
    const primary = (targetUrl || agentUrl || DEFAULT_AGENT_URL).trim().replace(/\/+$/, '');
    const candidates = [primary];
    if (primary === 'http://127.0.0.1:8000') {
      candidates.push('http://localhost:8000');
    }

    setTesting(true);
    setStatus((prev) => ({ ...prev, checked: true, error: null }));

    let connected = false;
    let lastErr = null;

    for (const url of candidates) {
      try {
        const healthRes = await fetch(`${url}/api/health`, {
          method: 'GET',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
        });
        if (!healthRes.ok) continue;
        const healthData = await healthRes.json();

        let systemData = null;
        try {
          const sysRes = await fetch(`${url}/api/system/status`, {
            method: 'GET',
            cache: 'no-store',
          });
          if (sysRes.ok) systemData = await sysRes.json();
        } catch { /* optional */ }

        if (typeof window !== 'undefined') {
          window.localStorage.setItem('frs_agent_url', url);
        }

        setStatus({
          checked: true,
          connected: true,
          activeUrl: url,
          health: healthData,
          system: systemData,
          error: null,
        });
        setAgentUrl(url);
        connected = true;
        break;
      } catch (err) {
        lastErr = err;
      }
    }

    if (!connected) {
      setStatus({
        checked: true,
        connected: false,
        activeUrl: primary,
        health: null,
        system: null,
        error: lastErr ? (lastErr.message || 'Cannot reach local agent') : 'No response from local agent',
      });
    }

    setTesting(false);
  }

  function copyToClipboard(text, id) {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedCmd(id);
      setTimeout(() => setCopiedCmd(null), 2000);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8 pb-12">
      {/* Page Title & Intro */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white shadow-sm shadow-brand-500/20">
            <Icon name="download" className="h-4 w-4" />
          </span>
          <span className="text-xs font-semibold uppercase tracking-wider text-brand-600 dark:text-indigo-400">
            Windows Recovery Suite
          </span>
        </div>
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-white">
          Recovery Agent Setup & Download
        </h1>
        <p className="text-base text-slate-600 dark:text-slate-300">
          This web application acts as your recovery control panel. Because recovering deleted files from hard drives requires direct,
          low-level sector access on Windows (e.g. <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-800 dark:bg-slate-800 dark:text-slate-200">\\.\PhysicalDrive0</code>),
          the scanning and reconstruction is performed by the <strong>Recovery Agent</strong> on your own PC. Your personal drive data never leaves your computer.
        </p>
      </div>

      {/* =========================================================================
          SECTION 1: DIRECT DOWNLOAD CARD
      ========================================================================= */}
      <div className="rounded-2xl border border-slate-200 bg-gradient-to-br from-white to-slate-50 p-6 shadow-sm dark:border-slate-800 dark:bg-gradient-to-br dark:from-slate-900 dark:to-slate-950">
        <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300 dark:border dark:border-emerald-800/50">
                Official Package (.ZIP)
              </span>
              <span className="text-xs text-slate-500 dark:text-slate-400">Version 1.0.0 • ~470 KB</span>
              <span className="text-xs text-slate-500 dark:text-slate-400">• Windows 10/11 (64-bit)</span>
            </div>
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">
              Download Windows Recovery Agent
            </h2>
            <p className="text-sm text-slate-600 dark:text-slate-300 max-w-xl">
              Includes pre-compiled C++ Engine (<code className="font-mono text-xs">win_recovery_engine.exe</code>),
              FastAPI local bridge, one-click launcher (<code className="font-mono text-xs">start-agent.bat</code>), and SQLite schema.
            </p>
          </div>
          <div className="flex flex-col gap-2 shrink-0">
            <a
              href={AGENT_ZIP}
              download="recovery-agent.zip"
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand-600 px-6 py-3.5 text-sm font-bold text-white shadow-md shadow-brand-500/25 transition-all hover:bg-brand-700 hover:shadow-lg hover:shadow-brand-500/30 active:scale-95"
            >
              <Icon name="download" className="h-5 w-5" />
              Download recovery-agent.zip
            </a>
            <span className="text-center text-[11px] text-slate-500 dark:text-slate-400">
              Direct download • No external dependencies required
            </span>
          </div>
        </div>

        {/* Package Highlights Grid */}
        <div className="mt-6 grid grid-cols-2 gap-3 border-t border-slate-200/80 pt-4 sm:grid-cols-4 dark:border-slate-800">
          <div className="rounded-lg bg-white/70 p-2.5 text-center shadow-xs dark:bg-slate-800/50">
            <div className="text-xs font-medium text-slate-500 dark:text-slate-400">Disk Engine</div>
            <div className="mt-0.5 text-xs font-bold text-slate-800 dark:text-slate-100">C++ Native (MSVC)</div>
          </div>
          <div className="rounded-lg bg-white/70 p-2.5 text-center shadow-xs dark:bg-slate-800/50">
            <div className="text-xs font-medium text-slate-500 dark:text-slate-400">Communication</div>
            <div className="mt-0.5 text-xs font-bold text-slate-800 dark:text-slate-100">Localhost (Port 8000)</div>
          </div>
          <div className="rounded-lg bg-white/70 p-2.5 text-center shadow-xs dark:bg-slate-800/50">
            <div className="text-xs font-medium text-slate-500 dark:text-slate-400">Supported Formats</div>
            <div className="mt-0.5 text-xs font-bold text-slate-800 dark:text-slate-100">NTFS, FAT32, exFAT</div>
          </div>
          <div className="rounded-lg bg-white/70 p-2.5 text-center shadow-xs dark:bg-slate-800/50">
            <div className="text-xs font-medium text-slate-500 dark:text-slate-400">Safety Policy</div>
            <div className="mt-0.5 text-xs font-bold text-emerald-600 dark:text-emerald-400">Read-Only Disk Access</div>
          </div>
        </div>
      </div>

      {/* =========================================================================
          SECTION 2: LIVE SETUP VERIFICATION WIDGET
      ========================================================================= */}
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-slate-200 pb-4 dark:border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold text-slate-900 dark:text-white">
                Live Setup Verification
              </h2>
              {status.connected ? (
                <span className="flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 dark:bg-emerald-950/70 dark:text-emerald-300 dark:border dark:border-emerald-800/60">
                  <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" /> Connected
                </span>
              ) : (
                <span className="flex items-center gap-1.5 rounded-full bg-rose-50 px-2.5 py-0.5 text-xs font-semibold text-rose-700 dark:bg-rose-950/70 dark:text-rose-300 dark:border dark:border-rose-800/60">
                  <span className="h-2 w-2 rounded-full bg-rose-500" /> Offline / Not Detected
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              Tests direct connection from your browser to <code className="font-mono text-slate-700 dark:text-slate-300">http://127.0.0.1:8000</code>.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={testConnection}
              disabled={testing}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-xs hover:bg-slate-50 active:bg-slate-100 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {testing ? <Spinner className="h-3.5 w-3.5" /> : <Icon name="bolt" className="h-3.5 w-3.5" />}
              {testing ? 'Testing Connection...' : 'Test Connection Now'}
            </button>
            {status.connected && (
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-bold text-white shadow-xs hover:bg-emerald-700"
              >
                Go to Recover <Icon name="check" className="h-3.5 w-3.5" />
              </Link>
            )}
          </div>
        </div>

        {/* Diagnostic Checklist */}
        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {/* Diagnostic Item 1 */}
          <div className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 dark:border-slate-800 dark:bg-slate-850">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-600 dark:text-slate-400">Agent API Service</span>
              {status.connected ? (
                <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">Online</span>
              ) : (
                <span className="text-xs font-medium text-slate-400">Offline</span>
              )}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-800 dark:text-slate-200">
              {status.connected ? '127.0.0.1:8000' : 'No response'}
            </div>
          </div>

          {/* Diagnostic Item 2 */}
          <div className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 dark:border-slate-800 dark:bg-slate-850">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-600 dark:text-slate-400">Administrator Rights</span>
              {status.system?.elevated ? (
                <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">Elevated</span>
              ) : status.connected ? (
                <span className="text-xs font-bold text-amber-500">Standard</span>
              ) : (
                <span className="text-xs font-medium text-slate-400">Unknown</span>
              )}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-800 dark:text-slate-200">
              {status.system?.elevation_type || (status.connected ? 'Enabled' : 'Run as Admin')}
            </div>
          </div>

          {/* Diagnostic Item 3 */}
          <div className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 dark:border-slate-800 dark:bg-slate-850">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-600 dark:text-slate-400">C++ Engine</span>
              {status.health?.engine?.available ? (
                <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">Ready</span>
              ) : status.connected ? (
                <span className="text-xs font-bold text-amber-500">Simulated</span>
              ) : (
                <span className="text-xs font-medium text-slate-400">Waiting</span>
              )}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-800 dark:text-slate-200">
              {status.health?.engine?.version || (status.connected ? 'v1.0.0' : 'Not Loaded')}
            </div>
          </div>

          {/* Diagnostic Item 4 */}
          <div className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3.5 dark:border-slate-800 dark:bg-slate-850">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-600 dark:text-slate-400">SQLite Database</span>
              {status.health?.database?.ok ? (
                <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">Active</span>
              ) : status.connected ? (
                <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400">Ready</span>
              ) : (
                <span className="text-xs font-medium text-slate-400">Waiting</span>
              )}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-800 dark:text-slate-200">
              {status.connected ? 'recovery.db' : 'Pending start'}
            </div>
          </div>
        </div>

        {/* Custom Address Input & Troubleshooting */}
        <div className="mt-4 pt-3 border-t border-slate-200/80 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
          <div className="flex items-center gap-2 flex-1">
            <span className="font-medium text-slate-500 dark:text-slate-400 shrink-0">Agent Address:</span>
            <input
              type="text"
              value={agentUrl}
              onChange={(e) => setAgentUrl(e.target.value)}
              placeholder="http://127.0.0.1:8000"
              className="input py-1 px-2.5 text-xs font-mono max-w-sm flex-1 dark:bg-slate-800"
            />
            <button
              type="button"
              onClick={() => testConnection(agentUrl)}
              disabled={testing}
              className="btn-primary py-1 px-3 text-xs shrink-0"
            >
              {testing ? 'Testing...' : 'Connect'}
            </button>
          </div>
          <span className="text-[11px] text-slate-500 dark:text-slate-400">
            Default: <code className="font-mono">http://127.0.0.1:8000</code> or <code className="font-mono">http://localhost:8000</code>
          </span>
        </div>

        {/* Status Message / Guidance */}
        {status.connected ? (
          <div className="mt-4 rounded-xl bg-emerald-50/80 p-4 border border-emerald-200 text-xs text-emerald-900 dark:bg-emerald-950/40 dark:border-emerald-800/60 dark:text-emerald-200">
            <div className="flex items-center gap-2 font-bold text-sm">
              <svg className="w-4 h-4 text-emerald-600 dark:text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              Setup Complete! The Recovery Agent is connected and active.
            </div>
            <p className="mt-1 text-emerald-800 dark:text-emerald-300">
              Connected to <strong>{status.activeUrl}</strong>. Your browser is actively communicating with the local engine on this PC. You can now perform quick scans, deep file carving, and restore deleted data with full hardware speeds.
            </p>
          </div>
        ) : (
          <div className="mt-4 rounded-xl bg-slate-100 p-4 border border-slate-200 text-xs text-slate-700 dark:bg-slate-800/70 dark:border-slate-700 dark:text-slate-300 space-y-2">
            <div className="flex items-center gap-2 font-semibold text-slate-900 dark:text-slate-100">
              <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Agent Not Detected at {status.activeUrl || DEFAULT_AGENT_URL}
            </div>
            <p className="text-slate-600 dark:text-slate-300">
              If the terminal is already running, here are the 3 common reasons for no detection:
            </p>
            <ul className="list-disc list-inside space-y-1 text-slate-600 dark:text-slate-300 text-[11.5px] pl-1">
              <li>
                <strong>Browser Local Network Permission:</strong> If visiting from a hosted site (HTTPS), your browser asks to connect to local devices. Click the <strong>lock / site settings icon</strong> next to the URL bar and ensure <em>&quot;Insecure content&quot;</em> or <em>&quot;Local network access&quot;</em> is set to <strong>Allow</strong>.
              </li>
              <li>
                <strong>Check Terminal Status:</strong> Ensure the command prompt window remains open and shows: <code className="font-mono bg-slate-200 dark:bg-slate-900 px-1 py-0.5 rounded text-[11px]">Uvicorn running on http://127.0.0.1:8000</code>.
              </li>
              <li>
                <strong>Ad-Blockers / Brave Shields:</strong> In Brave or extensions like uBlock, disable Shields for localhost connections or test by clicking the <strong>Connect</strong> button with <code className="font-mono">http://localhost:8000</code>.
              </li>
            </ul>
          </div>
        )}
      </div>

      {/* =========================================================================
          SECTION 3: STEP-BY-STEP SETUP INSTRUCTIONS
      ========================================================================= */}
      <div className="space-y-6">
        <div>
          <h2 className="text-xl font-bold text-slate-900 dark:text-white">
            Setup Instructions & Terminal Commands
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Choose either the 1-click batch launcher or run via terminal commands in Windows PowerShell.
          </p>
        </div>

        {/* Step Cards List */}
        <div className="space-y-4">
          {/* Step 1 */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-start gap-4">
              <span className="flex h-7 w-7 flex-none items-center justify-center rounded-lg bg-brand-600 text-xs font-bold text-white">
                1
              </span>
              <div className="space-y-2 flex-1">
                <h3 className="font-bold text-slate-900 dark:text-white">
                  Install Python 3.10 or newer (One-time)
                </h3>
                <p className="text-sm text-slate-600 dark:text-slate-300">
                  The agent service is built with Python and FastAPI. If you don&apos;t already have Python installed:
                </p>
                <div className="flex flex-wrap items-center gap-3 pt-1">
                  <a
                    href="https://www.python.org/downloads/"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    Download from Python.org <Icon name="download" className="h-3 w-3" />
                  </a>
                  <span className="text-xs text-amber-700 dark:text-amber-400 font-medium">
                    ⚠️ Critical: Check the box <strong>&quot;Add python.exe to PATH&quot;</strong> on the first installer screen.
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Step 2 */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-start gap-4">
              <span className="flex h-7 w-7 flex-none items-center justify-center rounded-lg bg-brand-600 text-xs font-bold text-white">
                2
              </span>
              <div className="space-y-2 flex-1">
                <h3 className="font-bold text-slate-900 dark:text-white">
                  Download and Extract the Package
                </h3>
                <p className="text-sm text-slate-600 dark:text-slate-300">
                  Download <a href={AGENT_ZIP} download="recovery-agent.zip" className="font-semibold text-brand-600 underline hover:text-brand-700 dark:text-indigo-400">recovery-agent.zip</a>,
                  right-click it, select <strong>Extract All...</strong>, and extract it to a directory such as <code className="font-mono text-xs bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded">C:\RecoveryAgent</code>.
                </p>
              </div>
            </div>
          </div>

          {/* Step 3: Two methods */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-start gap-4">
              <span className="flex h-7 w-7 flex-none items-center justify-center rounded-lg bg-brand-600 text-xs font-bold text-white">
                3
              </span>
              <div className="space-y-4 flex-1">
                <div>
                  <h3 className="font-bold text-slate-900 dark:text-white">
                    Start the Recovery Agent (Two Options)
                  </h3>
                  <p className="text-sm text-slate-600 dark:text-slate-300 mt-1">
                    Administrator permissions are strictly required so the engine can acquire a raw handle to disk partitions and MFT tables.
                  </p>
                </div>

                {/* Option A: 1-Click Batch */}
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-850">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-brand-100 px-2 py-0.5 text-[11px] font-bold text-brand-800 dark:bg-brand-950 dark:text-brand-300">
                      Option A (Recommended)
                    </span>
                    <span className="font-semibold text-sm text-slate-900 dark:text-white">One-Click Launcher</span>
                  </div>
                  <ol className="mt-2 list-decimal list-inside space-y-1.5 text-xs text-slate-600 dark:text-slate-300">
                    <li>Open your extracted <code className="font-mono">C:\RecoveryAgent</code> folder.</li>
                    <li>Right-click <strong className="text-slate-900 dark:text-white font-mono">start-agent.bat</strong> and select <strong>&quot;Run as administrator&quot;</strong>.</li>
                    <li>Click <strong>Yes</strong> when Windows asks for UAC rights.</li>
                    <li>The command prompt will automatically install required libraries and show:
                      <div className="mt-1.5 rounded bg-slate-900 p-2 font-mono text-[11px] text-emerald-400">
                        INFO: Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
                      </div>
                    </li>
                  </ol>
                </div>

                {/* Option B: Terminal Commands */}
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-850">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-slate-200 px-2 py-0.5 text-[11px] font-bold text-slate-700 dark:bg-slate-700 dark:text-slate-200">
                        Option B
                      </span>
                      <span className="font-semibold text-sm text-slate-900 dark:text-white">Manual Terminal Commands (PowerShell)</span>
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    Open Windows PowerShell or CMD as Administrator, then run:
                  </p>

                  <div className="mt-2 space-y-2">
                    {/* Command Block 1 */}
                    <div className="relative rounded-lg bg-slate-900 p-3 text-xs font-mono text-slate-100">
                      <div className="flex items-center justify-between pb-1 border-b border-slate-800 text-[11px] text-slate-400">
                        <span># 1. Navigate & Install Dependencies</span>
                        <button
                          type="button"
                          onClick={() => copyToClipboard('cd C:\\RecoveryAgent\npip install -r backend\\requirements.txt', 'cmd1')}
                          className="hover:text-white flex items-center gap-1 font-sans"
                        >
                          {copiedCmd === 'cmd1' ? '✓ Copied' : 'Copy'}
                        </button>
                      </div>
                      <pre className="mt-2 text-slate-200">
cd C:\RecoveryAgent{'\n'}pip install -r backend\requirements.txt
                      </pre>
                    </div>

                    {/* Command Block 2 */}
                    <div className="relative rounded-lg bg-slate-900 p-3 text-xs font-mono text-slate-100">
                      <div className="flex items-center justify-between pb-1 border-b border-slate-800 text-[11px] text-slate-400">
                        <span># 2. Launch Local Recovery Service</span>
                        <button
                          type="button"
                          onClick={() => copyToClipboard('python backend\\recovery_service.py', 'cmd2')}
                          className="hover:text-white flex items-center gap-1 font-sans"
                        >
                          {copiedCmd === 'cmd2' ? '✓ Copied' : 'Copy'}
                        </button>
                      </div>
                      <pre className="mt-2 text-emerald-400">
python backend\recovery_service.py
                      </pre>
                    </div>

                    {/* Command Block 3 (Verification) */}
                    <div className="relative rounded-lg bg-slate-900 p-3 text-xs font-mono text-slate-100">
                      <div className="flex items-center justify-between pb-1 border-b border-slate-800 text-[11px] text-slate-400">
                        <span># 3. Verify in Terminal (Optional)</span>
                        <button
                          type="button"
                          onClick={() => copyToClipboard('curl http://127.0.0.1:8000/api/health', 'cmd3')}
                          className="hover:text-white flex items-center gap-1 font-sans"
                        >
                          {copiedCmd === 'cmd3' ? '✓ Copied' : 'Copy'}
                        </button>
                      </div>
                      <pre className="mt-2 text-slate-300">
curl http://127.0.0.1:8000/api/health
                      </pre>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Step 4 */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-start gap-4">
              <span className="flex h-7 w-7 flex-none items-center justify-center rounded-lg bg-brand-600 text-xs font-bold text-white">
                4
              </span>
              <div className="space-y-2 flex-1">
                <h3 className="font-bold text-slate-900 dark:text-white">
                  Allow Browser Local Network Access
                </h3>
                <p className="text-sm text-slate-600 dark:text-slate-300">
                  When you click <strong>&quot;Test Connection Now&quot;</strong> or open the Recovery Dashboard, modern Chromium browsers (Chrome, Edge, Brave) may display a dialog asking:
                </p>
                <div className="rounded-lg bg-slate-100 p-3 font-medium text-xs text-slate-800 dark:bg-slate-800 dark:text-slate-200 border border-slate-200 dark:border-slate-700">
                  <em>&ldquo;{typeof window !== 'undefined' ? window.location.hostname : 'This site'} wants to connect to devices on your local network.&rdquo;</em> → Click <strong>Allow</strong>.
                </div>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  This standard browser security permission lets this web application issue commands to your locally running recovery engine.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Safety & Best Practices */}
      <Alert tone="warning" title="Important Safety Rules for File Recovery">
        <ul className="list-disc list-inside space-y-1 mt-1 text-xs">
          <li><strong>Never restore to the scanned drive:</strong> Always select a separate external drive, secondary partition, or USB flash drive as your destination to prevent overwriting deleted sectors.</li>
          <li><strong>Stop writing data to the drive:</strong> Do not install new software, download files, or stream videos onto the drive you wish to recover from.</li>
          <li><strong>Keep the terminal window open:</strong> The local agent must remain running in the background while you are performing scans or file restorations.</li>
        </ul>
      </Alert>
    </div>
  );
}

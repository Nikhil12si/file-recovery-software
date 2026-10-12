'use client';
/**
 * UserManual.jsx - Track C, Module 3: Integrated User Manual & Guide (/manual).
 *
 * A self-contained documentation hub for non-technical users:
 *   - Getting started (elevated backend start-up) with a live "check my setup" widget
 *   - Raw disk access permission instructions and error explanations
 *   - Safety rules (why the destination must be a physically separate drive)
 *   - Scan scenario recommendations + interactive scenario helper
 *   - Recovery confidence guide + interactive score explainer (mirrors the backend scorer)
 *   - Restoring, reports, troubleshooting FAQ (searchable) and glossary
 */
import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { Alert, Badge, ConfidenceBadge, Icon, Spinner, classNames } from './ui';

const SECTIONS = [
  { id: 'how-it-works', title: 'How recovery works' },
  { id: 'getting-started', title: 'Getting started' },
  { id: 'permissions', title: 'Administrator & raw disk access' },
  { id: 'safety', title: 'Safety rules' },
  { id: 'scenarios', title: 'Quick Scan or Deep Scan?' },
  { id: 'targets', title: 'Choosing what to scan' },
  { id: 'confidence', title: 'Recovery confidence ratings' },
  { id: 'restore', title: 'Restoring files' },
  { id: 'reports', title: 'Reports & history' },
  { id: 'faq', title: 'Troubleshooting & FAQ' },
  { id: 'glossary', title: 'Glossary' },
];

// ============================================================================= helpers
function CodeBlock({ children, label }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(children).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }).catch(() => {});
    }
  };
  return (
    <div className="relative mt-3">
      {label && <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>}
      <pre><code>{children}</code></pre>
      <button type="button" onClick={copy} className="absolute right-2 top-2 rounded bg-slate-700 px-2 py-0.5 text-xs text-slate-200 hover:bg-slate-600"
        style={label ? { top: '1.75rem' } : undefined}>{copied ? 'Copied' : 'Copy'}</button>
    </div>
  );
}

function Step({ n, title, children }) {
  return (
    <div className="relative mt-5 flex gap-4">
      <div className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-brand-600 text-sm font-bold text-white">{n}</div>
      <div className="min-w-0 flex-1">
        <h4 className="font-semibold text-slate-900">{title}</h4>
        <div className="text-sm leading-6 text-slate-700">{children}</div>
      </div>
    </div>
  );
}

function Section({ id, title, icon, children }) {
  return (
    <section id={id} className="card scroll-mt-20 p-6 prose-manual">
      <h2 className="flex items-center gap-2"><span className="rounded-lg bg-brand-50 p-1.5 text-brand-600"><Icon name={icon} className="h-5 w-5" /></span>{title}</h2>
      {children}
    </section>
  );
}

// ============================================================================= live setup check
function SetupCheck() {
  const [state, setState] = useState({ loading: false, health: null, status: null, error: null });
  const run = async () => {
    setState({ loading: true, health: null, status: null, error: null });
    try {
      const health = await api.health();
      let status = null;
      try { status = await api.systemStatus(); } catch { /* requires sign-in */ }
      setState({ loading: false, health, status, error: null });
    } catch (e) {
      setState({ loading: false, health: null, status: null, error: e.message });
    }
  };
  const item = (ok, text, hint) => (
    <li className="flex items-start gap-2">
      <span className={classNames('mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full', ok === null ? 'bg-slate-200 text-slate-500' : ok ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700')}>
        <Icon name={ok ? 'check' : ok === null ? 'info' : 'x'} className="h-3 w-3" />
      </span>
      <span>{text}{hint && <span className="block text-xs text-slate-500">{hint}</span>}</span>
    </li>
  );
  const h = state.health;
  const s = state.status;
  return (
    <div className="mt-4 rounded-xl border border-brand-100 bg-brand-50/50 p-4 not-prose">
      <div className="flex items-center justify-between">
        <div className="font-semibold text-slate-900">Check my setup</div>
        <button type="button" className="btn-primary px-3 py-1.5" onClick={run} disabled={state.loading}>{state.loading && <Spinner />} Run check</button>
      </div>
      {state.error && <div className="mt-3"><Alert tone="danger" title="Recovery service not reachable">{state.error} Start it as described in step 3.</Alert></div>}
      {h && (
        <ul className="mt-3 space-y-2 text-sm">
          {item(true, 'Recovery service is running', `version ${h.version}`)}
          {item(h.database.ok, `Database connected (${h.database.dialect})`, h.database.dialect === 'sqlite' ? 'SQLite fallback - set DATABASE_URL to use PostgreSQL' : null)}
          {item(h.engine.available, h.engine.mock ? 'Engine: simulation mode (no real disks are read)' : `Recovery engine found (${h.engine.version || 'unknown version'})`,
            h.engine.mock ? 'Build win_recovery_engine.exe and set ENGINE_PATH to scan real drives' : null)}
          {s ? item(s.elevated, s.elevated ? 'Running as Administrator' : 'NOT running as Administrator', s.elevated ? null : 'Restart the backend from an elevated terminal (see next section)')
            : item(null, 'Administrator check', 'Sign in to check elevation')}
        </ul>
      )}
    </div>
  );
}

// ============================================================================= scenario helper
const QUESTIONS = [
  { id: 'formatted', q: 'Was the drive formatted, re-partitioned, or does Windows say "You need to format the disk"?' },
  { id: 'opens', q: 'Does the drive still open normally in File Explorer?' },
  { id: 'names', q: 'Are original file names and folders important to you?' },
];

function ScenarioHelper() {
  const [a, setA] = useState({});
  const done = QUESTIONS.every((x) => a[x.id] !== undefined);
  let rec = null;
  if (done) {
    if (a.formatted || !a.opens) rec = { mode: 'Deep Scan', why: 'The file table is gone or damaged, so files must be found by their content signatures. Scan the whole physical disk or the partition without a drive letter.' };
    else if (a.names) rec = { mode: 'Quick Scan first', why: 'The Master File Table is intact, so Quick Scan restores original names, folders and dates in minutes. Run a Deep Scan afterwards only if files are missing.' };
    else rec = { mode: 'Quick Scan, then Deep Scan', why: 'Start fast; a Deep Scan can find older files whose MFT records were already reused.' };
  }
  return (
    <div className="mt-4 rounded-xl border border-slate-200 p-4 not-prose">
      <div className="font-semibold text-slate-900">Scenario helper</div>
      <div className="mt-3 space-y-3">
        {QUESTIONS.map((x) => (
          <div key={x.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="text-slate-700">{x.q}</span>
            <div className="flex gap-1">
              {[true, false].map((v) => (
                <button key={String(v)} type="button" onClick={() => setA({ ...a, [x.id]: v })}
                  className={classNames('rounded-lg border px-3 py-1 text-xs font-semibold', a[x.id] === v ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-600')}>
                  {v ? 'Yes' : 'No'}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      {rec && (
        <div className="mt-4"><Alert tone="success" title={`Recommendation: ${rec.mode}`}>{rec.why} <Link href="/dashboard" className="font-semibold underline">Start now</Link></Alert></div>
      )}
    </div>
  );
}

// ============================================================================= score explainer
// Mirrors backend/app/analysis.py ConfidenceScorer so users can see how ratings arise.
function ScoreExplainer() {
  const [method, setMethod] = useState('mft');
  const [header, setHeader] = useState('match');
  const [overwrite, setOverwrite] = useState(0);
  const [footer, setFooter] = useState(true);
  const [estimated, setEstimated] = useState(false);
  const [decodes, setDecodes] = useState('yes');

  const { score, level, reasons } = useMemo(() => {
    let s = 100;
    const r = [];
    if (header === 'mismatch') { s -= 45; r.push('Header does not match the file type (-45)'); }
    if (method !== 'carve' && overwrite > 0) { const d = Math.min(60, Math.round(overwrite * 0.7)); s -= d; r.push(`${overwrite}% of clusters reused (-${d})`); }
    if (method === 'carve') {
      s -= 5; r.push('Carved: name and folder lost (-5)');
      if (!footer) { s -= 30; r.push('No end-of-file marker (-30)'); }
      if (estimated) { s -= 20; r.push('Size estimated (-20)'); }
    }
    if (method === 'fat') { s -= 12; r.push('FAT chain cleared, contiguity assumed (-12)'); }
    if (decodes === 'yes') { s += 10; r.push('Content decodes completely (+10)'); }
    if (decodes === 'no') { s -= 35; r.push('Content fails to decode (-35)'); }
    s = Math.max(0, Math.min(100, s));
    return { score: s, level: s >= 75 ? 'high' : s >= 45 ? 'medium' : 'low', reasons: r };
  }, [method, header, overwrite, footer, estimated, decodes]);

  const Seg = ({ value, set, options }) => (
    <div className="flex gap-1">
      {options.map(([v, l]) => (
        <button key={v} type="button" onClick={() => set(v)}
          className={classNames('rounded-lg border px-2.5 py-1 text-xs font-medium', value === v ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-600')}>{l}</button>
      ))}
    </div>
  );

  return (
    <div className="mt-4 grid gap-4 rounded-xl border border-slate-200 p-4 md:grid-cols-2 not-prose">
      <div className="space-y-3 text-sm">
        <div className="font-semibold text-slate-900">Try it: how is a score calculated?</div>
        <div><div className="mb-1 text-xs text-slate-500">Found by</div><Seg value={method} set={setMethod} options={[['mft', 'NTFS MFT'], ['fat', 'FAT32'], ['carve', 'Carving']]} /></div>
        <div><div className="mb-1 text-xs text-slate-500">File header</div><Seg value={header} set={setHeader} options={[['match', 'Intact'], ['mismatch', 'Overwritten']]} /></div>
        {method !== 'carve' && (
          <div>
            <div className="mb-1 text-xs text-slate-500">Clusters reused by other files: {overwrite}%</div>
            <input type="range" min="0" max="100" step="5" value={overwrite} onChange={(e) => setOverwrite(Number(e.target.value))} className="w-full" />
          </div>
        )}
        {method === 'carve' && (
          <div className="flex gap-4">
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={footer} onChange={(e) => setFooter(e.target.checked)} /> End marker found</label>
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={estimated} onChange={(e) => setEstimated(e.target.checked)} /> Size estimated</label>
          </div>
        )}
        <div><div className="mb-1 text-xs text-slate-500">Preview decodes</div><Seg value={decodes} set={setDecodes} options={[['yes', 'Yes'], ['unknown', 'Not checked'], ['no', 'No']]} /></div>
      </div>
      <div className="rounded-lg bg-slate-50 p-4">
        <div className="text-xs uppercase tracking-wide text-slate-500">Result</div>
        <div className="mt-1 flex items-center gap-3"><span className="text-4xl font-bold text-slate-900">{score}</span><ConfidenceBadge level={level} /></div>
        <ul className="mt-3 space-y-1 text-xs text-slate-600">
          <li>Start at 100</li>
          {reasons.map((r) => <li key={r}>• {r}</li>)}
        </ul>
      </div>
    </div>
  );
}

// ============================================================================= FAQ
const FAQ = [
  ['The scan fails with "Administrator rights are required"', 'The backend opens \\\\.\\D: and \\\\.\\PhysicalDriveN directly, which Windows only allows for elevated processes. Stop the backend and start it again from "Terminal (Admin)". See Administrator & raw disk access.'],
  ['"Access is denied" even though I am an administrator', 'You are probably running a non-elevated terminal (UAC gives administrators a limited token by default). The title bar of an elevated window starts with "Administrator:". Security software may also block raw disk reads - allow win_recovery_engine.exe.'],
  ['My drive is not listed', 'Only fixed and removable disks are shown; network drives and optical drives are excluded. Click Refresh after connecting the drive. A drive whose partition was deleted appears as "Partition N - No drive letter" under its physical disk.'],
  ['The drive is BitLocker-encrypted', 'Unlock it in File Explorer first. Quick Scan reads the decrypted volume (D:). Deep Scan of the physical disk sees only ciphertext, so scan the unlocked volume instead.'],
  ['Quick Scan found nothing', 'The MFT records may already be reused, or the drive was formatted. Run a Deep Scan of the same drive. For FAT32 USB sticks, Quick Scan only finds files whose directory entries survive.'],
  ['Restored photos are grey or half-broken', 'Their clusters were partly overwritten or the file was fragmented (carving assumes contiguous data). Check the confidence reasons; Low-rated files often cannot be repaired.'],
  ['Why can I not restore to the same drive?', 'Writing anything to the source can overwrite the deleted data still waiting to be recovered. The destination guard compares volume GUIDs and physical disk numbers and blocks unsafe targets.'],
  ['The Deep Scan is very slow', 'Speed is limited by the disk (typically 80-180 MB/s on HDDs; a 2 TB disk takes about 3-6 hours). Unreadable sectors slow it down a lot - if read errors appear, create a sector image of the failing drive first.'],
  ['Can I scan an SSD?', 'The tool is designed for HDDs. SSDs use TRIM, which erases deleted blocks within seconds or minutes, so recovery usually finds nothing. You can force a scan with the "scan anyway" option.'],
  ['What is the _recovery_log_*.json file?', 'A log written next to the restored files listing each file, where it came from, its size and its SHA-256 checksum, so you can verify integrity later.'],
];

function FaqList({ filter }) {
  const [open, setOpen] = useState(null);
  const items = FAQ.filter(([q, a]) => !filter || `${q} ${a}`.toLowerCase().includes(filter.toLowerCase()));
  if (!items.length) return <p>No FAQ entry matches “{filter}”.</p>;
  return (
    <div className="mt-3 divide-y divide-slate-200 rounded-lg border border-slate-200 not-prose">
      {items.map(([q, a], i) => (
        <div key={q}>
          <button type="button" className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-slate-900 hover:bg-slate-50"
            onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>
            {q}<span className="ml-3 text-slate-400">{open === i ? '−' : '+'}</span>
          </button>
          {open === i && <div className="px-4 pb-4 text-sm leading-6 text-slate-600">{a}</div>}
        </div>
      ))}
    </div>
  );
}

// ============================================================================= page
export default function UserManual() {
  const [active, setActive] = useState(SECTIONS[0].id);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    const obs = new IntersectionObserver((entries) => {
      const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) setActive(visible[0].target.id);
    }, { rootMargin: '-80px 0px -60% 0px' });
    SECTIONS.forEach((s) => { const el = document.getElementById(s.id); if (el) obs.observe(el); });
    return () => obs.disconnect();
  }, []);

  return (
    <div className="grid gap-6 lg:grid-cols-[240px_1fr]">
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <div className="card p-3">
          <div className="px-2 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">User manual</div>
          <nav className="space-y-0.5">
            {SECTIONS.map((s) => (
              <a key={s.id} href={`#${s.id}`}
                className={classNames('block rounded-md px-2 py-1.5 text-sm', active === s.id ? 'bg-brand-50 font-medium text-brand-700' : 'text-slate-600 hover:bg-slate-100')}>
                {s.title}
              </a>
            ))}
          </nav>
        </div>
        <div className="card mt-3 p-3 text-xs text-slate-600">
          <div className="font-semibold text-red-700">Emergency checklist</div>
          <ol className="mt-1 list-decimal space-y-0.5 pl-4">
            <li>Stop using the drive</li><li>Do not install anything on it</li><li>Have a second drive ready</li><li>Run Quick Scan first</li>
          </ol>
        </div>
      </aside>

      <div className="min-w-0 space-y-6">
        <div className="card bg-gradient-to-br from-brand-600 to-brand-900 p-8 text-white">
          <Badge tone="purple">Guide</Badge>
          <h1 className="mt-3 text-3xl font-bold">Recover deleted files safely</h1>
          <p className="mt-2 max-w-2xl text-brand-100">
            A plain-language guide to getting your photos and documents back from Windows hard drives - without making things worse.
            Read the <a href="#safety" className="font-semibold text-white underline">safety rules</a> before your first scan.
          </p>
        </div>

        <Section id="how-it-works" title="How recovery works" icon="info">
          <p>When you delete a file, Windows does not erase its contents. It only marks the space as free and flags the file&apos;s record as unused. The data stays on the disk until something new is written over it - which is why acting quickly matters.</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3 not-prose">
            {[['1. Deleted', 'The NTFS Master File Table record is flagged "not in use". Name, folder and cluster locations remain.'],
              ['2. Still recoverable', 'Until Windows reuses those clusters, the original bytes can be copied out exactly.'],
              ['3. Overwritten', 'New files take over the clusters. Overwritten parts are gone for good; the scan rates this risk.']].map(([t, d], i) => (
              <div key={t} className={classNames('rounded-lg border p-3 text-sm', i === 2 ? 'border-red-200 bg-red-50' : i === 1 ? 'border-emerald-200 bg-emerald-50' : 'border-slate-200 bg-slate-50')}>
                <div className="font-semibold">{t}</div><div className="mt-1 text-slate-600">{d}</div>
              </div>
            ))}
          </div>
          <p>The application has three parts: a <strong>recovery engine</strong> (reads the disk, read-only), a <strong>recovery service</strong> (analyses and scores results, generates reports) and this <strong>web interface</strong>.</p>
        </Section>

        <Section id="getting-started" title="Getting started" icon="bolt">
          <p>You need Windows 10 or 11 (64-bit), an administrator account, and a second drive (for example, a USB disk) to store the recovered files.</p>
          <Step n={1} title="Install the components (once)">
            Install Python 3.10+, Node.js 18+ and optionally PostgreSQL 13+. Build or copy <code>win_recovery_engine.exe</code> to <code>engine\build\Release\</code>.
            Ideally, install the application on a drive you are <em>not</em> recovering from.
          </Step>
          <Step n={2} title="Create the database (optional but recommended)">
            <CodeBlock label="PowerShell">{'psql -U postgres -c "CREATE DATABASE file_recovery"\npsql -U postgres -d file_recovery -f database\\schema.sql'}</CodeBlock>
            Without PostgreSQL the service falls back to a local SQLite file automatically.
          </Step>
          <Step n={3} title="Start the recovery service as Administrator">
            Press <kbd>Win</kbd> + <kbd>X</kbd> and choose <strong>Terminal (Admin)</strong> (or <em>Windows PowerShell (Admin)</em>). Confirm the UAC prompt, then run:
            <CodeBlock label="Elevated PowerShell">{'cd C:\\FileRecovery\\backend\npython -m venv .venv\n.venv\\Scripts\\Activate.ps1\npip install -r requirements.txt\n$env:DATABASE_URL = "postgresql://postgres:secret@localhost:5432/file_recovery"\npython recovery_service.py'}</CodeBlock>
            Leave this window open. The service listens on <code>http://127.0.0.1:8000</code> (this computer only).
          </Step>
          <Step n={4} title="Start the web interface">
            <CodeBlock label="Normal terminal">{'cd C:\\FileRecovery\\web\nnpm install\nnpm run build\nnpm start'}</CodeBlock>
            Open <code>http://localhost:3000</code>, create your account (the first account becomes administrator) and run the setup check below.
          </Step>
          <SetupCheck />
        </Section>

        <Section id="permissions" title="Administrator & raw disk access" icon="shield">
          <p>To find deleted files, the engine reads the disk directly, sector by sector, through device paths such as <code>\\.\D:</code> (a volume) or <code>\\.\PhysicalDrive1</code> (a whole disk). Windows only allows this for <strong>elevated</strong> processes, even if your account is an administrator.</p>
          <h3>How to start an elevated terminal</h3>
          <ul>
            <li><strong>Start menu:</strong> type <em>PowerShell</em>, right-click it, then choose <em>Run as administrator</em>.</li>
            <li><strong>Quick menu:</strong> press <kbd>Win</kbd> + <kbd>X</kbd>, then choose <em>Terminal (Admin)</em>.</li>
            <li><strong>Shortcut:</strong> right-click your shortcut, then open <em>Properties &gt; Advanced</em> and tick <em>Run as administrator</em>.</li>
          </ul>
          <h3>How to check it worked</h3>
          <p>The window title begins with <strong>Administrator:</strong>. You can also run:</p>
          <CodeBlock>{'whoami /groups | findstr S-1-16-12288\n# A line containing "High Mandatory Level" means the terminal is elevated'}</CodeBlock>
          <p>The Recover page also shows a red banner when the service is not elevated.</p>
          <h3>What the engine does with these rights</h3>
          <ul>
            <li>It opens source drives with <strong>read-only</strong> access (<code>GENERIC_READ</code>) and unbuffered I/O, and never writes to them.</li>
            <li>It writes only to the destination folder you choose, after the destination guard has checked it.</li>
            <li>The service listens on <code>127.0.0.1</code> only, so other computers cannot reach it.</li>
          </ul>
          <Alert tone="warning" title="Security software">Some antivirus products block raw disk reads. If scans fail with &quot;Access is denied&quot; while elevated, add an exception for <code>win_recovery_engine.exe</code>.</Alert>
        </Section>

        <Section id="safety" title="Safety rules" icon="warning">
          <ol>
            <li><strong>Stop using the drive immediately.</strong> Every write (browser cache, downloads, Windows updates) can land on top of your deleted files.</li>
            <li><strong>Never install recovery software on the affected drive.</strong></li>
            <li><strong>Always restore to a physically separate drive.</strong> The tool blocks the same volume and the same physical disk during whole-disk scans, and warns about other partitions of the same disk.</li>
            <li><strong>Scanning the Windows (C:) drive?</strong> If possible, shut down and attach the disk to another computer as a secondary drive.</li>
            <li><strong>Failing drive</strong> (clicking noises, read errors in the scan)? Restore the most important files first, or have a professional create a sector image.</li>
            <li><strong>Do not run CHKDSK or &quot;repair&quot; tools before recovering.</strong> They may &quot;fix&quot; the file system by discarding the records you need.</li>
          </ol>
          <h3>Why a different physical drive?</h3>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 not-prose">
            <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm">
              <div className="font-semibold text-red-800">Restoring to D: while recovering D:</div>
              <p className="mt-1 text-red-900">Windows puts the restored file in the first free clusters - possibly the clusters of the next file you still want to recover. Each restore can destroy another file.</p>
            </div>
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm">
              <div className="font-semibold text-emerald-800">Restoring to a USB disk (E:)</div>
              <p className="mt-1 text-emerald-900">The source disk is only read. Every file can be recovered, in any order, as often as needed.</p>
            </div>
          </div>
        </Section>

        <Section id="scenarios" title="Quick Scan or Deep Scan?" icon="search">
          <div className="mt-4 overflow-x-auto not-prose">
            <table className="min-w-full text-sm">
              <thead><tr className="border-b border-slate-200 text-left text-slate-500"><th className="py-2 pr-4" /><th className="py-2 pr-4">Quick Scan (MFT)</th><th className="py-2">Deep Scan (carving)</th></tr></thead>
              <tbody className="divide-y divide-slate-100 text-slate-700">
                {[['How it works', 'Reads the NTFS Master File Table / FAT32 directories and lists entries marked as deleted', 'Reads every sector and recognises files by their signatures (e.g. JPEG starts with FF D8 FF)'],
                  ['Duration', 'Seconds to minutes', 'About 2-4 hours per TB on a healthy HDD'],
                  ['File names & folders', 'Kept', 'Lost - rebuilt from content (smart names)'],
                  ['After formatting', 'Usually nothing', 'Yes'],
                  ['Corrupted / RAW partition', 'Only if the MFT is readable', 'Yes'],
                  ['Fragmented files', 'Fully supported (data runs)', 'May be incomplete'],
                  ['Supported types', 'All file types', 'JPG, PNG, GIF, BMP, PDF, DOCX/XLSX/PPTX, ODF, ZIP, RAR, 7Z, DOC/XLS/PPT, MP4/MOV, WAV/AVI, SQLite']].map(([k, a, b]) => (
                  <tr key={k}><td className="py-2 pr-4 font-medium">{k}</td><td className="py-2 pr-4">{a}</td><td className="py-2">{b}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Recommendations by scenario</h3>
          <ul>
            <li><strong>Accidental delete / emptied Recycle Bin:</strong> Quick Scan of the volume (e.g. D:). Results usually have High confidence.</li>
            <li><strong>Formatted drive:</strong> Deep Scan of the volume or the whole physical disk.</li>
            <li><strong>Corrupted or RAW partition, missing drive letter:</strong> Deep Scan of the physical disk or the partition without a drive letter. Also try Quick Scan of that partition - the engine reads the boot sector directly and may still find the MFT.</li>
            <li><strong>Deleted folder:</strong> Single-folder Quick Scan with the folder&apos;s original path; it finds files even though the folder itself is gone.</li>
          </ul>
          <ScenarioHelper />
        </Section>

        <Section id="targets" title="Choosing what to scan" icon="drive">
          <ul>
            <li><strong>All drives</strong> scans every connected HDD. Quick mode covers all NTFS/FAT32 volumes plus partitions that lost their drive letter. Deep mode reads every physical HDD end to end.</li>
            <li><strong>Single drive / partition</strong> scans a volume such as <code>D:</code>, a whole physical disk (<code>Disk 1</code>), or one partition of it.</li>
            <li><strong>Single folder</strong> restricts results to files deleted from one folder, such as <code>D:\Users\Alice\Documents</code>, including its subfolders.</li>
          </ul>
          <p>Drives are labelled <Badge tone="green">HDD</Badge> or <Badge tone="amber">SSD</Badge>. Solid-state drives are excluded by default because TRIM wipes deleted data.</p>
        </Section>

        <Section id="confidence" title="Recovery confidence ratings" icon="check">
          <p>Every result gets a score from 0 to 100 and a rating. Hover over a badge in the results table (or open Preview) to see the reasons behind it.</p>
          <div className="mt-4 grid gap-3 md:grid-cols-3 not-prose">
            {[['high', '75 - 100', 'The file header is intact, its clusters have not been reused, and the content decodes (the photo opens, the PDF has pages).', 'Restore with confidence.'],
              ['medium', '45 - 74', 'Some risk: fragments, an estimated size (carving), partial overwriting, or content that could not be verified.', 'Restore and check the file; it often opens fine.'],
              ['low', '0 - 44', 'The header is overwritten, most clusters belong to other files, encryption or compression is involved, or the content fails to decode.', 'Restore only if nothing better exists; expect damage.']].map(([lvl, range, meaning, advice]) => (
              <div key={lvl} className="rounded-lg border border-slate-200 p-4 text-sm">
                <div className="flex items-center justify-between"><ConfidenceBadge level={lvl} /><span className="text-xs text-slate-500">{range}</span></div>
                <p className="mt-2 text-slate-700">{meaning}</p>
                <p className="mt-2 font-medium text-slate-900">{advice}</p>
              </div>
            ))}
          </div>
          <h3>What is checked</h3>
          <ul>
            <li><strong>Header intactness:</strong> do the first bytes match the expected signature for the file type?</li>
            <li><strong>Sector overwrite:</strong> how many of the file&apos;s clusters are now marked as used in the NTFS $Bitmap or FAT?</li>
            <li><strong>Fragmentation:</strong> how many pieces the file is split into; carving cannot follow fragments.</li>
            <li><strong>Structure:</strong> was the end-of-file marker found (JPEG FF D9, PDF %%EOF, ZIP central directory)?</li>
            <li><strong>Content decoding:</strong> the preview sample is opened with real decoders (images, PDF pages, ZIP CRCs).</li>
          </ul>
          <ScoreExplainer />
        </Section>

        <Section id="restore" title="Restoring files" icon="restore">
          <Step n={1} title="Select files">Tick files in the results table, use filters (type, confidence, deletion event) and &quot;Select all matching&quot; for large sets.</Step>
          <Step n={2} title="Choose a destination on another drive">Click <strong>Restore Selected Files</strong>, enter a folder such as <code>E:\Recovered\2026-10-05</code>, and press <strong>Check destination</strong>. Unsafe targets are blocked.</Step>
          <Step n={3} title="Pick naming options">
            <em>Smart names</em> gives carved files meaningful names derived from their content (for example <code>Financial_Report_2026.pdf</code> or <code>Photo_2025-07-08_iPhone_13.jpg</code>). <em>Recreate folders</em> rebuilds the original folder tree, such as <code>D_drive\Users\Alice\Documents</code>.
          </Step>
          <Step n={4} title="Verify">
            Existing files are never overwritten; duplicates get <code>(1)</code>, <code>(2)</code> suffixes. A <code>_recovery_log_*.json</code> file with SHA-256 checksums is saved alongside the restored files.
          </Step>
        </Section>

        <Section id="reports" title="Reports & history" icon="clock">
          <p>Every scan is kept under <Link href="/history">History &amp; Reports</Link>, so you can reopen it later and restore more files without scanning again. The search box there searches <em>inside</em> recovered documents across all sessions (keywords extracted from PDFs, Word, Excel and text files).</p>
          <p>Use <strong>PDF report</strong> to create a summary with:</p>
          <ul>
            <li>scan settings</li>
            <li>file types found</li>
            <li>confidence breakdown</li>
            <li>deletion events</li>
            <li>drive health warnings (unreadable sectors)</li>
            <li>the list of the most recoverable files</li>
          </ul>
        </Section>

        <Section id="faq" title="Troubleshooting & FAQ" icon="info">
          <input className="input mt-3" placeholder="Search the FAQ…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <FaqList filter={filter} />
        </Section>

        <Section id="glossary" title="Glossary" icon="book">
          <dl className="mt-3 grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 not-prose">
            {[['MFT (Master File Table)', 'The NTFS index containing one 1 KB record per file: name, folder, timestamps and where the data lives.'],
              ['Data runs', 'The list of cluster ranges that hold a file’s content, stored in its MFT record.'],
              ['Cluster', 'The allocation unit of a file system, usually 4 KB on NTFS.'],
              ['Sector / LBA', 'The smallest addressable unit of a disk (512 or 4096 bytes) and its number (Logical Block Address).'],
              ['Carving', 'Finding files by recognising their content signatures instead of using file-system metadata.'],
              ['Signature (magic bytes)', 'Fixed bytes at the start of a file type, for example %PDF- or FF D8 FF for JPEG.'],
              ['Fragmentation', 'A file stored in several non-adjacent pieces.'],
              ['Deletion event', 'A group of recovered files stored next to each other, usually deleted together.'],
              ['VBR', 'Volume Boot Record: the first sector of a partition, describing its file system layout.'],
              ['TRIM', 'An SSD command that erases deleted blocks, making recovery on SSDs unlikely.'],
              ['Elevation / UAC', 'Running a program with full administrator rights after confirming the User Account Control prompt.'],
              ['Volume GUID', 'Windows’ unique identifier of a volume, used to make sure the destination is not the source.']].map(([t, d]) => (
              <div key={t}><dt className="font-semibold text-slate-900">{t}</dt><dd className="text-slate-600">{d}</dd></div>
            ))}
          </dl>
        </Section>
      </div>
    </div>
  );
}

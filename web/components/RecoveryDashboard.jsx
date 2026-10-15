'use client';
/**
 * RecoveryDashboard.jsx - Track C, Module 3: Recovery Config Wizard + Results Dashboard.
 *
 *  ┌ Wizard ──────────────────────────────────────────────────────────────┐
 *  │ 1 Scenario helper (Accidental delete / Formatted / Corrupted / Custom) │
 *  │ 2 Target scope (All drives · Single drive/partition · Single folder)  │
 *  │ 3 Scan mode (Quick MFT · Deep carving) + file-type filter             │
 *  │ 4 Review + safety acknowledgement → POST /api/scans                   │
 *  └──────────────────────────────────────────────────────────────────────┘
 *  ┌ Workspace ────────────────────────────────────────────────────────────┐
 *  │ Live progress (polls /progress every second, cancel)                  │
 *  │ Results: stats, health warnings, deletion events, filters, paginated  │
 *  │ table with confidence badges, preview modal, "Restore Selected Files" │
 *  │ with destination guard, PDF report generation                         │
 *  └──────────────────────────────────────────────────────────────────────┘
 * Talks to the backend only through lib/api.js (REST contract).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { api, isLiveMode, setLiveMode, getAgentUrl } from '@/lib/api';
import {
  Alert, Badge, ConfidenceBadge, EmptyState, Icon, Modal, ProgressBar, Spinner, StatCard,
  classNames, formatBytes, formatDate, formatDuration,
} from './ui';

// ============================================================================= constants
const SCENARIOS = [
  { id: 'accidental_delete', title: 'Accidentally deleted files', mode: 'quick', icon: 'file',
    text: 'Files were deleted (Shift+Delete or emptied Recycle Bin) but the drive still works normally.',
    tip: 'Quick Scan reads the Master File Table and usually restores original names and folders.' },
  { id: 'formatted_drive', title: 'Formatted drive', mode: 'deep', icon: 'layers',
    text: 'The partition was formatted or re-installed and the old file table is gone.',
    tip: 'Deep Scan searches every sector for file signatures (JPEG, PDF, DOCX, ...). Names are rebuilt from content.' },
  { id: 'corrupted_partition', title: 'Corrupted or RAW partition', mode: 'deep', icon: 'warning',
    text: 'Windows asks to format the drive, shows it as RAW, or the drive letter disappeared.',
    tip: 'Select the physical disk or its partition. Deep Scan ignores damaged metadata entirely.' },
  { id: 'custom', title: 'Custom / expert', mode: 'quick', icon: 'bolt',
    text: 'Choose every option yourself.', tip: 'Run Quick Scan first; use Deep Scan if the results are incomplete.' },
];

const TYPE_GROUPS = [
  { label: 'Photos', types: ['jpg', 'png', 'gif', 'bmp', 'heic', 'webp'] },
  { label: 'Documents', types: ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'txt', 'odt'] },
  { label: 'Archives', types: ['zip', 'rar', '7z'] },
  { label: 'Audio / Video', types: ['mp4', 'mov', 'wav', 'avi', 'm4a'] },
  { label: 'Other', types: ['sqlite', 'msg'] },
];

const ACTIVE = ['queued', 'running', 'analyzing'];
const PHASES = {
  queued: 'Queued', starting: 'Starting engine', mft: 'Reading Master File Table', verify: 'Verifying file headers',
  fat_directories: 'Walking FAT directories', carve: 'Carving sectors', analysis: 'Analysing results', done: 'Finished',
};

const isLetterSpec = (s) => /^[A-Za-z]:$/.test(s || '');
const letterOf = (p) => (/^([A-Za-z]):/.exec(p || '') || [])[1];

// ============================================================================= root
export default function RecoveryDashboard() {
  const params = useSearchParams();
  const router = useRouter();
  const [system, setSystem] = useState(null);
  const [scanId, setScanId] = useState(params.get('scan'));
  const [live, setLive] = useState(false);

  useEffect(() => {
    const update = () => {
      setLive(isLiveMode());
      api.systemStatus().then(setSystem).catch(() => setSystem(null));
    };
    update();
    window.addEventListener('frs_mode_change', update);
    window.addEventListener('storage', update);
    return () => {
      window.removeEventListener('frs_mode_change', update);
      window.removeEventListener('storage', update);
    };
  }, []);

  useEffect(() => {
    setScanId(params.get('scan'));
  }, [params]);

  const openScan = (id) => {
    setScanId(id);
    router.replace(id ? `/dashboard?scan=${id}` : '/dashboard');
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{scanId ? 'Recovery session' : 'Recover deleted files'}</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">NTFS and FAT32 hard disk drives on Windows 10/11 - read-only scanning.</p>
        </div>
        {scanId && <button type="button" className="btn-secondary" onClick={() => openScan(null)}><Icon name="search" /> New scan</button>}
      </div>

      {/* Engine Status & Switcher Banner */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200/80 bg-slate-50/80 px-4 py-2.5 text-xs dark:border-slate-800 dark:bg-slate-900/70 shadow-sm">
        <div className="flex items-center gap-2">
          {live ? (
            <>
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500"></span>
              </span>
              <span className="font-bold text-emerald-800 dark:text-emerald-300">Live Windows Engine Mode</span>
              <span className="text-slate-600 dark:text-slate-400">• Scanning real drives on this PC via agent ({getAgentUrl()})</span>
            </>
          ) : (
            <>
              <span className="h-2.5 w-2.5 rounded-full bg-amber-500"></span>
              <span className="font-bold text-amber-800 dark:text-amber-300">Simulation (Mock) Mode</span>
              <span className="text-slate-600 dark:text-slate-400">• Displaying simulated demo disks and files</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          {live ? (
            <button
              type="button"
              onClick={() => setLiveMode(false)}
              className="text-xs font-medium text-slate-600 hover:text-slate-900 underline dark:text-slate-400 dark:hover:text-slate-200"
            >
              Switch to Simulation
            </button>
          ) : (
            <Link
              href="/agent"
              className="font-semibold text-brand-600 hover:text-brand-700 underline dark:text-indigo-400"
            >
              Connect Windows Recovery Agent →
            </Link>
          )}
        </div>
      </div>

      <SystemBanner system={system} />
      {scanId ? <ScanWorkspace key={scanId} scanId={scanId} onNewScan={() => openScan(null)} />
        : <ScanWizard system={system} onStarted={(s) => openScan(s.id)} />}
    </div>
  );
}

function SystemBanner({ system }) {
  if (!system) return null;
  return (
    <div className="space-y-2">
      {!system.elevated && !system.mock_mode && (
        <Alert tone="danger" title="Administrator rights required">
          The recovery service cannot open raw drives. Close it and start it from an elevated terminal.{' '}
          <Link className="font-semibold underline" href="/manual#permissions">How to fix this</Link>
        </Alert>
      )}
      {system.warnings.filter((w) => !/Administrator/.test(w)).map((w) => <Alert key={w} tone="warning">{w}</Alert>)}
    </div>
  );
}

// ============================================================================= wizard
function StepHeader({ step, setStep, canGo }) {
  const steps = ['Scenario', 'What to scan', 'Scan options', 'Review & start'];
  return (
    <ol className="flex flex-wrap gap-2">
      {steps.map((s, i) => (
        <li key={s}>
          <button type="button" disabled={!canGo(i)} onClick={() => setStep(i)}
            className={classNames('flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-colors',
              i === step ? 'bg-brand-600 text-white shadow-sm'
                : i < step ? 'bg-brand-50 text-brand-700 dark:bg-brand-950/70 dark:text-brand-300 dark:border dark:border-brand-800/40'
                : 'bg-slate-100 text-slate-500 dark:bg-slate-800/90 dark:text-slate-400',
              'disabled:cursor-not-allowed')}>
            <span className={classNames('flex h-5 w-5 items-center justify-center rounded-full text-xs font-semibold',
              i === step ? 'bg-white/20' : 'bg-white text-slate-700 dark:bg-slate-900 dark:text-slate-300')}>{i < step ? <Icon name="check" className="h-3 w-3" /> : i + 1}</span>
            {s}
          </button>
        </li>
      ))}
    </ol>
  );
}

function ScanWizard({ system, onStarted }) {
  const [step, setStep] = useState(0);
  const [scenario, setScenario] = useState('accidental_delete');
  const [scope, setScope] = useState('drive');
  const [sources, setSources] = useState([]);
  const [folderPath, setFolderPath] = useState('');
  const [mode, setMode] = useState('quick');
  const [types, setTypes] = useState([]);
  const [allowNonHdd, setAllowNonHdd] = useState(false);
  const [unallocatedOnly, setUnallocatedOnly] = useState(false);
  const [label, setLabel] = useState('');
  const [ack, setAck] = useState(false);
  const [drives, setDrives] = useState(null);
  const [drivesError, setDrivesError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const loadDrives = useCallback((refresh = false) => {
    setDrivesError(null);
    setDrives(null);
    api.drives(refresh).then(setDrives).catch((e) => setDrivesError(e.message));
  }, []);
  useEffect(() => loadDrives(false), [loadDrives]);

  useEffect(() => {
    const handler = () => loadDrives(true);
    window.addEventListener('frs_mode_change', handler);
    return () => window.removeEventListener('frs_mode_change', handler);
  }, [loadDrives]);

  const pickScenario = (id) => {
    setScenario(id);
    const sc = SCENARIOS.find((s) => s.id === id);
    setMode(sc.mode);
    if (id === 'corrupted_partition') setScope('drive');
    setStep(1);
  };

  // Media type of each selectable spec (for the HDD-only policy).
  const mediaOf = useMemo(() => {
    const map = {};
    if (!drives) return map;
    const diskMedia = Object.fromEntries(drives.physical_drives.map((d) => [d.index, d.media_type]));
    drives.volumes.forEach((v) => { map[v.source_spec] = diskMedia[v.physical_drive_index] || 'Unknown'; });
    drives.physical_drives.forEach((d) => {
      map[d.device_path] = d.media_type;
      d.partitions.forEach((p) => { map[p.source_spec] = d.media_type; });
    });
    return map;
  }, [drives]);

  const selectedSpecs = scope === 'folder' ? (letterOf(folderPath) ? [`${letterOf(folderPath).toUpperCase()}:`] : []) : scope === 'all' ? [] : sources;
  const ssdSelected = selectedSpecs.some((s) => mediaOf[s] === 'SSD');
  const systemSelected = drives && selectedSpecs.some((s) => drives.volumes.find((v) => v.source_spec === s && v.is_system_volume));
  const folderValid = /^[A-Za-z]:\\/.test(folderPath.trim());
  const targetValid = scope === 'all' || (scope === 'drive' && sources.length > 0) || (scope === 'folder' && folderValid);
  const canGo = (i) => i <= step || (i === 1) || (i >= 2 && targetValid && (!ssdSelected || allowNonHdd));

  const estimatedBytes = useMemo(() => {
    if (!drives) return 0;
    const sizeOf = (spec) => {
      const v = drives.volumes.find((x) => x.source_spec === spec);
      if (v) return v.total_bytes;
      for (const d of drives.physical_drives) {
        if (d.device_path === spec) return d.size_bytes;
        const p = d.partitions.find((x) => x.source_spec === spec);
        if (p) return p.length_bytes;
      }
      return 0;
    };
    if (scope === 'all') return drives.physical_drives.filter((d) => d.media_type !== 'SSD').reduce((s, d) => s + d.size_bytes, 0);
    return selectedSpecs.reduce((s, x) => s + sizeOf(x), 0);
  }, [drives, scope, selectedSpecs]);

  async function start() {
    setSubmitting(true);
    setError(null);
    try {
      const s = await api.createScan({
        label: label.trim() || undefined, mode, target_scope: scope, scenario,
        sources: scope === 'drive' ? sources : [], folder_path: scope === 'folder' ? folderPath.trim() : null,
        file_types: types, allow_non_hdd: allowNonHdd, unallocated_only: mode === 'deep' && unallocatedOnly,
      });
      onStarted(s);
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  const toggle = (arr, setArr, v) => setArr(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

  return (
    <div className="card p-6">
      <StepHeader step={step} setStep={setStep} canGo={canGo} />
      <div className="mt-6">
        {/* ---------------------------------------------------------------- step 1 */}
        {step === 0 && (
          <div>
            <h2 className="text-lg font-semibold text-slate-900 dark:text-white">What happened to your files?</h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">The scenario helper picks the right scan for you. You can change everything later.</p>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {SCENARIOS.map((s) => (
                <button key={s.id} type="button" onClick={() => pickScenario(s.id)}
                  className={classNames('rounded-xl border p-4 text-left transition hover:border-brand-400 dark:hover:border-brand-500 hover:shadow dark:hover:shadow-black/30',
                    scenario === s.id ? 'border-brand-500 ring-2 ring-brand-100 dark:ring-brand-950 dark:border-brand-500 bg-brand-50/30 dark:bg-brand-950/30' : 'border-slate-200 dark:border-slate-800 dark:bg-slate-900/50')}>
                  <div className="flex items-center gap-2 font-semibold text-slate-900 dark:text-white">
                    <span className="rounded-lg bg-brand-50 dark:bg-brand-950/80 p-1.5 text-brand-600 dark:text-indigo-400"><Icon name={s.icon} /></span>{s.title}
                    <Badge tone={s.mode === 'quick' ? 'green' : 'purple'}>{s.mode === 'quick' ? 'Quick Scan' : 'Deep Scan'}</Badge>
                  </div>
                  <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{s.text}</p>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{s.tip}</p>
                </button>
              ))}
            </div>
            <div className="mt-4">
              <Alert tone="warning" title="Stop using the affected drive now">
                Every file you save, download or install may overwrite deleted data. Never install this software or save recovered files on the drive you are recovering from.
              </Alert>
            </div>
          </div>
        )}

        {/* ---------------------------------------------------------------- step 2 */}
        {step === 1 && (
          <div className="space-y-5">
            <div>
              <h2 className="text-lg font-semibold text-slate-900 dark:text-white">What should be scanned?</h2>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                {[
                  { id: 'all', title: 'All drives', text: 'Every HDD and partition that is connected', icon: 'layers' },
                  { id: 'drive', title: 'Single drive / partition', text: 'A volume (D:) or a physical disk', icon: 'drive' },
                  { id: 'folder', title: 'Single folder', text: 'Only files deleted from one folder', icon: 'folder' },
                ].map((o) => (
                  <button key={o.id} type="button" onClick={() => setScope(o.id)}
                    className={classNames('rounded-xl border p-3 text-left transition',
                      scope === o.id
                        ? 'border-brand-500 ring-2 ring-brand-100 dark:ring-brand-950 dark:border-brand-500 bg-brand-50/20 dark:bg-brand-950/30'
                        : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 dark:bg-slate-900/50')}>
                    <div className="flex items-center gap-2 font-medium text-slate-900 dark:text-white"><Icon name={o.icon} />{o.title}</div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{o.text}</div>
                  </button>
                ))}
              </div>
            </div>

            {scope === 'folder' && (
              <div>
                <label className="label" htmlFor="folder">Folder that contained the deleted files</label>
                <input id="folder" className="input font-mono" placeholder="D:\Users\Alice\Documents" value={folderPath}
                  onChange={(e) => setFolderPath(e.target.value)} />
                <p className="mt-1 text-xs text-slate-500">
                  Quick Scan keeps only deleted files whose parent folder chain leads here (works even if the folder itself was deleted).
                  Deep Scan restricts carving to free space of this folder&apos;s partition.
                </p>
                {folderPath && !folderValid && <p className="mt-1 text-xs text-red-600">Enter an absolute path that starts with a drive letter, e.g. D:\Photos</p>}
              </div>
            )}

            {scope !== 'folder' && (
              <div>
                <div className="flex items-center justify-between">
                  <span className="label">{scope === 'all' ? 'Detected drives (all HDDs will be scanned)' : 'Choose drives or partitions'}</span>
                  <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => loadDrives(true)}>Refresh</button>
                </div>
                {drivesError && <Alert tone="danger">{drivesError}</Alert>}
                {!drives && !drivesError && <div className="flex items-center gap-2 py-6 text-sm text-slate-500"><Spinner /> Detecting drives…</div>}
                {drives && (
                  <DriveSelector drives={drives} scope={scope} selected={sources} mode={mode}
                    onToggle={(spec) => toggle(sources, setSources, spec)} />
                )}
              </div>
            )}

            {ssdSelected && (
              <Alert tone="warning" title="Solid-state drive selected">
                This tool is designed for hard disk drives. SSDs erase deleted blocks automatically (TRIM), so recovery is usually impossible.
                <label className="mt-2 flex items-center gap-2 font-medium">
                  <input type="checkbox" checked={allowNonHdd} onChange={(e) => setAllowNonHdd(e.target.checked)} /> Scan it anyway
                </label>
              </Alert>
            )}
            {systemSelected && (
              <Alert tone="info" title="This is your Windows system drive">
                Windows writes to it constantly, which can overwrite deleted files. For the best results, shut down and connect the drive to another PC as a secondary disk.
              </Alert>
            )}
            <div className="flex justify-between">
              <button type="button" className="btn-secondary" onClick={() => setStep(0)}>Back</button>
              <button type="button" className="btn-primary" disabled={!targetValid || (ssdSelected && !allowNonHdd)} onClick={() => setStep(2)}>Continue</button>
            </div>
          </div>
        )}

        {/* ---------------------------------------------------------------- step 3 */}
        {step === 2 && (
          <div className="space-y-5">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-white">How deep should we look?</h2>
            <div className="grid gap-3 md:grid-cols-2">
              <ModeCard active={mode === 'quick'} onClick={() => setMode('quick')} title="Quick Scan (MFT)" badge="Minutes"
                points={['Reads the NTFS Master File Table / FAT directories', 'Keeps original file names and folders', 'Detects whether clusters were overwritten', 'Best for recently deleted files']} />
              <ModeCard active={mode === 'deep'} onClick={() => setMode('deep')} title="Deep Scan (sector carving)" badge="Hours on large disks"
                points={['Reads every sector looking for file signatures', 'Works after formatting or corruption', 'Names are rebuilt from content (smart naming)', 'Finds JPEG, PNG, PDF, DOCX/XLSX, ZIP, MP4, ...']} />
            </div>
            <div>
              <div className="label">File types <span className="font-normal text-slate-500 dark:text-slate-400">(leave empty for all types)</span></div>
              <div className="space-y-2">
                {TYPE_GROUPS.map((g) => (
                  <div key={g.label} className="flex flex-wrap items-center gap-1.5">
                    <button type="button" className="w-28 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 hover:text-brand-600 dark:hover:text-brand-400"
                      onClick={() => setTypes(g.types.every((t) => types.includes(t)) ? types.filter((t) => !g.types.includes(t)) : [...new Set([...types, ...g.types])])}>
                      {g.label}
                    </button>
                    {g.types.map((t) => (
                      <button key={t} type="button" onClick={() => toggle(types, setTypes, t)}
                        className={classNames('rounded-full border px-2.5 py-0.5 text-xs font-medium transition',
                          types.includes(t)
                            ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-950/80 dark:text-brand-300 dark:border-brand-800'
                            : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-600')}>
                        .{t}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </div>
            {mode === 'deep' && scope !== 'folder' && (
              <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                <input type="checkbox" className="mt-1" checked={unallocatedOnly} onChange={(e) => setUnallocatedOnly(e.target.checked)} />
                <span>Only carve free (unallocated) space<span className="block text-xs text-slate-500 dark:text-slate-400">Faster and skips existing files. Uses the NTFS $Bitmap / FAT table when it is readable.</span></span>
              </label>
            )}
            <div className="flex justify-between">
              <button type="button" className="btn-secondary" onClick={() => setStep(1)}>Back</button>
              <button type="button" className="btn-primary" onClick={() => setStep(3)}>Review</button>
            </div>
          </div>
        )}

        {/* ---------------------------------------------------------------- step 4 */}
        {step === 3 && (
          <div className="space-y-5">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Review and start</h2>
            <dl className="grid gap-x-6 gap-y-2 rounded-lg bg-slate-50 dark:bg-slate-800/60 p-4 text-sm sm:grid-cols-2 border border-slate-200/60 dark:border-slate-700/60">
              <Row k="Scenario" v={SCENARIOS.find((s) => s.id === scenario).title} />
              <Row k="Scan mode" v={mode === 'quick' ? 'Quick Scan (MFT / FAT metadata)' : 'Deep Scan (sector-by-sector carving)'} />
              <Row k="Target" v={scope === 'all' ? 'All connected HDDs' : scope === 'folder' ? folderPath : sources.join(', ')} />
              <Row k="File types" v={types.length ? types.map((t) => `.${t}`).join(' ') : 'All supported types'} />
              {mode === 'deep' && estimatedBytes > 0 && (
                <Row k="Estimated time" v={`about ${formatDuration(estimatedBytes / (120 * 1024 * 1024))} at 120 MB/s for ${formatBytes(estimatedBytes)}`} />
              )}
            </dl>
            <div>
              <label className="label" htmlFor="label">Session name (optional)</label>
              <input id="label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Holiday photos from D:" maxLength={120} />
            </div>
            <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200">
              <input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              <span>I understand that scanning is read-only, and that I must restore files to a <strong>different physical drive</strong> than the one being scanned.</span>
            </label>
            {system && !system.elevated && !system.mock_mode && (
              <Alert tone="danger">The backend is not elevated, so this scan will fail. <Link href="/manual#permissions" className="underline">Fix permissions first</Link>.</Alert>
            )}
            {error && <Alert tone="danger">{error}</Alert>}
            <div className="flex justify-between">
              <button type="button" className="btn-secondary" onClick={() => setStep(2)}>Back</button>
              <button type="button" className="btn-primary" disabled={!ack || submitting || !targetValid} onClick={start}>
                {submitting ? <Spinner /> : <Icon name="search" />} Start {mode === 'quick' ? 'Quick' : 'Deep'} Scan
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div className="flex gap-2">
      <dt className="w-32 flex-none text-slate-500 dark:text-slate-400">{k}</dt>
      <dd className="font-medium text-slate-800 dark:text-slate-200">{v}</dd>
    </div>
  );
}

function ModeCard({ active, onClick, title, badge, points }) {
  return (
    <button type="button" onClick={onClick}
      className={classNames('rounded-xl border p-4 text-left transition',
        active
          ? 'border-brand-500 ring-2 ring-brand-100 dark:ring-brand-950 dark:border-brand-500 bg-brand-50/20 dark:bg-brand-950/30'
          : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 dark:bg-slate-900/50')}>
      <div className="flex items-center justify-between">
        <span className="font-semibold text-slate-900 dark:text-white">{title}</span><Badge tone="slate">{badge}</Badge>
      </div>
      <ul className="mt-2 space-y-1 text-sm text-slate-600 dark:text-slate-300">
        {points.map((p) => <li key={p} className="flex gap-2"><Icon name="check" className="mt-0.5 h-4 w-4 flex-none text-emerald-500" />{p}</li>)}
      </ul>
    </button>
  );
}

function DriveSelector({ drives, scope, selected, onToggle, mode }) {
  const disks = drives.physical_drives;
  const volumesOf = (idx) => drives.volumes.filter((v) => v.physical_drive_index === idx);
  return (
    <div className="space-y-3">
      {disks.map((d) => {
        const vols = volumesOf(d.index);
        const unmounted = d.partitions.filter((p) => !p.mounted_letter);
        return (
          <div key={d.index} className="rounded-xl border border-slate-200 dark:border-slate-800 dark:bg-slate-900/40">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 dark:border-slate-800/80 px-4 py-2.5">
              <Icon name="drive" className="h-5 w-5 text-slate-500 dark:text-slate-400" />
              <span className="font-medium text-slate-900 dark:text-slate-100">Disk {d.index}: {d.model || 'Unknown model'}</span>
              <Badge tone={d.media_type === 'HDD' ? 'green' : d.media_type === 'SSD' ? 'amber' : 'slate'}>{d.media_type}</Badge>
              <Badge>{d.bus_type}</Badge>
              <span className="text-sm text-slate-500 dark:text-slate-400">{formatBytes(d.size_bytes)} · {d.partition_style}</span>
              {scope === 'drive' && (
                <label className="ml-auto flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                  <input type="checkbox" checked={selected.includes(d.device_path)} onChange={() => onToggle(d.device_path)} />
                  Whole disk{mode === 'deep' ? ' (recommended for Deep Scan)' : ''}
                </label>
              )}
            </div>
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {vols.map((v) => (
                <label key={v.letter} className={classNames('flex items-center gap-3 px-4 py-2.5 text-sm', scope === 'drive' && 'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50')}>
                  {scope === 'drive' ? <input type="checkbox" checked={selected.includes(v.source_spec)} onChange={() => onToggle(v.source_spec)} />
                    : <Icon name="check" className={classNames('h-4 w-4', d.media_type === 'SSD' ? 'text-slate-300 dark:text-slate-600' : 'text-emerald-500')} />}
                  <span className="w-10 font-mono font-semibold text-slate-900 dark:text-slate-100">{v.letter}</span>
                  <span className="flex-1 truncate text-slate-800 dark:text-slate-200">{v.label || 'Local Disk'}</span>
                  <Badge tone={v.filesystem === 'RAW' ? 'red' : 'blue'}>{v.filesystem}</Badge>
                  {v.is_system_volume && <Badge tone="amber">Windows</Badge>}
                  <span className="hidden w-48 text-right text-slate-500 dark:text-slate-400 sm:block">{formatBytes(v.total_bytes - v.free_bytes)} used of {formatBytes(v.total_bytes)}</span>
                </label>
              ))}
              {unmounted.map((p) => (
                <label key={p.source_spec} className={classNames('flex items-center gap-3 px-4 py-2.5 text-sm', scope === 'drive' && 'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50')}>
                  {scope === 'drive' ? <input type="checkbox" checked={selected.includes(p.source_spec)} onChange={() => onToggle(p.source_spec)} />
                    : <Icon name="check" className="h-4 w-4 text-emerald-500" />}
                  <span className="w-10 font-mono text-slate-400">--</span>
                  <span className="flex-1 truncate text-slate-700 dark:text-slate-300">Partition {p.number} · {p.type_label}</span>
                  <Badge tone="red">No drive letter</Badge>
                  <span className="hidden w-48 text-right text-slate-500 dark:text-slate-400 sm:block">{formatBytes(p.length_bytes)}</span>
                </label>
              ))}
            </div>
          </div>
        );
      })}
      {disks.length === 0 && <EmptyState title="No drives detected" icon="drive">Make sure the backend runs as Administrator and the drive is connected.</EmptyState>}
    </div>
  );
}

// ============================================================================= workspace
function ScanWorkspace({ scanId, onNewScan }) {
  const [scan, setScan] = useState(null);
  const [error, setError] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef(null);

  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const p = await api.getProgress(scanId);
      if (!alive.current) return; // unmounted while the request was in flight: stop polling
      if (ACTIVE.includes(p.status)) {
        setScan((s) => (s ? { ...s, status: p.status, progress: p.progress, totals: p.totals, error: p.error } : s));
        timer.current = setTimeout(refresh, 1000);
      } else {
        const done = await api.getScan(scanId);
        if (alive.current) setScan(done);
      }
    } catch (e) {
      if (alive.current) setError(e.message);
    }
  }, [scanId]);

  useEffect(() => {
    alive.current = true;
    api.getScan(scanId).then((s) => {
      if (!alive.current) return;
      setScan(s);
      if (ACTIVE.includes(s.status)) timer.current = setTimeout(refresh, 800);
    }).catch((e) => { if (alive.current) setError(e.message); });
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
    };
  }, [scanId, refresh]);

  async function cancel() {
    setCancelling(true);
    try {
      await api.cancelScan(scanId);
    } catch {
      // Usually 409: the scan finished in the meantime. Not fatal - reload the session instead of
      // replacing the whole view with an error.
      setCancelling(false);
      refresh();
    }
  }

  if (error) return <Alert tone="danger" title="Could not load this session">{error}</Alert>;
  if (!scan) return <div className="flex justify-center py-16 text-slate-500"><Spinner className="h-6 w-6" /></div>;

  const active = ACTIVE.includes(scan.status);
  return (
    <div className="space-y-6">
      <div className="card flex flex-wrap items-center gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-slate-900 dark:text-white">{scan.label}</div>
          <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            {scan.mode === 'quick' ? 'Quick Scan' : 'Deep Scan'} · {scan.target_scope === 'all' ? 'All drives' : scan.target_scope === 'folder' ? scan.folder_path : scan.sources.join(', ')} · started {formatDate(scan.started_at || scan.created_at)}
          </div>
        </div>
        <StatusBadge status={scan.status} />
      </div>
      {active && <ProgressPanel scan={scan} onCancel={cancel} cancelling={cancelling} />}
      {scan.status === 'failed' && (
        <Alert tone="danger" title="The scan failed">
          {scan.error || 'Unknown error.'}
          {/Administrator/i.test(scan.error || '') && <> <Link href="/manual#permissions" className="font-semibold underline">Fix permissions</Link>.</>}
          <div className="mt-3"><button type="button" className="btn-secondary" onClick={onNewScan}>Start a new scan</button></div>
        </Alert>
      )}
      {!active && scan.status !== 'failed' && <ResultsPanel scan={scan} />}
    </div>
  );
}

function StatusBadge({ status }) {
  const tone = { completed: 'green', partial: 'amber', cancelled: 'amber', failed: 'red', running: 'blue', analyzing: 'purple', queued: 'slate' }[status];
  return <Badge tone={tone}>{status}</Badge>;
}

function ProgressPanel({ scan, onCancel, cancelling }) {
  const p = scan.progress || {};
  const log = p.log || [];
  return (
    <div className="card p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-500 dark:text-slate-400">{PHASES[p.phase] || p.phase || 'Working'}</div>
          <div className="text-3xl font-bold text-slate-900 dark:text-white">{Number(p.overall_percent || 0).toFixed(1)}%</div>
        </div>
        <button type="button" className="btn-danger" onClick={onCancel} disabled={cancelling || scan.status === 'analyzing'}>
          {cancelling ? <Spinner /> : <Icon name="x" />} {cancelling ? 'Stopping…' : 'Cancel scan'}
        </button>
      </div>
      <div className="mt-4"><ProgressBar value={p.overall_percent} striped={scan.status === 'queued'} /></div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <Metric label="Files found" value={(p.candidates_found || 0).toLocaleString()} />
        <Metric label="Source" value={`${(p.source_index || 0) + 1} of ${Math.max(1, p.source_count || 1)}`} hint={p.current_source} />
        <Metric label={p.unit === 'bytes' ? 'Read speed' : 'Progress'} value={p.unit === 'bytes' ? `${Number(p.rate_mb_s || 0).toFixed(0)} MB/s`
          : `${(p.bytes_done || 0).toLocaleString()} / ${(p.bytes_total || 0).toLocaleString()} ${p.unit || ''}`} />
        <Metric label="Time remaining" value={p.eta_seconds ? formatDuration(p.eta_seconds) : '-'} />
      </div>
      {p.read_errors > 0 && (
        <Alert tone="warning" title={`${p.read_errors} unreadable sector(s)`}>The drive may be failing. Let the scan finish, then restore the most important files first.</Alert>
      )}
      <div className="mt-4 rounded-lg bg-slate-900 dark:bg-black/80 dark:border dark:border-slate-800 p-3 font-mono text-xs leading-5 text-slate-200">
        {p.message && <div className="text-emerald-300">› {p.message}</div>}
        {log.slice(-8).map((l, i) => (
          <div key={`${l.ts}-${i}`} className={l.level === 'error' ? 'text-red-300' : l.level === 'warning' ? 'text-amber-300' : ''}>
            [{(l.ts || '').slice(11, 19)}] {l.message}
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">You can leave this page; the scan keeps running and is listed under History.</p>
    </div>
  );
}

function Metric({ label, value, hint }) {
  return (
    <div className="rounded-lg bg-slate-50 dark:bg-slate-800/60 dark:border dark:border-slate-700/60 p-3">
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      <div className="font-semibold text-slate-900 dark:text-white">{value}</div>
      {hint && <div className="truncate text-xs text-slate-400 dark:text-slate-500">{hint}</div>}
    </div>
  );
}

// ============================================================================= results
function useDebounced(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function ResultsPanel({ scan }) {
  const [stats, setStats] = useState(null);
  const [clusters, setClusters] = useState([]);
  const [filters, setFilters] = useState({ q: '', extension: [], confidence: [], cluster_id: '', method: '' });
  const [sort, setSort] = useState({ sort: 'confidence', order: 'desc' });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(() => new Map()); // id -> file
  const [preview, setPreview] = useState(null);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [report, setReport] = useState({ busy: false, last: null, error: null });
  const q = useDebounced(filters.q, 300);

  useEffect(() => {
    api.stats(scan.id).then(setStats).catch(() => {});
    api.clusters(scan.id).then(setClusters).catch(() => {});
  }, [scan.id]);

  const query = useMemo(() => ({ page, page_size: pageSize, q: q || undefined, extension: filters.extension, confidence: filters.confidence,
    cluster_id: filters.cluster_id || undefined, method: filters.method || undefined, sort: sort.sort, order: sort.order }),
  [page, pageSize, q, filters.extension, filters.confidence, filters.cluster_id, filters.method, sort]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.listFiles(scan.id, query).then(setData).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [scan.id, query]);
  useEffect(load, [load]);
  useEffect(() => setPage(1), [q, filters.extension, filters.confidence, filters.cluster_id, filters.method, pageSize]);

  const setFilter = (k, v) => setFilters((f) => ({ ...f, [k]: v }));
  const toggleIn = (k, v) => setFilters((f) => ({ ...f, [k]: f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v] }));
  const toggleSort = (col) => setSort((s) => ({ sort: col, order: s.sort === col && s.order === 'desc' ? 'asc' : 'desc' }));

  const items = data ? data.items : [];
  const allOnPage = items.length > 0 && items.every((f) => selected.has(f.id));
  const togglePage = () => setSelected((m) => {
    const n = new Map(m);
    if (allOnPage) items.forEach((f) => n.delete(f.id)); else items.forEach((f) => n.set(f.id, f));
    return n;
  });
  const toggleOne = (f) => setSelected((m) => {
    const n = new Map(m);
    if (n.has(f.id)) n.delete(f.id); else n.set(f.id, f);
    return n;
  });
  const [selectingAll, setSelectingAll] = useState(false);
  async function selectAllMatching() {
    setSelectingAll(true);
    try {
      const n = new Map(selected);
      for (let p = 1; p <= 20; p += 1) {
        const res = await api.listFiles(scan.id, { ...query, page: p, page_size: 500 });
        res.items.forEach((f) => n.set(f.id, f));
        if (p >= res.total_pages) break;
      }
      setSelected(n);
    } catch (e) {
      setError(e.message);
    } finally {
      setSelectingAll(false);
    }
  }

  async function generateReport() {
    setReport({ busy: true, last: report.last, error: null });
    try {
      const r = await api.createReport(scan.id);
      await api.downloadFile(r.download_url, r.file_name);
      setReport({ busy: false, last: r, error: null });
    } catch (e) {
      setReport({ busy: false, last: report.last, error: e.message });
    }
  }

  const totals = scan.totals || {};
  const selectedBytes = [...selected.values()].reduce((s, f) => s + f.size_bytes, 0);
  const extOptions = stats ? Object.entries(stats.by_extension).sort((a, b) => b[1] - a[1]) : [];

  return (
    <div className="space-y-6">
      {scan.status === 'cancelled' && <Alert tone="warning">The scan was cancelled; the results below are partial.</Alert>}
      {scan.status === 'partial' && <Alert tone="warning">Some sources could not be scanned completely. {scan.error}</Alert>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="Files found" value={(totals.files || 0).toLocaleString()} hint={formatBytes(totals.bytes)} />
        <StatCard label="High confidence" value={(totals.high || 0).toLocaleString()} tone="green" hint="Expect intact files" />
        <StatCard label="Medium" value={(totals.medium || 0).toLocaleString()} tone="amber" hint="Some risk" />
        <StatCard label="Low" value={(totals.low || 0).toLocaleString()} tone="red" hint="Likely damaged" />
        <StatCard label="Deletion events" value={(totals.clusters || 0).toLocaleString()} hint="Groups of adjacent files" />
      </div>
      {(scan.health_warnings || []).map((w) => <Alert key={w} tone="danger" title="Drive health warning">{w}</Alert>)}

      {clusters.length > 0 && (
        <div className="card p-4">
          <div className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">Deletion events (files that were stored next to each other)</div>
          <div className="flex flex-wrap gap-2">
            {clusters.map((c) => (
              <button key={c.id} type="button" onClick={() => setFilter('cluster_id', filters.cluster_id === c.id ? '' : c.id)}
                className={classNames('rounded-lg border px-3 py-1.5 text-left text-xs transition',
                  filters.cluster_id === c.id
                    ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-950/80 dark:text-brand-300 dark:border-brand-800'
                    : 'border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-700 dark:bg-slate-900/50')}>
                <div className="font-semibold text-slate-900 dark:text-white">{c.label.split(':')[0]}</div>
                <div className="text-slate-500 dark:text-slate-400">{c.file_count} files · .{c.dominant_extension} · {formatBytes(c.total_bytes)}{c.time_start ? ` · ${formatDate(c.time_start)}` : ''}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        {/* toolbar */}
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 dark:border-slate-800 p-4">
          <div className="relative min-w-[220px] flex-1">
            <Icon name="search" className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input className="input pl-9" placeholder="Search names and folders…" value={filters.q} onChange={(e) => setFilter('q', e.target.value)} />
          </div>
          <div className="flex gap-1">
            {['high', 'medium', 'low'].map((c) => (
              <button key={c} type="button" onClick={() => toggleIn('confidence', c)}
                className={classNames('rounded-full border px-3 py-1 text-xs font-semibold capitalize transition',
                  filters.confidence.includes(c)
                    ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-950/80 dark:text-brand-300 dark:border-brand-800'
                    : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-600')}>
                {c}
              </button>
            ))}
          </div>
          <select className="input w-auto" value={filters.method} onChange={(e) => setFilter('method', e.target.value)} aria-label="Recovery method">
            <option value="">All methods</option>
            <option value="mft">MFT (names kept)</option>
            <option value="fat">FAT32 directory</option>
            <option value="carve">Carved</option>
          </select>
          <button type="button" className="btn-secondary" onClick={generateReport} disabled={report.busy}>
            {report.busy ? <Spinner /> : <Icon name="download" />} PDF report
          </button>
          <button type="button" className="btn-primary" disabled={selected.size === 0} onClick={() => setRestoreOpen(true)}>
            <Icon name="restore" /> Restore Selected Files ({selected.size})
          </button>
        </div>
        {extOptions.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 dark:border-slate-800/80 px-4 py-2">
            <span className="mr-1 text-xs font-medium text-slate-500 dark:text-slate-400">Type:</span>
            {extOptions.map(([ext, n]) => (
              <button key={ext} type="button" onClick={() => toggleIn('extension', ext)}
                className={classNames('rounded-full border px-2.5 py-0.5 text-xs transition',
                  filters.extension.includes(ext)
                    ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-950/80 dark:text-brand-300 dark:border-brand-800'
                    : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-600')}>
                .{ext} <span className="text-slate-400 dark:text-slate-500">{n}</span>
              </button>
            ))}
            {(filters.extension.length > 0 || filters.confidence.length > 0 || filters.cluster_id || filters.method || filters.q) && (
              <button type="button" className="ml-2 text-xs font-medium text-brand-600 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300"
                onClick={() => setFilters({ q: '', extension: [], confidence: [], cluster_id: '', method: '' })}>Clear filters</button>
            )}
          </div>
        )}
        {report.error && <div className="p-4"><Alert tone="danger" onClose={() => setReport({ ...report, error: null })}>{report.error}</Alert></div>}
        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-3 bg-brand-50 dark:bg-brand-950/70 border-y border-brand-200 dark:border-brand-900/50 px-4 py-2 text-sm text-brand-900 dark:text-brand-200">
            <span><strong>{selected.size.toLocaleString()}</strong> selected ({formatBytes(selectedBytes)})</span>
            <button type="button" className="font-medium underline" onClick={() => setSelected(new Map())}>Clear selection</button>
          </div>
        )}
        {data && allOnPage && data.total > items.length && (
          <div className="bg-slate-50 dark:bg-slate-800/60 border-y border-slate-200 dark:border-slate-800 px-4 py-2 text-sm text-slate-600 dark:text-slate-300">
            All {items.length} files on this page are selected.{' '}
            <button type="button" className="font-medium text-brand-600 dark:text-indigo-400 underline" onClick={selectAllMatching} disabled={selectingAll}>
              {selectingAll ? 'Selecting…' : `Select all ${data.total.toLocaleString()} matching files`}
            </button>
          </div>
        )}

        {/* table */}
        {error && <div className="p-4"><Alert tone="danger">{error}</Alert></div>}
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 dark:bg-slate-900/90 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-800">
              <tr>
                <th className="w-10 px-4 py-2.5"><input type="checkbox" checked={allOnPage} onChange={togglePage} aria-label="Select page" /></th>
                <SortTh col="name" sort={sort} onSort={toggleSort}>Name</SortTh>
                <th className="px-3 py-2.5">Original location</th>
                <SortTh col="extension" sort={sort} onSort={toggleSort}>Type</SortTh>
                <SortTh col="size" sort={sort} onSort={toggleSort} right>Size</SortTh>
                <SortTh col="confidence" sort={sort} onSort={toggleSort}>Recovery chance</SortTh>
                <th className="px-3 py-2.5">Modified</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/80">
              {items.map((f) => (
                <tr key={f.id} className={classNames('transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50', selected.has(f.id) && 'bg-brand-50/40 dark:bg-brand-950/40')}>
                  <td className="px-4 py-2.5"><input type="checkbox" checked={selected.has(f.id)} onChange={() => toggleOne(f)} aria-label={`Select ${f.display_name}`} /></td>
                  <td className="max-w-xs px-3 py-2.5">
                    <div className="truncate font-medium text-slate-900 dark:text-slate-100" title={f.display_name}>{f.display_name}</div>
                    {f.suggested_name && f.file_name && f.suggested_name !== f.file_name && (
                      <div className="truncate text-xs text-slate-500 dark:text-slate-400" title="Suggested from content">Suggested: {f.suggested_name}</div>
                    )}
                    {!f.file_name && <div className="text-xs text-violet-600 dark:text-violet-400">Name rebuilt from content</div>}
                    {f.restored && <Badge tone="green">restored</Badge>}
                  </td>
                  <td className="max-w-xs truncate px-3 py-2.5 font-mono text-xs text-slate-500 dark:text-slate-400" title={f.original_path || ''}>
                    {f.original_path || <span className="font-sans italic text-slate-400 dark:text-slate-500">unknown (carved at LBA {f.absolute_lba.toLocaleString()})</span>}
                  </td>
                  <td className="px-3 py-2.5"><Badge>.{f.extension || '?'}</Badge></td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums text-slate-700 dark:text-slate-200">{formatBytes(f.size_bytes)}</td>
                  <td className="px-3 py-2.5"><ConfidenceBadge level={f.confidence} score={f.confidence_score} reasons={f.confidence_reasons} /></td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-xs text-slate-500 dark:text-slate-400">{formatDate(f.timestamps && f.timestamps.modified)}</td>
                  <td className="px-3 py-2.5 text-right">
                    <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setPreview(f)}><Icon name="eye" /> Preview</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {loading && !data && <div className="flex justify-center py-10"><Spinner className="h-6 w-6 text-slate-400" /></div>}
          {data && items.length === 0 && (
            <EmptyState title="No files match these filters">
              {totals.files === 0 ? 'Nothing recoverable was found. If you used Quick Scan, try a Deep Scan of the same drive.' : 'Clear some filters to see more files.'}
            </EmptyState>
          )}
        </div>
        {data && data.total > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 dark:border-slate-800 px-4 py-3 text-sm">
            <span className="text-slate-500 dark:text-slate-400">
              {((data.page - 1) * data.page_size + 1).toLocaleString()}–{Math.min(data.page * data.page_size, data.total).toLocaleString()} of {data.total.toLocaleString()}
              {loading && <Spinner className="ml-2 inline h-3 w-3" />}
            </span>
            <div className="flex items-center gap-2">
              <select className="input w-auto py-1" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} aria-label="Rows per page">
                {[25, 50, 100, 250].map((n) => <option key={n} value={n}>{n} / page</option>)}
              </select>
              <button type="button" className="btn-secondary px-3 py-1" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <span className="text-slate-600">Page {data.page} / {data.total_pages}</span>
              <button type="button" className="btn-secondary px-3 py-1" disabled={page >= data.total_pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        )}
      </div>

      {preview && <PreviewModal scanId={scan.id} file={preview} onClose={() => setPreview(null)}
        onRestore={(f) => { setSelected(new Map([[f.id, f]])); setPreview(null); setRestoreOpen(true); }} />}
      {restoreOpen && <RestoreDialog scan={scan} files={[...selected.values()]} onClose={(done) => { setRestoreOpen(false); if (done) load(); }} />}
    </div>
  );
}

function SortTh({ col, sort, onSort, children, right }) {
  const active = sort.sort === col;
  return (
    <th className={classNames('px-3 py-2', right && 'text-right')}>
      <button type="button" className={classNames('inline-flex items-center gap-1 uppercase', active && 'text-slate-800')} onClick={() => onSort(col)}>
        {children}{active && <span aria-hidden="true">{sort.order === 'asc' ? '▲' : '▼'}</span>}
      </button>
    </th>
  );
}

// ============================================================================= preview modal
function PreviewModal({ scanId, file, onClose, onRestore }) {
  const [preview, setPreview] = useState(null);
  const [img, setImg] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let url = null;
    let alive = true;
    api.getPreview(scanId, file.id).then(async (p) => {
      if (!alive) return;
      setPreview(p);
      if (p.thumbnail_url) {
        try {
          url = await api.fetchBlobUrl(p.thumbnail_url);
          if (alive) setImg(url);
        } catch { /* thumbnail missing: text fallback */ }
      }
    }).catch((e) => setError(e.message));
    return () => {
      alive = false;
      if (url && url.startsWith('blob:')) URL.revokeObjectURL(url);
    };
  }, [scanId, file.id]);

  const ts = file.timestamps || {};
  return (
    <Modal open onClose={onClose} title={file.display_name} wide
      footer={<>
        <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
        <button type="button" className="btn-primary" onClick={() => onRestore(file)}><Icon name="restore" /> Restore this file</button>
      </>}>
      <div className="grid gap-5 md:grid-cols-5">
        <div className="md:col-span-3">
          <div className="flex min-h-[220px] items-center justify-center overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950">
            {error && <div className="p-4 text-sm text-red-600 dark:text-red-400">{error}</div>}
            {!preview && !error && <Spinner className="h-6 w-6 text-slate-400" />}
            {preview && img && <img src={img} alt={`Preview of ${file.display_name}`} className="max-h-[420px] w-auto object-contain" />}
            {preview && !img && preview.text_excerpt && (
              <pre className="max-h-[420px] w-full overflow-auto whitespace-pre-wrap p-4 text-left text-xs leading-5 text-slate-700 dark:text-slate-200">{preview.text_excerpt}</pre>
            )}
            {preview && !img && !preview.text_excerpt && <div className="p-6 text-center text-sm text-slate-500 dark:text-slate-400">{preview.message || 'No preview available.'}</div>}
          </div>
          {preview && preview.keywords && preview.keywords.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Keywords:</span>
              {preview.keywords.map((k) => <Badge key={k} tone="purple">{k}</Badge>)}
            </div>
          )}
        </div>
        <div className="space-y-4 md:col-span-2">
          <div>
            <ConfidenceBadge level={file.confidence} score={file.confidence_score} />
            <ul className="mt-2 space-y-1 text-xs text-slate-600 dark:text-slate-300">
              {(file.confidence_reasons || []).map((r) => <li key={r} className="flex gap-1.5"><span className="text-slate-400 dark:text-slate-500">•</span>{r}</li>)}
            </ul>
          </div>
          <dl className="space-y-1.5 text-xs">
            <Detail k="Original name" v={file.file_name || 'unknown (carved)'} />
            <Detail k="Suggested name" v={file.suggested_name || '-'} />
            <Detail k="Original folder" v={file.original_path || '-'} mono />
            <Detail k="Size" v={`${formatBytes(file.size_bytes)} (${file.size_bytes.toLocaleString()} bytes)`} />
            <Detail k="Method" v={{ mft: 'NTFS $MFT record', fat: 'FAT32 directory entry', carve: 'Signature carving' }[file.method]} />
            <Detail k="Fragments" v={file.fragment_count} />
            <Detail k="Location (LBA)" v={file.absolute_lba.toLocaleString()} />
            <Detail k="Created" v={formatDate(ts.created)} />
            <Detail k="Modified" v={formatDate(ts.modified)} />
            <Detail k="Deleted (approx.)" v={formatDate(ts.mft_modified)} />
          </dl>
        </div>
      </div>
    </Modal>
  );
}

function Detail({ k, v, mono }) {
  return (
    <div className="flex gap-2">
      <dt className="w-28 flex-none text-slate-500 dark:text-slate-400">{k}</dt>
      <dd className={classNames('min-w-0 break-all text-slate-800 dark:text-slate-100', mono && 'font-mono')}>{v}</dd>
    </div>
  );
}

// ============================================================================= restore dialog
function RestoreDialog({ scan, files, onClose }) {
  const today = new Date().toISOString().slice(0, 10);
  const [destination, setDestination] = useState(`E:\\Recovered\\${today}`);
  const [smartNames, setSmartNames] = useState(true);
  const [preservePaths, setPreservePaths] = useState(true);
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const poll = useRef(null);
  const required = files.reduce((s, f) => s + f.size_bytes, 0);
  const sourceSpecs = useMemo(() => {
    const set = new Set(scan.sources || []);
    files.forEach((f) => { const l = letterOf(f.original_path); if (l) set.add(`${l.toUpperCase()}:`); });
    return [...set];
  }, [scan.sources, files]);

  useEffect(() => () => clearTimeout(poll.current), []);
  useEffect(() => setCheck(null), [destination]);

  async function validate() {
    setChecking(true);
    setError(null);
    try {
      setCheck(await api.validateDestination(destination.trim(), sourceSpecs, required));
    } catch (e) {
      setError(e.message);
    } finally {
      setChecking(false);
    }
  }

  async function start() {
    setError(null);
    try {
      const j = await api.restore(scan.id, { file_ids: files.map((f) => f.id), destination: destination.trim(), use_smart_names: smartNames, preserve_paths: preservePaths });
      setJob(j);
      const tick = async () => {
        try {
          const cur = await api.getRestore(j.id);
          setJob(cur);
          if (['queued', 'running'].includes(cur.status)) poll.current = setTimeout(tick, 800);
        } catch (e) {
          setError(e.message);
        }
      };
      poll.current = setTimeout(tick, 500);
    } catch (e) {
      if (e.detail && e.detail.destination_check) setCheck(e.detail.destination_check);
      setError(e.message);
    }
  }

  const running = job && ['queued', 'running'].includes(job.status);
  const finished = job && !running;
  const lowCount = files.filter((f) => f.confidence === 'low').length;

  return (
    <Modal open onClose={() => !running && onClose(Boolean(finished))} title={`Restore ${files.length.toLocaleString()} file${files.length === 1 ? '' : 's'} (${formatBytes(required)})`}
      footer={job ? (
        <button type="button" className="btn-primary" disabled={running} onClick={() => onClose(true)}>{running ? 'Restoring…' : 'Done'}</button>
      ) : (
        <>
          <button type="button" className="btn-secondary" onClick={() => onClose(false)}>Cancel</button>
          <button type="button" className="btn-secondary" onClick={validate} disabled={checking || !destination.trim()}>{checking && <Spinner />} Check destination</button>
          <button type="button" className="btn-primary" onClick={start} disabled={!check || !check.safe}><Icon name="restore" /> Start restore</button>
        </>
      )}>
      {!job && (
        <div className="space-y-4">
          <Alert tone="info" title="Restore to a different physical drive">
            Saving recovered files to the drive you are recovering from overwrites the very data you are trying to save. Use a USB drive or another internal disk.
          </Alert>
          <div>
            <label className="label" htmlFor="dest">Destination folder</label>
            <input id="dest" className="input font-mono" value={destination} onChange={(e) => setDestination(e.target.value)} />
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">The folder is created if it does not exist. Existing files are never overwritten.</p>
          </div>
          <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
            <input type="checkbox" className="mt-1" checked={smartNames} onChange={(e) => setSmartNames(e.target.checked)} />
            <span>Use smart names for files without an original name<span className="block text-xs text-slate-500 dark:text-slate-400">e.g. carved file → Financial_Report_2026.pdf, Photo_2025-07-08_iPhone_13.jpg</span></span>
          </label>
          <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
            <input type="checkbox" className="mt-1" checked={preservePaths} onChange={(e) => setPreservePaths(e.target.checked)} />
            <span>Recreate the original folder structure<span className="block text-xs text-slate-500 dark:text-slate-400">Carved files go to Carved\&lt;TYPE&gt;\</span></span>
          </label>
          {lowCount > 0 && <Alert tone="warning">{lowCount} selected file(s) have a low recovery chance and may be damaged.</Alert>}
          {check && (
            <Alert tone={check.safe ? 'success' : 'danger'} title={check.safe ? 'Destination is safe' : 'Destination is not safe'}>
              {check.conflicts.map((c) => <div key={c}>{c}</div>)}
              {check.warnings.map((w) => <div key={w} className="text-amber-800 dark:text-amber-200">{w}</div>)}
              {check.safe && check.free_bytes > 0 && <div>{formatBytes(check.free_bytes)} free on {check.destination_volume_root}</div>}
            </Alert>
          )}
          {!check && <p className="text-xs text-slate-500 dark:text-slate-400">Check the destination first - the guard compares volume GUIDs and physical disks with the scanned sources.</p>}
          {error && <Alert tone="danger">{error}</Alert>}
        </div>
      )}
      {job && (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium text-slate-900 dark:text-white">{running ? 'Restoring files…' : `Restore ${job.status}`}</span>
            <span className="tabular-nums text-slate-500 dark:text-slate-400">{job.restored_files} ok · {job.failed_files} failed · {formatBytes(job.bytes_written)}</span>
          </div>
          <ProgressBar value={job.percent} tone={finished ? (job.failed_files ? 'red' : 'green') : 'brand'} />
          {finished && job.status === 'completed' && (
            <Alert tone="success" title="All files restored">
              Saved to <span className="font-mono">{job.destination}</span>. A <span className="font-mono">_recovery_log_*.json</span> with SHA-256 checksums was written next to them.
            </Alert>
          )}
          {job.error && <Alert tone="danger">{job.error}</Alert>}
          <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-800">
            <table className="min-w-full text-xs">
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {job.items.slice(0, 200).map((it) => {
                  const f = files.find((x) => x.id === it.file_id);
                  return (
                    <tr key={it.file_id}>
                      <td className="px-3 py-1.5">
                        {it.status === 'ok' ? <Icon name="check" className="h-4 w-4 text-emerald-500" />
                          : it.status === 'partial' ? <span title="Restored, but some sectors could not be read"><Icon name="warning" className="h-4 w-4 text-amber-500" /></span>
                          : it.status === 'pending' ? <Spinner className="h-3 w-3 text-slate-400" /> : <Icon name="x" className="h-4 w-4 text-red-500" />}
                      </td>
                      <td className="max-w-[16rem] truncate px-2 py-1.5">{f ? f.display_name : it.file_id}</td>
                      <td className="max-w-[18rem] truncate px-2 py-1.5 font-mono text-slate-500" title={it.output_path || it.error || ''}>{it.output_path || it.error || ''}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {error && <Alert tone="danger">{error}</Alert>}
        </div>
      )}
    </Modal>
  );
}

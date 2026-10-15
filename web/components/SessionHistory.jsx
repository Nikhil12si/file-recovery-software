'use client';
/**
 * SessionHistory.jsx - session history, semantic search across all recovered
 * files, PDF reports and candidates.json import (UI for Module 7).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { Alert, Badge, ConfidenceBadge, EmptyState, Icon, Spinner, classNames, formatBytes, formatDate } from './ui';

const STATUS_TONE = { completed: 'green', partial: 'amber', cancelled: 'amber', failed: 'red', running: 'blue', analyzing: 'purple', queued: 'slate' };

export default function SessionHistory() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [reports, setReports] = useState([]);
  const [busy, setBusy] = useState({});
  const [search, setSearch] = useState('');
  const [hits, setHits] = useState(null);
  const [searching, setSearching] = useState(false);
  const fileInput = useRef(null);

  const load = useCallback(() => {
    api.listScans({ page, page_size: 15, q: q || undefined }).then(setData).catch((e) => setError(e.message));
    api.listReports().then(setReports).catch(() => {});
  }, [page, q]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!data || !data.items.some((s) => ['queued', 'running', 'analyzing'].includes(s.status))) return undefined;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [data, load]);

  async function runSearch(e) {
    e.preventDefault();
    if (search.trim().length < 2) return;
    setSearching(true);
    try {
      setHits(await api.search(search.trim()));
    } catch (err) {
      setError(err.message);
    } finally {
      setSearching(false);
    }
  }

  async function report(scan) {
    setBusy((b) => ({ ...b, [scan.id]: 'report' }));
    try {
      const r = await api.createReport(scan.id);
      await api.downloadFile(r.download_url, r.file_name);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [scan.id]: null }));
    }
  }

  async function remove(scan) {
    if (!window.confirm(`Delete "${scan.label}" and its results? Recovered files already saved are not touched.`)) return;
    setBusy((b) => ({ ...b, [scan.id]: 'delete' }));
    try {
      await api.deleteScan(scan.id);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [scan.id]: null }));
    }
  }

  async function importFile(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try {
      await api.importScan(file);
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      e.target.value = '';
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">History &amp; reports</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Every scan is stored with its results, so you can come back later and restore more files.</p>
        </div>
        <div className="flex gap-2">
          <input ref={fileInput} type="file" accept=".json,application/json" className="hidden" onChange={importFile} />
          <button type="button" className="btn-secondary" onClick={() => fileInput.current && fileInput.current.click()}>Import candidates.json</button>
          <Link href="/dashboard" className="btn-primary"><Icon name="search" /> New scan</Link>
        </div>
      </div>
      {error && <Alert tone="danger" onClose={() => setError(null)}>{error}</Alert>}

      {/* semantic search */}
      <div className="card p-4">
        <form onSubmit={runSearch} className="flex flex-wrap gap-2">
          <div className="relative min-w-[260px] flex-1">
            <Icon name="search" className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input className="input pl-9" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder='Search inside recovered documents, e.g. "invoice acme" or "financial report"' />
          </div>
          <button type="submit" className="btn-primary" disabled={searching}>{searching && <Spinner />} Search all sessions</button>
        </form>
        {hits && (
          <div className="mt-4">
            {hits.length === 0 ? <p className="text-sm text-slate-500 dark:text-slate-400">No recovered document mentions “{search}”.</p> : (
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {hits.map((h) => (
                  <li key={h.file.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                    <Icon name="file" className="h-4 w-4 text-slate-400" />
                    <span className="font-medium text-slate-900 dark:text-slate-100">{h.file.display_name}</span>
                    <ConfidenceBadge level={h.file.confidence} score={h.file.confidence_score} />
                    {h.matched_keywords.map((k) => <Badge key={k} tone="purple">{k}</Badge>)}
                    <span className="text-xs text-slate-500 dark:text-slate-400">in {h.scan_label}</span>
                    <Link href={`/dashboard?scan=${h.file.scan_id}`} className="ml-auto text-xs font-medium text-brand-600 dark:text-indigo-400 hover:underline">Open session</Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* sessions */}
      <div className="card">
        <div className="flex items-center gap-3 border-b border-slate-200 dark:border-slate-800 p-4">
          <input className="input max-w-xs" placeholder="Filter sessions…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
          {data && <span className="text-sm text-slate-500 dark:text-slate-400">{data.total} session(s)</span>}
        </div>
        {!data && <div className="flex justify-center py-10"><Spinner className="h-6 w-6 text-slate-400" /></div>}
        {data && data.items.length === 0 && <EmptyState title="No recovery sessions yet" icon="clock">Start a scan from the Recover page.</EmptyState>}
        {data && data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 dark:bg-slate-900/90 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-800">
                <tr>
                  <th className="px-4 py-2.5">Session</th><th className="px-3 py-2.5">Status</th><th className="px-3 py-2.5">Files</th>
                  <th className="px-3 py-2.5">High / Med / Low</th><th className="px-3 py-2.5">Started</th><th className="px-3 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/80">
                {data.items.map((s) => (
                  <tr key={s.id} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50">
                    <td className="max-w-sm px-4 py-2.5">
                      <Link href={`/dashboard?scan=${s.id}`} className="block truncate font-medium text-slate-900 dark:text-slate-100 hover:text-brand-600 dark:hover:text-brand-400">{s.label}</Link>
                      <div className="text-xs text-slate-500 dark:text-slate-400">{s.mode === 'quick' ? 'Quick' : 'Deep'} · {s.target_scope === 'all' ? 'All drives' : s.target_scope === 'folder' ? s.folder_path : s.sources.join(', ')}</div>
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge tone={STATUS_TONE[s.status]}>{s.status}</Badge>
                      {['running', 'analyzing'].includes(s.status) && <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{Number(s.progress.overall_percent || 0).toFixed(0)}%</div>}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{(s.totals.files || 0).toLocaleString()}<div className="text-xs text-slate-500 dark:text-slate-400">{formatBytes(s.totals.bytes)}</div></td>
                    <td className="px-3 py-2.5 text-xs tabular-nums">
                      <span className="text-emerald-600 dark:text-emerald-400">{s.totals.high}</span> / <span className="text-amber-600 dark:text-amber-400">{s.totals.medium}</span> / <span className="text-red-600 dark:text-red-400">{s.totals.low}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-xs text-slate-500 dark:text-slate-400">{formatDate(s.started_at || s.created_at)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right">
                      <Link href={`/dashboard?scan=${s.id}`} className="btn-ghost px-2 py-1 text-xs">Open</Link>
                      <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={Boolean(busy[s.id]) || ['queued', 'running', 'analyzing'].includes(s.status)}
                        onClick={() => report(s)}>{busy[s.id] === 'report' ? <Spinner className="h-3 w-3" /> : <Icon name="download" />} Report</button>
                      <button type="button" className="btn-ghost px-2 py-1 text-xs text-red-600 dark:text-red-400" disabled={Boolean(busy[s.id]) || ['queued', 'running', 'analyzing'].includes(s.status)}
                        onClick={() => remove(s)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-end gap-2 border-t border-slate-200 dark:border-slate-800 p-3 text-sm">
            <button type="button" className="btn-secondary px-3 py-1" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="text-slate-600 dark:text-slate-400">Page {page} / {data.total_pages}</span>
            <button type="button" className="btn-secondary px-3 py-1" disabled={page >= data.total_pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </div>

      {/* reports */}
      <div className="card p-4">
        <h2 className="font-semibold text-slate-900 dark:text-white">Generated reports</h2>
        {reports.length === 0 ? <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">No PDF reports yet. Use “Report” on a finished session.</p> : (
          <ul className="mt-2 divide-y divide-slate-100 dark:divide-slate-800">
            {reports.map((r) => (
              <li key={r.id} className={classNames('flex flex-wrap items-center gap-3 py-2 text-sm')}>
                <Icon name="file" className="h-4 w-4 text-red-500" />
                <span className="font-medium text-slate-900 dark:text-slate-100">{r.title}</span>
                <span className="text-xs text-slate-500 dark:text-slate-400">{formatDate(r.created_at)} · {formatBytes(r.size_bytes)}</span>
                <button type="button" className="ml-auto text-xs font-medium text-brand-600 dark:text-indigo-400 hover:underline"
                  onClick={() => api.downloadFile(r.download_url, r.file_name).catch((e) => setError(e.message))}>Download PDF</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

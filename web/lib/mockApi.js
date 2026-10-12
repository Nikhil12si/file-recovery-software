/**
 * mockApi.js - in-browser implementation of the REST contract (Track C, Day 1).
 *
 * Mirrors lib/api.js function-for-function and returns objects shaped like
 * contracts/mocks/rest/*.json, including a simulated scan with live progress,
 * restore jobs, reports and search. Enabled with NEXT_PUBLIC_USE_MOCKS=true.
 */
import { MOCK_DRIVES } from './mockData';

const store = { user: null, scans: new Map(), files: new Map(), restores: new Map(), reports: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
const uid = () => (typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID()
  : 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16)));

class MockError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : detail.message);
    this.status = status;
    this.detail = detail;
  }
}

// ----------------------------------------------------------------------------- file templates
const TEMPLATES = [
  ['Q3_Financial_Report.pdf', 'Users\\Alice\\Documents\\Finance', 'pdf', 482113, 'high', 98, ['financial report', 'revenue', 'quarterly'], 'Quarterly Financial Report 2026\nQuarterly revenue grew twelve percent. The financial statements show strong operating margin...', 'Quarterly_Financial_Report_2026.pdf'],
  ['IMG_2041.JPG', 'Users\\Alice\\Pictures\\Holiday 2025', 'jpg', 3245112, 'high', 100, [], null, 'Photo_2025-07-08_iPhone_13.jpg'],
  ['IMG_2042.JPG', 'Users\\Alice\\Pictures\\Holiday 2025', 'jpg', 2988110, 'high', 95, [], null, 'Photo_2025-07-08_iPhone_13.jpg'],
  ['IMG_2046.JPG', 'Users\\Alice\\Pictures\\Holiday 2025', 'jpg', 3011987, 'low', 12, [], null, null],
  ['Board_Meeting_Minutes.docx', 'Users\\Alice\\Documents', 'docx', 38211, 'high', 100, ['board meeting', 'meeting minutes', 'budget'], 'Board Meeting Minutes October 2025\nThe board approved the annual budget...', 'Board_Meeting_Minutes_October_2025.docx'],
  ['Lease_Agreement_12B.docx', 'Users\\Alice\\Desktop', 'docx', 29004, 'medium', 61, ['lease agreement', 'tenant', 'deposit'], 'Lease Agreement Apartment 12B\nThis lease agreement between landlord and tenant...', 'Lease_Agreement_Apartment_12B_2025.docx'],
  ['Employee_Handbook.pdf', 'Shared\\HR', 'pdf', 1203320, 'high', 92, ['employee handbook', 'leave policy', 'benefits'], 'Employee Handbook 2025\nThis employee handbook explains leave policy, remote work policy...', 'Employee_Handbook_2025.pdf'],
  ['invoice_4411.pdf', 'Users\\Alice\\Documents\\Finance', 'pdf', 88213, 'medium', 58, ['invoice', 'acme corporation', 'payment terms'], 'Invoice 4411 Acme Corporation\nInvoice for consulting services delivered...', 'Invoice_4411_Acme_Corporation_2025.pdf'],
  ['wifi_notes.txt', 'Users\\Alice\\Desktop', 'txt', 612, 'high', 90, ['router', 'guest network', 'firmware'], 'Wi-Fi setup notes\nRouter admin password changed. Guest network enabled.', 'Wifi_Setup_Notes_2025.txt'],
  ['Screenshot 1.png', 'Users\\Alice\\Pictures', 'png', 145522, 'high', 100, [], null, null],
  ['thesis_draft_v3.pdf', 'Projects\\Thesis', 'pdf', 2210443, 'low', 30, ['disk forensics', 'master file table', 'carving'], null, 'Thesis_Draft_Disk_Forensics_2026.pdf'],
  ['budget_2026.xlsx', 'Users\\Alice\\Documents\\Finance', 'xlsx', 64321, 'medium', 70, ['budget', 'forecast'], 'Budget forecast 2026 Item Cost', 'Budget_Forecast_2026.xlsx'],
];
const REASONS = {
  high: ['File signature intact', 'All clusters still free - not overwritten', 'Content verified: decodes completely'],
  medium: ['File signature intact', '25% of the file\'s clusters are now used by other files (overwritten)', '2 fragments (locations known from MFT data runs)'],
  low: ['File header does not match the extension - the first clusters were probably overwritten', '100% of the file\'s clusters are now used by other files (overwritten)', 'Content check failed: decoder error'],
};
const COLORS = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6'];

function thumbSvg(name, ext, i) {
  const c1 = COLORS[i % COLORS.length];
  const c2 = COLORS[(i + 3) % COLORS.length];
  const label = ext === 'pdf' ? 'PDF page 1' : name;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1">`
    + `<stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs>`
    + `<rect width="320" height="220" fill="url(#g)"/><circle cx="250" cy="60" r="28" fill="#fde68a" opacity=".9"/>`
    + `<path d="M0 220 L90 120 L160 190 L220 140 L320 220Z" fill="#0f172a" opacity=".35"/>`
    + `<text x="12" y="208" font-family="Segoe UI,Arial" font-size="13" fill="white">${label.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function buildFiles(scan) {
  const deep = scan.mode === 'deep';
  const letter = (scan.sources[0] || 'D:').match(/^[A-Za-z]:/) ? scan.sources[0].slice(0, 2).toUpperCase() : 'D:';
  const types = scan.file_types || [];
  const out = [];
  const count = deep ? 34 : 46;
  let lba = 2048000;
  for (let i = 0; i < count; i += 1) {
    const t = TEMPLATES[i % TEMPLATES.length];
    const [name, dir, ext, size, conf, score, keywords, text, suggested] = t;
    if (types.length && !types.includes(ext)) continue;
    lba += Math.floor(size / 512) + 64 + (i % 9 === 0 ? 900000 : 0);
    const folder = scan.target_scope === 'folder' && scan.folder_path ? scan.folder_path.replace(/\\$/, '') : `${letter}\\${dir}`;
    const fileName = deep ? null : (i >= TEMPLATES.length ? name.replace(/(\.\w+)$/, ` (${Math.floor(i / TEMPLATES.length)})$1`) : name);
    const isImage = ext === 'jpg' || ext === 'png';
    const preview = conf === 'low' && isImage ? 'none' : isImage || ext === 'pdf' ? 'image' : text ? 'text' : 'none';
    out.push({
      id: uid(), scan_id: scan.id, candidate_id: `${deep ? 'pd1-carve' : `vol-${letter[0]}-mft`}-${40000 + i}`,
      method: deep ? 'carve' : 'mft', file_name: fileName, suggested_name: suggested,
      display_name: fileName || suggested || `carved_${lba}.${ext}`, original_path: fileName ? `${folder}\\${fileName}` : null,
      extension: ext, mime_type: { pdf: 'application/pdf', jpg: 'image/jpeg', png: 'image/png', txt: 'text/plain' }[ext] || 'application/octet-stream',
      size_bytes: size + i * 37, confidence: conf, confidence_score: score,
      confidence_reasons: deep ? ['Recovered by signature carving (original name and folder are lost)', ...REASONS[conf].slice(0, 2)] : REASONS[conf],
      cluster_id: null, lba, absolute_lba: lba + 2048, fragment_count: conf === 'medium' ? 2 : 1,
      timestamps: deep ? { created: null, modified: null, mft_modified: null, accessed: null }
        : { created: '2025-03-02T09:12:00Z', modified: `2025-10-${String((i % 27) + 1).padStart(2, '0')}T17:45:00Z`, mft_modified: '2026-01-07T11:04:00Z', accessed: '2026-01-07T08:00:00Z' },
      keywords, has_thumbnail: preview === 'image', preview_kind: preview, restored: false, restored_path: null,
      source_id: deep ? 'pd1' : `vol-${letter[0]}`,
      _text: text, _thumb: preview === 'image' ? thumbSvg(fileName || suggested || name, ext, i) : null,
    });
  }
  // Two deletion events by LBA proximity.
  const clusters = [];
  const sorted = [...out].sort((a, b) => a.absolute_lba - b.absolute_lba);
  let group = [];
  const flush = () => {
    if (group.length < 2) { group = []; return; }
    const id = `${scan.id}:c${clusters.length + 1}`;
    group.forEach((f) => { f.cluster_id = id; });
    const exts = {};
    group.forEach((f) => { exts[f.extension] = (exts[f.extension] || 0) + 1; });
    const dominant = Object.entries(exts).sort((a, b) => b[1] - a[1])[0][0];
    clusters.push({ id, scan_id: scan.id, label: `Deletion event #${clusters.length + 1}: ${group.length} files (mostly .${dominant})`,
      file_count: group.length, total_bytes: group.reduce((s, f) => s + f.size_bytes, 0), lba_start: group[0].absolute_lba,
      lba_end: group[group.length - 1].absolute_lba + 100, dominant_extension: dominant, time_start: deep ? null : '2026-01-07T11:04:00Z',
      time_end: deep ? null : '2026-01-07T11:09:00Z' });
    group = [];
  };
  sorted.forEach((f, i) => {
    if (i > 0 && f.absolute_lba - sorted[i - 1].absolute_lba > 400000) flush();
    group.push(f);
  });
  flush();
  return { files: out, clusters };
}

function publicFile(f) {
  const { _text, _thumb, ...rest } = f;
  return rest;
}

function progressTemplate() {
  return { phase: 'queued', percent: 0, overall_percent: 0, bytes_done: 0, bytes_total: 0, unit: 'bytes', rate_mb_s: 0, eta_seconds: 0,
    candidates_found: 0, source_index: 0, source_count: 1, current_source: null, read_errors: 0, message: 'Waiting for a free scan slot',
    updated_at: now(), log: [] };
}

function simulateScan(scan) {
  const total = scan.mode === 'deep' ? 1000204886016 : 250000;
  const durationMs = scan.mode === 'deep' ? 14000 : 8000;
  const started = Date.now();
  scan.status = 'running';
  scan.started_at = now();
  scan.progress.log.push({ ts: now(), level: 'info', message: `Scanning ${scan.sources.join(', ') || 'all drives'}` });
  const timer = setInterval(() => {
    if (scan._cancel) {
      clearInterval(timer);
      scan.status = 'cancelled';
      scan.finished_at = now();
      scan.progress = { ...scan.progress, phase: 'done', message: 'Cancelled by user' };
      return;
    }
    const frac = Math.min(1, (Date.now() - started) / durationMs);
    const p = scan.progress;
    if (frac < 0.9) {
      p.phase = scan.mode === 'deep' ? 'carve' : frac < 0.6 ? 'mft' : 'verify';
      p.unit = scan.mode === 'deep' ? 'bytes' : 'records';
      p.percent = Math.round((frac / 0.9) * 10000) / 100;
      p.overall_percent = Math.round(frac * 10000) / 100;
      p.bytes_done = Math.floor((total * frac) / 0.9);
      p.bytes_total = total;
      p.rate_mb_s = scan.mode === 'deep' ? 141.7 : 0;
      p.eta_seconds = Math.round(((durationMs * (0.9 - frac)) / 1000));
      p.candidates_found = Math.floor(40 * frac);
      p.current_source = scan.mode === 'deep' ? 'pd1' : 'vol-D';
      p.message = `${p.phase === 'carve' ? 'Carving sectors' : p.phase === 'mft' ? 'Reading $MFT records' : 'Verifying headers'}`;
    } else if (frac < 1) {
      p.phase = 'analysis';
      scan.status = 'analyzing';
      p.overall_percent = Math.round(frac * 10000) / 100;
      p.message = 'Scoring confidence and generating previews';
    } else {
      clearInterval(timer);
      const { files, clusters } = buildFiles(scan);
      store.files.set(scan.id, { files, clusters });
      scan.status = 'completed';
      scan.finished_at = now();
      scan.totals = { files: files.length, bytes: files.reduce((s, f) => s + f.size_bytes, 0), high: files.filter((f) => f.confidence === 'high').length,
        medium: files.filter((f) => f.confidence === 'medium').length, low: files.filter((f) => f.confidence === 'low').length, clusters: clusters.length };
      scan.health_warnings = scan.mode === 'deep'
        ? ['Disk 1 (WDC WD20EZRZ-00Z5HB0): 3 unreadable sector(s) were skipped. The drive may be failing - stop using it and create a sector-by-sector image before further recovery attempts.']
        : [];
      scan.progress = { ...p, phase: 'done', percent: 100, overall_percent: 100, candidates_found: files.length,
        message: `${files.length} files found (${scan.totals.high} high confidence)`, updated_at: now() };
      scan.progress.log.push({ ts: now(), level: 'info', message: `Engine finished: completed - ${files.length} candidates` });
    }
    p.updated_at = now();
  }, 400);
}

function scanOut(s) {
  const { _cancel, ...rest } = s;
  return JSON.parse(JSON.stringify(rest));
}

function letterOf(p) {
  const m = /^(?:\\\\[.?]\\)?([A-Za-z]):/.exec(p || '');
  return m ? `${m[1].toUpperCase()}:` : null;
}

function diskOf(letter) {
  const v = MOCK_DRIVES.volumes.find((x) => x.letter === letter);
  return v ? v.physical_drive_index : null;
}

function validate(destination, sources, required = 0) {
  const conflicts = [];
  const warnings = [];
  let code = 'OK';
  const dl = letterOf(destination);
  const dd = dl ? diskOf(dl) : null;
  if (!dl) {
    code = 'INVALID_PATH';
    conflicts.push('Use an absolute Windows path such as E:\\Recovered.');
  }
  sources.forEach((s) => {
    const sl = letterOf(s);
    const pm = /PhysicalDrive(\d+)(@p\d+)?/i.exec(s);
    if (sl && dl && sl === dl) {
      code = 'SAME_VOLUME';
      conflicts.push(`Destination ${dl}\\ is the same volume as source ${sl}\\. Writing there would overwrite the deleted data you are recovering.`);
    } else if (pm && dd !== null && Number(pm[1]) === dd && !pm[2]) {
      code = 'SAME_PHYSICAL_DISK';
      conflicts.push(`Destination lives on PhysicalDrive${dd}, which is being scanned sector-by-sector.`);
    } else if (sl && dd !== null && diskOf(sl) === dd) {
      warnings.push(`Destination is a different partition on the same physical disk as ${sl}\\. A physically separate drive is recommended.`);
    }
  });
  const vol = MOCK_DRIVES.volumes.find((v) => v.letter === dl);
  return { safe: conflicts.length === 0, reason_code: conflicts.length ? code : 'OK', destination, destination_volume_root: dl ? `${dl}\\` : null,
    destination_volume_guid: vol ? vol.volume_guid_path : null, destination_physical_drives: dd !== null ? [dd] : [],
    free_bytes: vol ? vol.free_bytes : 0, required_bytes: required, conflicts, warnings };
}

const MINI_PDF = '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj '
  + '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj '
  + '4 0 obj<</Length 63>>stream\nBT /F1 20 Tf 60 780 Td (Mock recovery report - simulation mode) Tj ET\nendstream endobj '
  + '5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF';

function requireScan(id) {
  const s = store.scans.get(id);
  if (!s) throw new MockError(404, 'Scan session not found');
  return s;
}

export const mockApi = {
  async register(email, password, fullName) {
    await sleep(250);
    if (!password || password.length < 8) throw new MockError(422, 'password: String should have at least 8 characters');
    store.user = { id: uid(), email, full_name: fullName || null, role: 'admin', created_at: now(), last_login_at: now() };
    return { access_token: 'mock-token', token_type: 'bearer', expires_at: new Date(Date.now() + 8 * 3600e3).toISOString(), user: store.user };
  },
  async login(email, password) {
    await sleep(250);
    if (!password) throw new MockError(401, 'Invalid email or password');
    store.user = store.user || { id: uid(), email, full_name: 'Demo User', role: 'admin', created_at: now(), last_login_at: now() };
    return { access_token: 'mock-token', token_type: 'bearer', expires_at: new Date(Date.now() + 8 * 3600e3).toISOString(), user: store.user };
  },
  async logout() { return null; },
  async me() { return store.user || { id: 'demo', email: 'demo@example.com', full_name: 'Demo User', role: 'admin', created_at: now(), last_login_at: now() }; },
  async health() {
    return { status: 'ok', version: '1.0.0', engine: { available: true, mock: true, path: null, version: '1.0.0-mock' }, database: { ok: true, dialect: 'sqlite' } };
  },
  async systemStatus() {
    return { platform: 'Browser simulation', elevated: true, elevation_type: 'full', engine_available: true, mock_mode: true,
      warnings: ['Simulation mode: the web app is running on built-in mock data (NEXT_PUBLIC_USE_MOCKS=true). No disks are read.'] };
  },
  async drives() { await sleep(300); return JSON.parse(JSON.stringify(MOCK_DRIVES)); },
  async validateDestination(destination, sources, requiredBytes = 0) { await sleep(200); return validate(destination, sources, requiredBytes); },
  async createScan(req) {
    await sleep(300);
    const mode = req.mode || ({ accidental_delete: 'quick', formatted_drive: 'deep', corrupted_partition: 'deep' }[req.scenario] || 'quick');
    const scan = {
      id: uid(), label: req.label || `${(req.scenario || 'custom').replace('_', ' ')} - ${(req.sources || []).join(', ') || req.folder_path || 'All drives'} (${mode})`,
      mode, target_scope: req.target_scope, sources: req.sources || [], folder_path: req.folder_path || null, scenario: req.scenario || 'custom',
      file_types: req.file_types || [], status: 'queued', progress: progressTemplate(),
      totals: { files: 0, bytes: 0, high: 0, medium: 0, low: 0, clusters: 0 }, health_warnings: [], error: null, created_at: now(),
      started_at: null, finished_at: null,
    };
    store.scans.set(scan.id, scan);
    setTimeout(() => simulateScan(scan), 600);
    return scanOut(scan);
  },
  async importScan(file, label) { return mockApi.createScan({ label: label || `Imported ${file.name}`, mode: 'quick', target_scope: 'drive', sources: ['D:'] }); },
  async listScans({ page = 1, page_size = 20, q } = {}) {
    let items = [...store.scans.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
    if (q) items = items.filter((s) => `${s.label} ${s.status} ${s.mode}`.toLowerCase().includes(q.toLowerCase()));
    const total = items.length;
    return { items: items.slice((page - 1) * page_size, page * page_size).map(scanOut), page, page_size, total, total_pages: Math.ceil(total / page_size) };
  },
  async getScan(id) { return scanOut(requireScan(id)); },
  async getProgress(id) {
    const s = requireScan(id);
    return { status: s.status, progress: JSON.parse(JSON.stringify(s.progress)), totals: s.totals, error: s.error };
  },
  async cancelScan(id) {
    const s = requireScan(id);
    if (!['queued', 'running', 'analyzing'].includes(s.status)) throw new MockError(409, `Scan is not running (status: ${s.status})`);
    s._cancel = true;
    return scanOut(s);
  },
  async deleteScan(id) { store.scans.delete(id); store.files.delete(id); return null; },
  async listFiles(id, params = {}) {
    await sleep(120);
    const data = store.files.get(id) || { files: [] };
    const { page = 1, page_size = 50, extension, confidence, q, cluster_id: clusterId, method, sort = 'confidence', order = 'desc' } = params;
    const exts = (Array.isArray(extension) ? extension : String(extension || '').split(',')).filter(Boolean);
    const confs = (Array.isArray(confidence) ? confidence : String(confidence || '').split(',')).filter(Boolean);
    let items = data.files.filter((f) => (!exts.length || exts.includes(f.extension))
      && (!confs.length || confs.includes(f.confidence))
      && (!clusterId || f.cluster_id === clusterId) && (!method || f.method === method)
      && (!q || `${f.display_name} ${f.original_path || ''}`.toLowerCase().includes(q.toLowerCase())));
    const key = { name: 'display_name', size: 'size_bytes', confidence: 'confidence_score', lba: 'absolute_lba', extension: 'extension', modified: 'absolute_lba' }[sort] || 'confidence_score';
    items = [...items].sort((a, b) => (a[key] > b[key] ? 1 : a[key] < b[key] ? -1 : 0) * (order === 'asc' ? 1 : -1));
    const total = items.length;
    return { items: items.slice((page - 1) * page_size, page * page_size).map(publicFile), page, page_size, total, total_pages: Math.ceil(total / page_size) };
  },
  async getFile(id, fid) {
    const f = (store.files.get(id)?.files || []).find((x) => x.id === fid);
    if (!f) throw new MockError(404, 'File not found');
    return { ...publicFile(f), sha256: null, integrity: { header_check: f.confidence === 'low' ? 'mismatch' : 'match' }, extents: [], notes: [], mft: null };
  },
  async getPreview(id, fid) {
    await sleep(150);
    const f = (store.files.get(id)?.files || []).find((x) => x.id === fid);
    if (!f) throw new MockError(404, 'File not found');
    return { file_id: fid, kind: f.preview_kind, thumbnail_url: f._thumb, text_excerpt: f._text, keywords: f.keywords,
      message: f.preview_kind === 'none' ? 'No preview: the content could not be decoded (it may be overwritten).' : null };
  },
  async clusters(id) { return (store.files.get(id)?.clusters || []).map((c) => ({ ...c })); },
  async stats(id) {
    const files = store.files.get(id)?.files || [];
    const by = (k) => files.reduce((acc, f) => ({ ...acc, [f[k]]: (acc[f[k]] || 0) + 1 }), {});
    return { by_extension: by('extension'), by_confidence: by('confidence'), by_method: by('method'), total_files: files.length,
      total_bytes: files.reduce((s, f) => s + f.size_bytes, 0) };
  },
  async search(q) {
    await sleep(150);
    const term = q.toLowerCase();
    const hits = [];
    store.files.forEach(({ files }, scanId) => files.forEach((f) => {
      const hay = `${f.keywords.join(' ')} ${f.display_name} ${f.suggested_name || ''} ${f._text || ''}`.toLowerCase();
      if (hay.includes(term)) {
        hits.push({ file: publicFile(f), scan_label: store.scans.get(scanId)?.label || '', rank: 1, matched_keywords: f.keywords.filter((k) => k.includes(term)) });
      }
    }));
    return hits.slice(0, 50);
  },
  async restore(id, req) {
    await sleep(300);
    const scan = requireScan(id);
    const check = validate(req.destination, [...scan.sources, ...scan.sources.map(letterOf).filter(Boolean)],
      0);
    if (!check.safe) throw new MockError(400, { message: 'Unsafe destination', destination_check: check });
    const files = (store.files.get(id)?.files || []).filter((f) => req.file_ids.includes(f.id));
    const job = { id: uid(), scan_id: id, status: 'running', destination: req.destination, total_files: files.length, restored_files: 0,
      failed_files: 0, bytes_written: 0, percent: 0, items: files.map((f) => ({ file_id: f.id, status: 'pending', output_path: null, bytes_written: 0, sha256: null, error: null })),
      destination_check: check, error: null, created_at: now(), finished_at: null };
    store.restores.set(job.id, job);
    let i = 0;
    const timer = setInterval(() => {
      if (i >= files.length) {
        clearInterval(timer);
        job.status = job.failed_files ? 'partial' : 'completed';
        job.percent = 100;
        job.finished_at = now();
        return;
      }
      const f = files[i];
      const item = job.items[i];
      const ok = f.confidence !== 'low' || i % 2 === 0;
      // Same naming and folder rules as backend/app/jobs.py (output_name / relative_dir).
      const name = f.file_name || (req.use_smart_names && f.suggested_name) || `carved_${f.absolute_lba}.${f.extension || 'bin'}`;
      const relDir = (() => {
        if (!req.preserve_paths) return '';
        if (!f.original_path) return `Carved\\${(f.extension || 'other').toUpperCase()}\\`;
        const parts = f.original_path.split(/[\\/]+/).filter(Boolean);
        const root = /^[A-Za-z]:$/.test(parts[0]) ? `${parts[0][0]}_drive` : parts[0];
        return `${[root, ...parts.slice(1, -1)].join('\\')}\\`;
      })();
      Object.assign(item, ok
        ? { status: 'ok', output_path: `${req.destination.replace(/\\$/, '')}\\${relDir}${name}`, bytes_written: f.size_bytes,
          sha256: Array.from({ length: 64 }, () => '0123456789abcdef'[Math.floor(Math.random() * 16)]).join('') }
        : { status: 'failed', error: 'Read error: unreadable sector' });
      if (ok) { job.restored_files += 1; job.bytes_written += f.size_bytes; f.restored = true; f.restored_path = item.output_path; } else job.failed_files += 1;
      i += 1;
      job.percent = Math.round((i / files.length) * 100);
    }, 250);
    return JSON.parse(JSON.stringify(job));
  },
  async getRestore(jobId) {
    const j = store.restores.get(jobId);
    if (!j) throw new MockError(404, 'Restore job not found');
    return JSON.parse(JSON.stringify(j));
  },
  async cancelRestore(jobId) { return mockApi.getRestore(jobId); },
  async listRestores(id) { return [...store.restores.values()].filter((j) => j.scan_id === id); },
  async createReport(id, title) {
    await sleep(500);
    const scan = requireScan(id);
    const r = { id: uid(), scan_id: id, title: title || `Recovery Report - ${scan.label}`, file_name: `recovery_report_${id.slice(0, 8)}.pdf`,
      size_bytes: MINI_PDF.length, created_at: now(), download_url: `mock://report/${id}` };
    store.reports.unshift(r);
    return r;
  },
  async listReports(scanId) { return store.reports.filter((r) => !scanId || r.scan_id === scanId); },
  async fetchBlobUrl(path) {
    if (path.startsWith('data:')) return path;
    for (const { files } of store.files.values()) {
      const f = files.find((x) => path.includes(x.id));
      if (f && f._thumb) return f._thumb;
    }
    throw new MockError(404, 'No thumbnail');
  },
  async downloadFile(path, fileName) {
    const blob = new Blob([MINI_PDF], { type: 'application/pdf' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName || 'report.pdf';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  },
};

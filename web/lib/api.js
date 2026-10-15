/**
 * api.js - typed-by-contract client for the FastAPI recovery service.
 *
 * Every function maps 1:1 to a route in contracts/README.md and returns the
 * JSON shape defined in contracts/schemas/rest-api.schema.json.
 * With NEXT_PUBLIC_USE_MOCKS=true the same functions are served by
 * lib/mockApi.js so the UI can be built and demoed without any backend.
 */
import { mockApi } from './mockApi';

const DEFAULT_AGENT_BASE = 'http://127.0.0.1:8000';
const TOKEN_KEY = 'frs_token';
const USER_KEY = 'frs_user';

export class ApiError extends Error {
  constructor(status, detail, code) {
    const message = typeof detail === 'string' ? detail : (detail && (detail.message || detail.detail)) || `HTTP ${status}`;
    super(message);
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

// ----------------------------------------------------------------------------- dynamic mode & URL
export function getAgentUrl() {
  if (typeof window !== 'undefined') {
    const stored = window.localStorage.getItem('frs_agent_url');
    if (stored) return stored.trim().replace(/\/+$/, '');
  }
  const envBase = (process.env.NEXT_PUBLIC_API_BASE || '').trim().replace(/\/+$/, '');
  if (envBase) return envBase;
  return DEFAULT_AGENT_BASE;
}

export function isLiveMode() {
  if (typeof window === 'undefined') {
    return process.env.NEXT_PUBLIC_USE_MOCKS === 'false';
  }
  const mode = window.localStorage.getItem('frs_mode');
  if (mode === 'live') return true;
  if (mode === 'mock') return false;
  if (process.env.NEXT_PUBLIC_USE_MOCKS === 'false') return true;
  return false;
}

export function setLiveMode(live) {
  if (typeof window === 'undefined') return;
  const targetMode = live ? 'live' : 'mock';
  window.localStorage.setItem('frs_mode', targetMode);
  window.dispatchEvent(new CustomEvent('frs_mode_change', { detail: { mode: targetMode, live: Boolean(live) } }));
}

export async function checkAgentHealth(targetUrl = null) {
  const base = (targetUrl || getAgentUrl() || DEFAULT_AGENT_BASE).trim().replace(/\/+$/, '');
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);
    const res = await fetch(`${base}/api/health`, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    clearTimeout(timeout);
    if (!res.ok) return false;
    const data = await res.json();
    return data && data.status === 'ok';
  } catch {
    return false;
  }
}

// ----------------------------------------------------------------------------- session storage
export const session = {
  token() {
    if (typeof window === 'undefined') return null;
    try { return window.localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  user() {
    if (typeof window === 'undefined') return null;
    try { return JSON.parse(window.localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  },
  save(auth) {
    try {
      window.localStorage.setItem(TOKEN_KEY, auth.access_token);
      window.localStorage.setItem(USER_KEY, JSON.stringify(auth.user));
    } catch { /* storage disabled: session lasts for this tab only */ }
  },
  clear() {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
      window.localStorage.removeItem(USER_KEY);
    } catch { /* ignore */ }
  },
};

let unauthorizedHandler = null;
export function onUnauthorized(fn) { unauthorizedHandler = fn; }

async function request(method, path, { body, query, form, raw } = {}) {
  const base = getAgentUrl();
  let urlStr;
  if (/^https?:\/\//i.test(path)) {
    urlStr = path;
  } else {
    const baseClean = (base || '').replace(/\/+$/, '');
    const pathClean = path.startsWith('/') ? path : `/${path}`;
    urlStr = baseClean
      ? `${baseClean}${pathClean}`
      : (typeof window !== 'undefined' ? `${window.location.origin}${pathClean}` : `http://localhost${pathClean}`);
  }
  const url = new URL(urlStr);
  if (query) {
    Object.entries(query).forEach(([k, v]) => {
      if (v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)) return;
      url.searchParams.set(k, Array.isArray(v) ? v.join(',') : String(v));
    });
  }
  const headers = {};
  const token = session.token();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url.toString(), { method, headers, body: payload, cache: 'no-store' });
  } catch (e) {
    throw new ApiError(
      0,
      `Cannot reach Windows Recovery Agent at ${base}. Ensure start-agent.bat is running on your PC.`,
      'NETWORK'
    );
  }
  if (res.status === 401 && unauthorizedHandler && !path.startsWith('/api/auth/login')) unauthorizedHandler();
  if (!res.ok) {
    let detail = res.statusText;
    let code;
    try {
      const j = await res.json();
      detail = j.detail ?? detail;
      code = j.code;
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail, code);
  }
  if (raw) return res;
  if (res.status === 204) return null;
  return res.json();
}

const enc = encodeURIComponent;

const realApi = {
  // ---- auth (Module 1 with 2FA)
  register: (email, password, fullName, phoneNumber) =>
    request('POST', '/api/auth/register', { body: { email, password, full_name: fullName || null, phone_number: phoneNumber || null } }),
  login: (email, password, phoneNumber) =>
    request('POST', '/api/auth/login', { body: { email, password, phone_number: phoneNumber || null } }),
  verify2FA: (verificationId, code) => request('POST', '/api/auth/verify-2fa', { body: { verification_id: verificationId, code } }),
  resend2FA: (verificationId, phoneNumber) =>
    request('POST', '/api/auth/resend-2fa', { body: { verification_id: verificationId, phone_number: phoneNumber || null } }),
  logout: () => request('POST', '/api/auth/logout'),
  me: () => request('GET', '/api/auth/me'),
  // ---- system
  health: () => request('GET', '/api/health'),
  systemStatus: () => request('GET', '/api/system/status'),
  drives: (refresh = false) => request('GET', '/api/drives', { query: { refresh: refresh || undefined } }),
  validateDestination: (destination, sources, requiredBytes = 0) =>
    request('POST', '/api/destinations/validate', { body: { destination, sources, required_bytes: requiredBytes } }),
  // ---- scans
  createScan: (req) => request('POST', '/api/scans', { body: req }),
  importScan: (file, label) => {
    const fd = new FormData();
    fd.append('file', file);
    return request('POST', '/api/scans/import', { form: fd, query: { label } });
  },
  listScans: (params = {}) => request('GET', '/api/scans', { query: params }),
  getScan: (id) => request('GET', `/api/scans/${enc(id)}`),
  getProgress: (id) => request('GET', `/api/scans/${enc(id)}/progress`),
  cancelScan: (id) => request('POST', `/api/scans/${enc(id)}/cancel`),
  deleteScan: (id) => request('DELETE', `/api/scans/${enc(id)}`),
  // ---- results (Module 6)
  listFiles: (id, params = {}) => request('GET', `/api/scans/${enc(id)}/files`, { query: params }),
  getFile: (id, fid) => request('GET', `/api/scans/${enc(id)}/files/${enc(fid)}`),
  getPreview: (id, fid) => request('GET', `/api/scans/${enc(id)}/files/${enc(fid)}/preview`),
  clusters: (id) => request('GET', `/api/scans/${enc(id)}/clusters`),
  stats: (id) => request('GET', `/api/scans/${enc(id)}/stats`),
  search: (q, limit = 50) => request('GET', '/api/search', { query: { q, limit } }),
  // ---- restore
  restore: (id, req) => request('POST', `/api/scans/${enc(id)}/restore`, { body: req }),
  getRestore: (jobId) => request('GET', `/api/restores/${enc(jobId)}`),
  cancelRestore: (jobId) => request('POST', `/api/restores/${enc(jobId)}/cancel`),
  listRestores: (id) => request('GET', `/api/scans/${enc(id)}/restores`),
  // ---- reports (Module 7)
  createReport: (id, title) => request('POST', `/api/scans/${enc(id)}/reports`, { body: title ? { title } : {} }),
  listReports: (scanId) => request('GET', '/api/reports', { query: { scan_id: scanId } }),
  // ---- binary helpers (auth header => blob => object URL)
  fetchBlobUrl: async (path) => {
    const res = await request('GET', path, { raw: true });
    return URL.createObjectURL(await res.blob());
  },
  downloadFile: async (path, fileName) => {
    const res = await request('GET', path, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName || 'download';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  },
};

// Dynamic proxy that routes calls to realApi when in Live Mode and mockApi when in Mock Mode
export const api = new Proxy({}, {
  get(target, prop) {
    const live = isLiveMode();
    const activeApi = live ? realApi : mockApi;
    const fn = activeApi[prop] || mockApi[prop];
    return typeof fn === 'function' ? fn.bind(activeApi) : fn;
  }
});

export const usingMocks = false;
export const usesLocalAgent = true;
export const agentAddress = DEFAULT_AGENT_BASE;

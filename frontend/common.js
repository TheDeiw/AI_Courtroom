// common.js — owner: C
export const MOCK = new URLSearchParams(location.search).has('mock');

const MOCKS = [
  [/^GET \/api\/messages/, 'messages'],
  [/^GET \/api\/incidents\?/, 'incidents'],
  [/^GET \/api\/incidents\/\d+/, 'incident_single'],
  [/^GET \/api\/channels/, 'channel'],
  [/^GET \/api\/appeals\?status=court_queue/, 'appeals_court'],
  [/^GET \/api\/appeals\?status=support_queue/, 'appeals_support'],
  [/^GET \/api\/appeals\/\d+/, 'appeal'],
  [/^GET \/api\/court\/cases\/\d+/, 'court_case'],
  [/^GET \/api\/stats/, 'stats'],
];

export async function api(path, { method = 'GET', body } = {}) {
  if (MOCK) {
    const hit = MOCKS.find(([re]) => re.test(`${method} ${path}`));
    if (hit) {
      return (await fetch(`mocks/${hit[1]}.json`)).json();
    }
    // POST/PUT mocks — return success stubs
    if (method === 'POST' && path.includes('/api/messages')) {
      return { id: Math.floor(Math.random() * 1000), status: 'new' };
    }
    if (method === 'POST' && path.includes('/api/appeals')) {
      return { id: 42, status: 'new', attempt_no: 1 };
    }
    return { ok: true, id: 1, status: 'new', attempt_no: 1 };
  }

  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body && JSON.stringify(body),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status, data });
  return data;
}

/**
 * Format a UTC ISO datetime string to local readable format.
 */
export function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/**
 * Safely set text content — never use innerHTML for user content.
 */
export function setText(el, text) {
  if (el) el.textContent = text ?? '';
}

/**
 * Create an element with optional className and text content.
 */
export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

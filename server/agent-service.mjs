import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';

export const MAX_BODY_BYTES = 150 * 1024;
export const ONLINE_MS = 90_000;
export const QUEUE_TTL_MS = 15 * 60_000;
const SESSION_SECONDS = 8 * 60 * 60;
const COOKIE = '__Secure-agent_session';
const REQUEST_TTL_MS = 24 * 60 * 60_000;
const LOGIN_WINDOW_MS = 15 * 60_000;
const PUBLIC_WINDOW_MS = 24 * 60 * 60_000;
const PUBLIC_DEFAULT_ITEM_COUNT = 12;
const UNCERTAIN_DELIVERY_ERRORS = new Set(['delivery_unknown_check_local', 'worker_interrupted_check_local']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/;
const PHASES = new Set(['collect', 'local-read', 'analyze', 'render', 'send']);
const BASE_HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Cache-Control': 'no-store, private, max-age=0',
  'Pragma': 'no-cache',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Vary': 'Cookie, Authorization, Origin',
};

class HttpError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}
function problem(status, code) { throw new HttpError(status, code); }
function response(status, body, headers = {}) { return { status, headers: { ...BASE_HEADERS, ...headers }, body }; }
function equalSecret(actual, expected) {
  // Hash first so timingSafeEqual always receives equal-sized buffers.
  return timingSafeEqual(createHash('sha256').update(String(actual)).digest(), createHash('sha256').update(String(expected)).digest());
}
function object(value, code = 'invalid_request') {
  if (!value || typeof value !== 'object' || Array.isArray(value)) problem(400, code);
  return value;
}
function onlyKeys(value, allowed) {
  if (Object.keys(value).some(key => !allowed.includes(key))) problem(400, 'invalid_request');
}
function id(value, code = 'invalid_request') {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) problem(400, code);
  return value;
}
function iso(value, code = 'invalid_request') {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value))) problem(400, code);
  return new Date(value).toISOString();
}
function containsPrivateLocationOrEmail(value) {
  if (/[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+/.test(value)) return true;
  const withoutWebUrls = value.replace(/https?:\/\/[^\s<>"']+/gi, '');
  return /[a-zA-Z]:[\\/]|\\\\[^\s\\]+\\|(?:^|[\s"'(<\u3400-\u9fff])\/[a-zA-Z_.~][^\s<>"']*/.test(withoutWebUrls);
}
function safeText(value, max, optional = false) {
  if (optional && (value === undefined || value === null || value === '')) return '';
  if (typeof value !== 'string' || !value.trim() || value.length > max || containsPrivateLocationOrEmail(value)) problem(400, 'invalid_report');
  // This is plain text, never trusted HTML. The client must use textContent.
  return value.trim();
}
function sourceUrl(value) {
  const text = safeText(value, 2048);
  let url;
  try { url = new URL(text); } catch { problem(400, 'invalid_report'); }
  if (url.protocol !== 'https:' || url.username || url.password || !url.hostname) problem(400, 'invalid_report');
  return text;
}
function recipientEmail(value) {
  if (typeof value !== 'string' || /[^\x20-\x7e]/.test(value)) problem(400, 'invalid_email');
  const email = value.trim().toLowerCase();
  if (email.length > 254) problem(400, 'invalid_email');
  const parts = email.split('@');
  if (parts.length !== 2) problem(400, 'invalid_email');
  const [local, domain] = parts;
  const labels = domain.split('.');
  if (!local || local.length > 64 || !/^[a-z0-9._%+-]+$/.test(local) || local.startsWith('.') || local.endsWith('.') || local.includes('..')
      || labels.length < 2 || labels.some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
      || !/^[a-z]{2,63}$/.test(labels.at(-1))) problem(400, 'invalid_email');
  return email;
}
function publicPreferences(input) {
  const itemCount = input.itemCount === undefined ? PUBLIC_DEFAULT_ITEM_COUNT : input.itemCount;
  if (!Number.isInteger(itemCount) || itemCount < 5 || itemCount > 20) problem(400, 'invalid_item_count');
  const raw = input.keywords === undefined ? '' : input.keywords;
  if (typeof raw !== 'string' || [...raw].length > 200 || /[\p{Cf}\p{Cs}]/u.test(raw)
    || [...raw].some(char => /\p{Cc}/u.test(char) && !/\s/u.test(char))) problem(400, 'invalid_keywords');
  const keywords = raw.replace(/\s+/gu, ' ').trim();
  // The worker uses this as literal filter text, never as a model or executable instruction.
  return { itemCount, keywords };
}
function jobPreferences(job) { return { itemCount: job.itemCount ?? PUBLIC_DEFAULT_ITEM_COUNT, keywords: job.keywords ?? '' }; }
function deliveryUncertain(job) {
  return job.status === 'failed' && (UNCERTAIN_DELIVERY_ERRORS.has(job.error)
    || (typeof job.mailSent !== 'boolean' && job.error !== 'queue_expired'));
}
function refundablePublicJob(job) {
  return job?.mode === 'public-send' && job.status === 'failed'
    && !deliveryUncertain(job) && (job.mailSent === false || job.error === 'queue_expired');
}
function publicJob(job) {
  const result = Object.fromEntries(['id', 'mode', 'status', 'phase', 'createdAt', 'updatedAt'].map(key => [key, job[key]]));
  Object.assign(result, jobPreferences(job));
  result.error = job.error ? (['queue_expired', 'insufficient_news', 'no_matching_news', ...UNCERTAIN_DELIVERY_ERRORS].includes(job.error) ? job.error : 'run_failed') : null;
  if (typeof job.mailSent === 'boolean') result.mailSent = job.mailSent;
  else if (job.error === 'queue_expired') result.mailSent = false;
  if (job.status === 'failed') result.deliveryUncertain = deliveryUncertain(job);
  return result;
}
function dashboardJob(job) {
  if (!job) return null;
  const { recipient, ...safe } = job;
  return safe;
}
function publicRequestJob(state, remembered) {
  const job = state.activeJob?.id === remembered.jobId ? state.activeJob : state.history.find(item => item.id === remembered.jobId) ?? remembered.job;
  return job?.mode === 'public-send' ? job : null;
}
function quotaEntryJob(state, entry) {
  const matches = Object.values(state.requests).filter(item => item.mode === 'public-send' && (entry.jobId
    ? item.jobId === entry.jobId : item.emailHash === entry.emailHash && item.at === entry.at));
  // Old entries have no unique job pointer: an ambiguous timestamp must never refund a sent job.
  const remembered = matches.length === 1 ? matches[0] : null;
  if (remembered) return publicRequestJob(state, remembered);
  if (!entry.jobId) return null;
  return state.activeJob?.id === entry.jobId ? state.activeJob : state.history.find(job => job.id === entry.jobId);
}
function publicQuota(state, now, emailHash, ipHash, jobId) {
  // Legacy entries without jobId still count unless their matching job proves no mail was sent.
  const accepted = (state.publicAccepted ?? []).filter(item => item.at > now - PUBLIC_WINDOW_MS && !refundablePublicJob(quotaEntryJob(state, item)));
  state.publicAccepted = accepted;
  for (const [code, matches, limit] of [
    ['public_email_limit', accepted.filter(item => item.emailHash === emailHash), 3],
    ['public_ip_limit', accepted.filter(item => item.ipHash === ipHash), 9],
    ['public_daily_limit', accepted, 30],
  ]) {
    if (matches.length >= limit) return { error: code, retryAfterSeconds: Math.max(1, Math.ceil((matches[0].at + PUBLIC_WINDOW_MS - now) / 1000)) };
  }
  accepted.push({ at: now, emailHash, ipHash, jobId });
  return null;
}

/** Rebuild only fields the private dashboard actually displays. Never persist
 * raw local-library chunks, file inventories, paths, mail addresses, or logs. */
export function sanitizeReport(input) {
  const report = object(input, 'invalid_report');
  if (!Array.isArray(report.items) || report.items.length < 1 || report.items.length > 15) problem(400, 'invalid_report');
  const seen = new Set();
  const items = report.items.map(raw => {
    object(raw, 'invalid_report');
    const itemId = id(raw.itemId, 'invalid_report');
    if (seen.has(itemId)) problem(400, 'invalid_report');
    seen.add(itemId);
    const date = raw.publishedAtUtc ? iso(raw.publishedAtUtc, 'invalid_report') : null;
    const freshness = raw.freshness === 'fresh' ? 'recent' : raw.freshness;
    if (!['recent', 'stale', 'undated'].includes(freshness)) problem(400, 'invalid_report');
    const source = safeText(raw.sourceName ?? raw.source, 200);
    if (raw.region !== undefined && !['domestic', 'international'].includes(raw.region)) problem(400, 'invalid_report');
    return {
      itemId, title: safeText(raw.title, 300), source, sourceName: source,
      url: sourceUrl(raw.url), publishedAtUtc: date, publishedAt: date ?? '日期不明', freshness,
      summary: safeText(raw.summary, 2000), whyItMatters: safeText(raw.whyItMatters, 1500),
      ...(raw.region === undefined ? {} : { region: raw.region }),
    };
  });
  const suggestions = report.learningSuggestions ?? [];
  if (!Array.isArray(suggestions) || suggestions.length > 3) problem(400, 'invalid_report');
  const connections = report.localConnections ?? [];
  if (!Array.isArray(connections) || connections.length > 3) problem(400, 'invalid_report');
  const result = {
    schemaVersion: 1, runId: id(report.runId, 'invalid_report'),
    title: safeText(report.title ?? '每日科技简报', 200),
    generatedAtUtc: iso(report.generatedAtUtc ?? report.generatedAt, 'invalid_report'),
    overview: safeText(report.overview, 3000), items,
    learningSuggestions: suggestions.map(value => safeText(value, 2000)),
    localConnections: connections.map(raw => {
      object(raw, 'invalid_report');
      const itemId = id(raw.itemId, 'invalid_report');
      if (!seen.has(itemId)) problem(400, 'invalid_report');
      return {
        itemId, newsTitle: safeText(raw.newsTitle ?? items.find(item => item.itemId === itemId).title, 300),
        sourceTitle: safeText(raw.sourceTitle, 300), anchor: safeText(raw.anchor, 200),
        quote: safeText(raw.quote, 1000), relationship: safeText(raw.relationship, 2000),
        nextStep: safeText(raw.nextStep, 1500),
      };
    }),
  };
  for (const field of ['sourceNote', 'localContext', 'localRelevanceNote']) {
    if (report[field] !== undefined && report[field] !== null) result[field] = safeText(report[field], 3000, true);
  }
  result.generatedAt = result.generatedAtUtc;
  if (Buffer.byteLength(JSON.stringify(result), 'utf8') > 100 * 1024) problem(400, 'invalid_report');
  return result;
}

function configuration(env) {
  const originValue = env.SITE_ORIGIN;
  let origin;
  try {
    const parsed = new URL(originValue);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') return null;
    origin = parsed.origin;
  } catch { return null; }
  for (const key of ['AGENT_ADMIN_PASSWORD', 'AGENT_SESSION_SECRET', 'AGENT_WORKER_TOKEN']) {
    if (typeof env[key] !== 'string' || env[key].length < 32) return null;
  }
  if (typeof env.AGENT_WORKER_ID !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(env.AGENT_WORKER_ID)) return null;
  const redisUrl = env.UPSTASH_REDIS_REST_URL || env.KV_REST_API_URL;
  const redisToken = env.UPSTASH_REDIS_REST_TOKEN || env.KV_REST_API_TOKEN;
  try {
    const parsed = new URL(redisUrl);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash) return null;
  } catch { return null; }
  if (typeof redisToken !== 'string' || !redisToken) return null;
  return { origin, password: env.AGENT_ADMIN_PASSWORD, sessionSecret: env.AGENT_SESSION_SECRET,
    workerToken: env.AGENT_WORKER_TOKEN, workerId: env.AGENT_WORKER_ID, redisUrl, redisToken };
}

function sessionToken(config, now) {
  const issued = Math.floor(now / 1000);
  const payload = Buffer.from(JSON.stringify({ v: 1, sub: 'owner', iat: issued, exp: issued + SESSION_SECONDS, nonce: randomBytes(16).toString('base64url') })).toString('base64url');
  return `${payload}.${createHmac('sha256', config.sessionSecret).update(payload).digest('base64url')}`;
}
function validSession(cookieHeader, config, now) {
  if (typeof cookieHeader !== 'string' || cookieHeader.length > 8192) return false;
  const cookies = cookieHeader.split(';').map(part => part.trim());
  const matches = cookies.filter(part => part.startsWith(`${COOKIE}=`));
  if (matches.length !== 1) return false;
  const value = matches[0].slice(COOKIE.length + 1);
  if (value.length > 1024 || !/^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+$/.test(value)) return false;
  const [payload, signature] = value.split('.');
  const expected = createHmac('sha256', config.sessionSecret).update(payload).digest('base64url');
  if (!equalSecret(signature, expected)) return false;
  try {
    const data = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
    const seconds = Math.floor(now / 1000);
    return data.v === 1 && data.sub === 'owner' && Number.isInteger(data.iat) && Number.isInteger(data.exp)
      && data.iat <= seconds + 30 && data.exp > seconds && data.exp - data.iat === SESSION_SECONDS;
  } catch { return false; }
}
function cookie(value, maxAge) { return `${COOKIE}=${value}; Path=/api/agent; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`; }
function getHeader(headers, name) {
  if (headers?.get) return headers.get(name) || '';
  const value = headers?.[name] ?? headers?.[Object.keys(headers ?? {}).find(key => key.toLowerCase() === name)];
  return typeof value === 'string' ? value : '';
}

function initialState() {
  return { version: 1, worker: { lastSeenAt: null, phase: 'idle', activeJobId: null }, activeJob: null, history: [], latestReport: null, requests: {} };
}
function rememberHistory(state, job) {
  state.history = [structuredClone(dashboardJob(job)), ...state.history.filter(old => old.id !== job.id)].slice(0, 20);
  if (job.mode === 'public-send') {
    // Keep capability status for a day even if owner jobs displace visible history.
    for (const [key, remembered] of Object.entries(state.requests)) {
      if (key.startsWith('public:') && remembered.jobId === job.id) {
        remembered.job = publicJob(job);
        if (job.runId) remembered.runId = job.runId;
      }
    }
    if (refundablePublicJob(job)) {
      state.publicAccepted = (state.publicAccepted ?? []).filter(entry => {
        if (entry.jobId) return entry.jobId !== job.id;
        return quotaEntryJob(state, entry)?.id !== job.id;
      });
    }
  }
}
function expireQueue(state, now) {
  const job = state.activeJob;
  if (job?.status === 'queued' && now - Date.parse(job.createdAt) >= QUEUE_TTL_MS) {
    job.status = 'failed'; job.phase = 'failed'; job.error = 'queue_expired'; job.mailSent = false; job.updatedAt = new Date(now).toISOString();
    rememberHistory(state, job); state.activeJob = null;
    if (state.worker.activeJobId === job.id) { state.worker.activeJobId = null; state.worker.phase = 'idle'; }
  }
  // Idempotency keys outlive the 20-entry visible history.
  const entries = Object.entries(state.requests).filter(([, item]) => now - item.at < REQUEST_TTL_MS).sort((a, b) => b[1].at - a[1].at);
  state.requests = Object.fromEntries([...entries.filter(([key]) => key.startsWith('public:')), ...entries.filter(([key]) => !key.startsWith('public:')).slice(0, 200)]);
}
function workerOnline(worker, now) { return worker.lastSeenAt !== null && now - Date.parse(worker.lastSeenAt) >= 0 && now - Date.parse(worker.lastSeenAt) <= ONLINE_MS; }
function viewState(state, now) {
  return { worker: { ...state.worker, online: workerOnline(state.worker, now) },
    activeJob: dashboardJob(state.activeJob), history: state.history.map(dashboardJob), latestReport: state.latestReport,
    schedule: { time: '09:00', timezone: 'Asia/Shanghai', execution: 'local' } };
}
function touchWorker(state, now) { state.worker.lastSeenAt = new Date(now).toISOString(); }
function saveReport(state, report, mailSent, completedAt) {
  if (!state.latestReport || Date.parse(completedAt) >= Date.parse(state.latestReport.completedAt)) {
    const previousSent = state.latestReport?.runId === report.runId && state.latestReport.mailSent;
    state.latestReport = { ...report, mailSent: mailSent || Boolean(previousSent), completedAt };
  }
}

// Redis REST accepts JSON command arrays; EVAL performs this compare-and-set
// atomically. All dynamic values are arguments, never interpolated Lua source.
const CAS_SCRIPT = `local current = redis.call('GET', KEYS[1])
if (current or '') ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2])
return 1`;
const RATE_SCRIPT = `local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('PTTL', KEYS[1])}`;

/** Production storage: private Redis only; no process-memory fallback. */
export class RedisStore {
  constructor({ url, token, fetchImpl = globalThis.fetch, key = '{homepage-agent}:state:v1' }) {
    this.url = url.replace(/\/$/, ''); this.token = token; this.fetch = fetchImpl; this.key = key;
  }
  async command(args) {
    let result;
    try {
      const reply = await this.fetch(this.url, { method: 'POST', headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(args), cache: 'no-store', signal: AbortSignal.timeout(8000) });
      if (!reply.ok) throw new Error('redis_http_failure');
      result = await reply.json();
      if (!result || Object.hasOwn(result, 'error') || !Object.hasOwn(result, 'result')) throw new Error('redis_result_failure');
    } catch { throw new Error('storage_unavailable'); }
    return result.result;
  }
  async transact(transform) {
    for (let attempt = 0; attempt < 12; attempt++) {
      const raw = await this.command(['GET', this.key]);
      let state;
      try { state = raw === null ? initialState() : JSON.parse(raw); } catch { throw new Error('storage_unavailable'); }
      if (!state || state.version !== 1 || !Array.isArray(state.history) || !state.worker || !state.requests) throw new Error('storage_unavailable');
      const result = transform(state);
      const changed = await this.command(['EVAL', CAS_SCRIPT, '1', this.key, raw ?? '', JSON.stringify(state)]);
      if (Number(changed) === 1) return result;
    }
    throw new Error('storage_busy');
  }
  async rateLimit(key, limit, windowMs) {
    const value = await this.command(['EVAL', RATE_SCRIPT, '1', `{homepage-agent}:login:${key}`, String(windowMs)]);
    if (!Array.isArray(value) || value.length !== 2) throw new Error('storage_unavailable');
    return { allowed: Number(value[0]) <= limit, retryAfter: Math.max(1, Math.ceil(Number(value[1]) / 1000)) };
  }
}

/** Request adapter independent of Vercel; injectable storage is for tests.
 * request = {method,url,headers,body,ip}; returns {status,headers,body}. */
export function createAgentService({ env = process.env, store, now = Date.now, makeId = randomUUID } = {}) {
  const config = configuration(env);
  const backingStore = store ?? (config ? new RedisStore({ url: config.redisUrl, token: config.redisToken }) : null);
  return async function handle(request) {
    try {
      if (!config || !backingStore) return response(503, { error: 'not_configured' });
      const currentTime = now();
      const method = String(request.method ?? 'GET').toUpperCase();
      let action;
      try { action = new URL(request.url, config.origin).searchParams.get('action'); } catch { problem(400, 'invalid_request'); }
      if (!['login', 'logout', 'status', 'start', 'public-start', 'public-status', 'poll', 'update', 'publish'].includes(action)) problem(404, 'not_found');
      if (method !== (action === 'status' ? 'GET' : 'POST')) return response(405, { error: 'method_not_allowed' }, { Allow: action === 'status' ? 'GET' : 'POST' });
      const ownerAction = ['login', 'logout', 'status', 'start'].includes(action);
      const publicAction = ['public-start', 'public-status'].includes(action);
      if ((ownerAction || publicAction) && method === 'POST' && getHeader(request.headers, 'origin') !== config.origin) problem(403, 'origin_mismatch');
      // A cross-site GET is also rejected when it carries an explicit Origin.
      if (ownerAction && getHeader(request.headers, 'origin') && getHeader(request.headers, 'origin') !== config.origin) problem(403, 'origin_mismatch');
      if (ownerAction && !['login'].includes(action) && !validSession(getHeader(request.headers, 'cookie'), config, currentTime)) problem(401, 'unauthorized');
      if (!ownerAction && !publicAction) {
        const authorization = getHeader(request.headers, 'authorization');
        if (!authorization.startsWith('Bearer ') || !equalSecret(authorization.slice(7), config.workerToken)) problem(401, 'unauthorized');
      }
      let body = {};
      if (method === 'POST') {
        if (!/^application\/json(?:\s*;|$)/i.test(getHeader(request.headers, 'content-type'))) problem(415, 'json_required');
        const raw = typeof request.body === 'string' || Buffer.isBuffer(request.body) ? request.body : JSON.stringify(request.body ?? {});
        if (Buffer.byteLength(raw, 'utf8') > MAX_BODY_BYTES) problem(413, 'body_too_large');
        try { body = object(JSON.parse(Buffer.isBuffer(raw) ? raw.toString('utf8') : raw)); } catch (error) {
          if (error instanceof HttpError) throw error;
          problem(400, 'invalid_json');
        }
      }
      if (action === 'login') {
        onlyKeys(body, ['password']);
        const hash = createHmac('sha256', config.sessionSecret).update(String(request.ip || 'unknown')).digest('hex');
        const rate = await backingStore.rateLimit(hash, 5, LOGIN_WINDOW_MS, currentTime);
        if (!rate.allowed) return response(429, { error: 'too_many_attempts' }, { 'Retry-After': String(rate.retryAfter) });
        if (typeof body.password !== 'string' || body.password.length > 1024 || !equalSecret(body.password, config.password)) problem(401, 'unauthorized');
        return response(200, { ok: true }, { 'Set-Cookie': cookie(sessionToken(config, currentTime), SESSION_SECONDS) });
      }
      if (action === 'logout') {
        onlyKeys(body, []);
        return response(200, { ok: true }, { 'Set-Cookie': cookie('', 0) });
      }
      if (action === 'status') {
        return response(200, await backingStore.transact(state => { expireQueue(state, currentTime); return viewState(state, currentTime); }));
      }
      if (publicAction) {
        onlyKeys(body, action === 'public-start' ? ['requestId', 'email', 'itemCount', 'keywords'] : ['requestId']);
        if (typeof body.requestId !== 'string' || !UUID.test(body.requestId)) problem(400, 'invalid_request');
        const requestKey = `public:${body.requestId.toLowerCase()}`;
        const email = action === 'public-start' ? recipientEmail(body.email) : null;
        const preferences = action === 'public-start' ? publicPreferences(body) : null;
        const emailHash = email && createHmac('sha256', config.sessionSecret).update(`public-email:${email}`).digest('hex');
        const ipHash = createHmac('sha256', config.sessionSecret).update(`public-ip:${String(request.ip || 'unknown')}`).digest('hex');
        const jobId = action === 'public-start' ? makeId() : null;
        const outcome = await backingStore.transact(state => {
          expireQueue(state, currentTime);
          const remembered = state.requests[requestKey];
          if (remembered) {
            const previousPreferences = jobPreferences(remembered);
            if (remembered.mode !== 'public-send' || (email && (remembered.emailHash !== emailHash
              || previousPreferences.itemCount !== preferences.itemCount || previousPreferences.keywords !== preferences.keywords))) return { status: 409, body: { error: 'request_conflict' } };
            const job = publicRequestJob(state, remembered);
            return job ? { status: action === 'public-start' ? 202 : 200, body: { job: publicJob(job) } }
              : { status: action === 'public-start' ? 409 : 404, body: { error: action === 'public-start' ? 'request_already_used' : 'not_found' } };
          }
          if (action === 'public-status') return { status: 404, body: { error: 'not_found' } };
          if (state.activeJob) return { status: 409, body: { error: 'job_active' } };
          if (!workerOnline(state.worker, currentTime)) return { status: 409, body: { error: 'worker_offline' } };
          const limited = publicQuota(state, currentTime, emailHash, ipHash, jobId);
          if (limited) return { status: 429, body: limited };
          const timestamp = new Date(currentTime).toISOString();
          const job = { id: jobId, mode: 'public-send', recipient: email, ...preferences, status: 'queued', phase: 'queued', createdAt: timestamp, updatedAt: timestamp, error: null };
          state.activeJob = job;
          state.requests[requestKey] = { jobId, mode: 'public-send', emailHash, ...preferences, at: currentTime };
          return { status: 202, body: { job: publicJob(job) } };
        });
        return response(outcome.status, outcome.body, outcome.status === 429 ? { 'Retry-After': String(outcome.body.retryAfterSeconds) } : {});
      }
      if (action === 'start') {
        onlyKeys(body, ['requestId', 'mode']);
        if (typeof body.requestId !== 'string' || !UUID.test(body.requestId) || !['preview', 'send'].includes(body.mode)) problem(400, 'invalid_request');
        const requestId = body.requestId.toLowerCase();
        const jobId = makeId();
        const outcome = await backingStore.transact(state => {
          expireQueue(state, currentTime);
          const remembered = state.requests[requestId];
          if (remembered) {
            if (remembered.mode !== body.mode) return { status: 409, body: { error: 'request_conflict' } };
            const job = state.activeJob?.id === remembered.jobId ? state.activeJob : state.history.find(item => item.id === remembered.jobId);
            return job ? { status: 202, body: { job: dashboardJob(job) } } : { status: 409, body: { error: 'request_already_used' } };
          }
          if (state.activeJob) return { status: 409, body: { error: 'job_active', job: dashboardJob(state.activeJob) } };
          if (!workerOnline(state.worker, currentTime)) return { status: 409, body: { error: 'worker_offline' } };
          const timestamp = new Date(currentTime).toISOString();
          const job = { id: jobId, mode: body.mode, status: 'queued', phase: 'queued', createdAt: timestamp, updatedAt: timestamp, error: null };
          state.activeJob = job;
          state.requests[requestId] = { jobId, mode: body.mode, at: currentTime };
          return { status: 202, body: { job } };
        });
        return response(outcome.status, outcome.body);
      }
      if (!equalSecret(body.workerId, config.workerId)) problem(403, 'worker_mismatch');
      if (action === 'poll') {
        onlyKeys(body, ['workerId']);
        const result = await backingStore.transact(state => {
          expireQueue(state, currentTime); touchWorker(state, currentTime);
          if (state.activeJob?.status === 'queued') {
            state.activeJob.status = 'running'; state.activeJob.phase = 'collect'; state.activeJob.updatedAt = new Date(currentTime).toISOString();
          }
          const job = state.activeJob;
          state.worker.activeJobId = job?.id ?? null; state.worker.phase = job?.phase ?? 'idle';
          // Public delivery adds a validated recipient and literal filter preferences.
          return { job: job ? { id: job.id, mode: job.mode, ...(job.mode === 'public-send' ? { recipient: job.recipient, ...jobPreferences(job) } : {}) } : null, pollSeconds: 30 };
        });
        return response(200, result);
      }
      if (action === 'publish') {
        onlyKeys(body, ['workerId', 'runId', 'report', 'mailSent', 'completedAt']);
        const runId = id(body.runId);
        const report = sanitizeReport(body.report);
        if (report.runId !== runId || typeof body.mailSent !== 'boolean') problem(400, 'invalid_request');
        const completedAt = iso(body.completedAt);
        if (Date.parse(completedAt) > currentTime + 5 * 60_000) problem(400, 'invalid_request');
        await backingStore.transact(state => {
          expireQueue(state, currentTime); touchWorker(state, currentTime);
          if ([state.activeJob, ...state.history, ...Object.values(state.requests)].some(job => job?.mode === 'public-send' && (job.runId === runId || `web-${job.id ?? job.jobId}` === runId))) problem(409, 'public_report_publish_forbidden');
          saveReport(state, report, body.mailSent, completedAt);
          return null;
        });
        return response(200, { ok: true });
      }
      onlyKeys(body, ['workerId', 'jobId', 'status', 'phase', 'runId', 'mailSent', 'report', 'error']);
      if (typeof body.jobId !== 'string' || !UUID.test(body.jobId) || !['running', 'completed', 'failed'].includes(body.status)) problem(400, 'invalid_request');
      if ((body.status === 'running' && !PHASES.has(body.phase)) || (body.status === 'completed' && body.phase !== 'complete') || (body.status === 'failed' && body.phase !== 'failed')) problem(400, 'invalid_request');
      if (body.mailSent !== undefined && typeof body.mailSent !== 'boolean') problem(400, 'invalid_request');
      if (body.error !== undefined && body.error !== null && (typeof body.error !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(body.error))) problem(400, 'invalid_request');
      const runId = body.runId === undefined ? undefined : id(body.runId);
      const report = body.report === undefined ? null : sanitizeReport(body.report);
      if (report && (body.status !== 'completed' || (runId && report.runId !== runId))) problem(400, 'invalid_request');
      const outcome = await backingStore.transact(state => {
        expireQueue(state, currentTime); touchWorker(state, currentTime);
        const job = state.activeJob;
        const knownJob = job?.id === body.jobId ? job : state.history.find(item => item.id === body.jobId);
        if (knownJob && knownJob.mode !== 'public-send' && body.status === 'completed' && !report) return { status: 400, body: { error: 'report_required' } };
        if (knownJob?.mode === 'public-send' && report && (report.localConnections.length || report.localContext || report.localRelevanceNote)) return { status: 409, body: { error: 'public_report_private_data' } };
        if (!job || job.id !== body.jobId) {
          const terminal = state.history.find(item => item.id === body.jobId);
          if (terminal && terminal.status === body.status && ['completed', 'failed'].includes(body.status)) return { status: 200, body: { ok: true } };
          return { status: 409, body: { error: 'job_mismatch' } };
        }
        if (job.status !== 'running') return { status: 409, body: { error: 'job_not_claimed' } };
        if ((job.mode === 'preview' && (body.mailSent === true || body.phase === 'send')) || (body.status === 'completed' && ['send', 'public-send'].includes(job.mode) && body.mailSent !== true)
          || (job.mode === 'public-send' && body.phase === 'local-read')) return { status: 409, body: { error: 'invalid_job_transition' } };
        job.status = body.status; job.phase = body.phase; job.updatedAt = new Date(currentTime).toISOString();
        job.error = body.status === 'failed' ? body.error || 'run_failed' : null;
        if (runId || report) job.runId = runId || report.runId;
        if (body.mailSent !== undefined) job.mailSent = body.mailSent;
        else if (job.mode === 'public-send' && body.status === 'failed') delete job.mailSent;
        state.worker.phase = job.phase; state.worker.activeJobId = job.id;
        if (body.status !== 'running') {
          if (report && job.mode !== 'public-send') saveReport(state, report, Boolean(body.mailSent), job.updatedAt);
          rememberHistory(state, job); state.activeJob = null;
          state.worker.activeJobId = null;
        }
        return { status: 200, body: { ok: true } };
      });
      return response(outcome.status, outcome.body);
    } catch (error) {
      if (error instanceof HttpError) return response(error.status, { error: error.code });
      // No diagnostics, token, request body, or remote service response is exposed.
      return response(503, { error: 'storage_unavailable' });
    }
  };
}

/** Bounded Node/Vercel request reader. Works with parsed Vercel bodies too. */
export async function readRequestBody(request) {
  const length = getHeader(request.headers, 'content-length');
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY_BYTES)) problem(413, 'body_too_large');
  if (request.body !== undefined) {
    const raw = typeof request.body === 'string' || Buffer.isBuffer(request.body) ? request.body : JSON.stringify(request.body);
    if (Buffer.byteLength(raw, 'utf8') > MAX_BODY_BYTES) problem(413, 'body_too_large');
    return raw;
  }
  const chunks = []; let size = 0;
  for await (const chunk of request) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += value.length;
    if (size > MAX_BODY_BYTES) problem(413, 'body_too_large');
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString('utf8') || '{}';
}

export function createNodeHandler(options) {
  const handle = createAgentService(options);
  return async function nodeHandler(req, res) {
    let result;
    try {
      const body = req.method === 'POST' ? await readRequestBody(req) : undefined;
      const forwarded = getHeader(req.headers, 'x-vercel-forwarded-for');
      const ip = forwarded.split(',')[0].trim() || req.socket?.remoteAddress || 'unknown';
      result = await handle({ method: req.method, url: req.url, headers: req.headers, body, ip });
    } catch (error) {
      result = response(error instanceof HttpError ? error.status : 400, { error: error instanceof HttpError ? error.code : 'invalid_request' });
    }
    for (const [key, value] of Object.entries(result.headers)) res.setHeader(key, value);
    res.statusCode = result.status;
    res.end(JSON.stringify(result.body));
  };
}

export { createAgentService as createService, initialState as createInitialState };

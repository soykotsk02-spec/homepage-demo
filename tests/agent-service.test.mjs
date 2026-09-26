import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { Readable } from 'node:stream';
import { createAgentService, createInitialState, RedisStore, sanitizeReport, readRequestBody, MAX_BODY_BYTES, ONLINE_MS, QUEUE_TTL_MS } from '../server/agent-service.mjs';

const ENV = {
  SITE_ORIGIN: 'https://demo.example',
  AGENT_ADMIN_PASSWORD: 'test-owner-password-0123456789-abcdef',
  AGENT_SESSION_SECRET: 'test-session-secret-0123456789-abcdef',
  AGENT_WORKER_TOKEN: 'test-worker-token-0123456789-abcdefgh',
  AGENT_WORKER_ID: 'test-local-worker',
  UPSTASH_REDIS_REST_URL: 'https://test-redis.example',
  UPSTASH_REDIS_REST_TOKEN: 'test-only-never-connect',
};

// Explicitly injected test store. Production has no in-memory fallback.
class MemoryStore {
  constructor() { this.state = createInitialState(); this.rates = new Map(); this.tail = Promise.resolve(); this.rateKeys = []; }
  transact(transform) {
    const task = this.tail.then(() => {
      const draft = structuredClone(this.state);
      const result = transform(draft);
      this.state = draft;
      return structuredClone(result);
    });
    this.tail = task.catch(() => {});
    return task;
  }
  async rateLimit(key, limit, windowMs, now) {
    this.rateKeys.push(key);
    let entry = this.rates.get(key);
    if (!entry || entry.until <= now) entry = { count: 0, until: now + windowMs };
    entry.count++; this.rates.set(key, entry);
    return { allowed: entry.count <= limit, retryAfter: Math.ceil((entry.until - now) / 1000) };
  }
}
function fixtureReport(runId = 'run-test') {
  return {
    runId, title: '科技简报', generatedAtUtc: '2026-09-27T01:00:00Z', overview: '本次收集了科技新闻。',
    items: [{ itemId: 'story1', title: '科技新闻标题', sourceName: 'Official source', url: 'https://example.org/story',
      publishedAtUtc: '2026-09-26T12:00:00Z', freshness: 'recent', summary: '这里是依据来源整理的摘要。', whyItMatters: '可结合本地资料提出验证问题。' }],
    learningSuggestions: ['花二十分钟核对来源。'],
    localConnections: [{ itemId: 'story1', sourceTitle: '测试学习资料', anchor: '正文段落 3',
      quote: '先核对资料来源，再开展分析。', relationship: '可以用同样的方法核对新闻。', nextStep: '整理两个待验证的问题。' }],
  };
}
function fixture() {
  let clock = Date.parse('2026-09-27T01:00:00Z');
  const store = new MemoryStore();
  const handle = createAgentService({ env: ENV, store, now: () => clock });
  const request = (action, body, extra = {}) => handle({ method: action === 'status' ? 'GET' : 'POST',
    url: `/api/agent?action=${action}`, headers: { 'content-type': 'application/json', origin: ENV.SITE_ORIGIN, ...(extra.headers ?? {}) },
    body, ip: '203.0.113.9', ...extra,
    // Keep the merged headers, including content type for body validation.
    headers: { 'content-type': 'application/json', origin: ENV.SITE_ORIGIN, ...(extra.headers ?? {}) },
  });
  let cookie;
  const login = async () => { const result = await request('login', { password: ENV.AGENT_ADMIN_PASSWORD }); cookie = result.headers['Set-Cookie'].split(';')[0]; return result; };
  const owner = (action, body, extra = {}) => request(action, body, { ...extra, headers: { cookie, ...(extra.headers ?? {}) } });
  const worker = (action, body = {}, extra = {}) => request(action, { workerId: ENV.AGENT_WORKER_ID, ...body }, { ...extra,
    headers: { authorization: `Bearer ${ENV.AGENT_WORKER_TOKEN}`, ...(extra.headers ?? {}) } });
  return { store, handle, request, login, owner, worker, advance: ms => { clock += ms; }, time: () => clock };
}
async function started(f, mode = 'preview') {
  await f.login(); await f.worker('poll');
  const result = await f.owner('start', { requestId: randomUUID(), mode });
  assert.equal(result.status, 202);
  return result.body.job;
}

test('missing configuration fails closed with no-store and no CORS', async () => {
  const service = createAgentService({ env: {}, store: new MemoryStore() });
  const result = await service({ method: 'GET', url: '/api/agent?action=status' });
  assert.equal(result.status, 503); assert.equal(result.body.error, 'not_configured');
  assert.match(result.headers['Cache-Control'], /no-store/);
  assert.equal(result.headers['Access-Control-Allow-Origin'], undefined);
});

test('reports require an owner session and worker actions require a bearer', async () => {
  const f = fixture();
  assert.equal((await f.request('status')).status, 401);
  assert.equal((await f.request('poll', { workerId: ENV.AGENT_WORKER_ID })).status, 401);
  assert.equal((await f.worker('poll', {}, { headers: { authorization: 'Bearer invalid' } })).status, 401);
  assert.equal((await f.worker('poll', { workerId: 'different-worker' })).status, 403);
});

test('login and owner mutations enforce exact Origin, including missing Origin', async () => {
  const f = fixture();
  for (const origin of ['', 'https://evil.example', 'https://demo.example.evil']) {
    assert.equal((await f.request('login', { password: ENV.AGENT_ADMIN_PASSWORD }, { headers: { origin } })).status, 403);
  }
  await f.login();
  assert.equal((await f.owner('start', { requestId: randomUUID(), mode: 'preview' }, { headers: { origin: '' } })).status, 403);
  assert.equal((await f.owner('logout', {}, { headers: { origin: 'https://evil.example' } })).status, 403);
  assert.equal((await f.owner('status', undefined, { headers: { origin: 'https://evil.example' } })).status, 403);
});

test('session cookie is signed, private, secure, scoped, expiring, and logout clears it', async () => {
  const f = fixture(); const login = await f.login();
  assert.match(login.headers['Set-Cookie'], /HttpOnly; Secure; SameSite=Strict; Max-Age=28800/);
  assert.match(login.headers['Set-Cookie'], /Path=\/api\/agent/);
  assert.equal((await f.owner('status')).status, 200);
  const cookie = login.headers['Set-Cookie'].split(';')[0];
  assert.equal((await f.request('status', undefined, { headers: { cookie: cookie.slice(0, -1) + '!' } })).status, 401);
  const logout = await f.owner('logout', {});
  assert.match(logout.headers['Set-Cookie'], /Max-Age=0/);
  f.advance(8 * 60 * 60_000);
  assert.equal((await f.owner('status')).status, 401);
});

test('login uses a durable bounded rate counter indexed by an IP hash only', async () => {
  const f = fixture();
  for (let attempt = 0; attempt < 5; attempt++) assert.equal((await f.request('login', { password: 'wrong' })).status, 401);
  const limited = await f.request('login', { password: ENV.AGENT_ADMIN_PASSWORD });
  assert.equal(limited.status, 429); assert.equal(limited.headers['Retry-After'], '900');
  assert.match(f.store.rateKeys[0], /^[0-9a-f]{64}$/);
  assert.ok(!JSON.stringify(f.store.rateKeys).includes('203.0.113.9'));
  f.advance(15 * 60_000);
  assert.equal((await f.login()).status, 200);
});

test('body size, JSON content type, action and mode are bounded', async () => {
  const f = fixture(); await f.login();
  assert.equal((await f.request('login', { password: '字'.repeat(MAX_BODY_BYTES / 2) })).status, 413);
  assert.equal((await f.request('login', '{}', { headers: { 'content-type': 'text/plain' } })).status, 415);
  assert.equal((await f.request('login', '{broken')).status, 400);
  assert.equal((await f.owner('start', { requestId: randomUUID(), mode: 'run arbitrary command' })).status, 400);
  assert.equal((await f.owner('start', { requestId: randomUUID(), mode: 'send', command: 'anything' })).status, 400);
  assert.equal((await f.request('poll', {}, { method: 'OPTIONS' })).status, 405);
});

test('offline worker prevents queueing and heartbeat establishes online state', async () => {
  const f = fixture(); await f.login();
  const offline = await f.owner('start', { requestId: randomUUID(), mode: 'preview' });
  assert.equal(offline.status, 409); assert.equal(offline.body.error, 'worker_offline');
  assert.deepEqual((await f.worker('poll')).body, { job: null, pollSeconds: 30 });
  assert.equal((await f.owner('status')).body.worker.online, true);
  f.advance(ONLINE_MS + 1);
  assert.equal((await f.owner('status')).body.worker.online, false);
});

test('simultaneous duplicate request IDs yield exactly the same job', async () => {
  const f = fixture(); await f.login(); await f.worker('poll');
  const body = { requestId: randomUUID(), mode: 'preview' };
  const responses = await Promise.all([f.owner('start', body), f.owner('start', body)]);
  assert.deepEqual(responses.map(result => result.status), [202, 202]);
  assert.equal(responses[0].body.job.id, responses[1].body.job.id);
  assert.equal(Object.keys(f.store.state.requests).length, 1);
  const changedMode = await f.owner('start', { ...body, mode: 'send' });
  assert.equal(changedMode.status, 409);
});

test('different simultaneous starts cannot create two active jobs', async () => {
  const f = fixture(); await f.login(); await f.worker('poll');
  const responses = await Promise.all(['preview', 'send'].map(mode => f.owner('start', { requestId: randomUUID(), mode })));
  assert.deepEqual(responses.map(result => result.status).sort(), [202, 409]);
  assert.equal(Object.keys(f.store.state.requests).length, 1);
});

test('poll always returns only id and mode; an offline running job resumes the same id', async () => {
  const f = fixture(); const job = await started(f);
  const first = await f.worker('poll');
  assert.deepEqual(first.body.job, { id: job.id, mode: 'preview' });
  await f.worker('update', { jobId: job.id, status: 'running', phase: 'analyze' });
  f.advance(QUEUE_TTL_MS * 2);
  const status = await f.owner('status');
  assert.equal(status.body.worker.online, false); assert.equal(status.body.activeJob.id, job.id);
  assert.equal((await f.owner('start', { requestId: randomUUID(), mode: 'send' })).status, 409);
  assert.deepEqual((await f.worker('poll')).body.job, { id: job.id, mode: 'preview' });
  assert.equal((await f.owner('status')).body.activeJob.phase, 'analyze');
});

test('unclaimed queue expires after 15 minutes and is never executed', async () => {
  const f = fixture(); const job = await started(f);
  f.advance(QUEUE_TTL_MS);
  assert.equal((await f.worker('poll')).body.job, null);
  const status = (await f.owner('status')).body;
  assert.equal(status.activeJob, null); assert.equal(status.history[0].id, job.id);
  assert.equal(status.history[0].status, 'failed'); assert.equal(status.history[0].error, 'queue_expired');
});

test('reports retain useful local connections and discard all non-whitelisted material', () => {
  const raw = fixtureReport();
  raw.localEvidence = { path: 'C:\\Users\\private\\secret.docx', email: 'secret@example.com' };
  raw['raw-local-evidence'] = 'private source'; raw.recipient = 'secret@example.com';
  raw.localConnections[0].path = 'C:\\private.docx'; raw.items[0].hidden = 'secret';
  const safe = sanitizeReport(raw);
  for (const field of ['sourceTitle', 'anchor', 'quote', 'relationship', 'nextStep']) assert.equal(safe.localConnections[0][field], raw.localConnections[0][field]);
  assert.ok(!JSON.stringify(safe).includes('secret')); assert.ok(!JSON.stringify(safe).includes('C:\\'));
  assert.equal(safe.recipient, undefined);
});

test('known report text rejects emails and Windows, UNC, or Unix absolute paths', () => {
  for (const privateText of ['mail secret@example.com', '电脑C:\\Users\\private\\a.docx', 'file /home/alice/notes.md', '\\\\server\\folder\\data']) {
    const raw = fixtureReport(); raw.localConnections[0].quote = privateText;
    assert.throws(() => sanitizeReport(raw), /invalid_report/);
  }
  const raw = fixtureReport(); raw.items[0].url = 'javascript:alert(1)';
  assert.throws(() => sanitizeReport(raw), /invalid_report/);
});

test('terminal update saves a private report, clears active job, and is idempotent', async () => {
  const f = fixture(); const job = await started(f); await f.worker('poll');
  const body = { jobId: job.id, status: 'completed', phase: 'complete', runId: 'run-test', mailSent: false, report: fixtureReport() };
  assert.equal((await f.worker('update', body)).status, 200);
  assert.equal((await f.worker('update', body)).status, 200);
  const status = (await f.owner('status')).body;
  assert.equal(status.activeJob, null); assert.equal(status.history.length, 1);
  assert.equal(status.history[0].status, 'completed'); assert.equal(status.latestReport.mailSent, false);
  assert.deepEqual(status.schedule, { time: '09:00', timezone: 'Asia/Shanghai', execution: 'local' });
});

test('preview cannot send; send cannot report success without an actual sent flag', async () => {
  const f = fixture(); const job = await started(f); await f.worker('poll');
  assert.equal((await f.worker('update', { jobId: job.id, status: 'running', phase: 'send' })).status, 409);
  assert.equal((await f.worker('update', { jobId: job.id, status: 'completed', phase: 'complete', mailSent: true, report: fixtureReport() })).status, 409);
  await f.worker('update', { jobId: job.id, status: 'failed', phase: 'failed', error: 'test_cancelled' });
  const next = await f.owner('start', { requestId: randomUUID(), mode: 'send' }); await f.worker('poll');
  assert.equal((await f.worker('update', { jobId: next.body.job.id, status: 'completed', phase: 'complete', mailSent: false, report: fixtureReport() })).status, 409);
  assert.equal((await f.worker('update', { jobId: next.body.job.id, status: 'completed', phase: 'complete', mailSent: true, report: fixtureReport() })).status, 200);
});

test('invalid report or unsafe error never alters the running job or saves private data', async () => {
  const f = fixture(); const job = await started(f); await f.worker('poll');
  const report = fixtureReport(); report.overview = 'C:\\Users\\private';
  assert.equal((await f.worker('update', { jobId: job.id, status: 'completed', phase: 'complete', mailSent: false, report })).status, 400);
  assert.equal((await f.worker('update', { jobId: job.id, status: 'failed', phase: 'failed', error: 'C:\\Users\\private' })).status, 400);
  assert.equal(f.store.state.activeJob.status, 'running'); assert.equal(f.store.state.latestReport, null);
});

test('publish synchronizes the existing morning report without replacing newer results', async () => {
  const f = fixture(); await f.login();
  assert.equal((await f.worker('publish', { runId: 'morning', report: fixtureReport('morning'), mailSent: true, completedAt: new Date(f.time()).toISOString() })).status, 200);
  const earlier = new Date(f.time() - 60_000).toISOString();
  await f.worker('publish', { runId: 'old', report: fixtureReport('old'), mailSent: false, completedAt: earlier });
  assert.equal((await f.owner('status')).body.latestReport.runId, 'morning');
  await f.worker('publish', { runId: 'morning', report: fixtureReport('morning'), mailSent: false, completedAt: new Date(f.time()).toISOString() });
  assert.equal((await f.owner('status')).body.latestReport.mailSent, true);
});

test('history stays at 20 entries and old request IDs cannot start another job', async () => {
  const f = fixture(); await f.login(); await f.worker('poll');
  let firstRequest;
  for (let i = 0; i < 23; i++) {
    const requestId = randomUUID(); if (i === 0) firstRequest = requestId;
    const job = (await f.owner('start', { requestId, mode: 'preview' })).body.job;
    await f.worker('poll');
    await f.worker('update', { jobId: job.id, status: 'failed', phase: 'failed', error: 'test_failed' });
  }
  assert.equal(f.store.state.history.length, 20);
  assert.equal((await f.owner('start', { requestId: firstRequest, mode: 'preview' })).status, 409);
  assert.equal(f.store.state.activeJob, null);
});

test('Redis transport uses private POST commands and a Lua CAS to avoid races', async () => {
  let raw = null; let gets = 0; const calls = [];
  const fakeFetch = async (_url, options) => {
    assert.equal(options.method, 'POST'); assert.equal(options.headers.Authorization, 'Bearer private-token');
    assert.equal(options.cache, 'no-store');
    const args = JSON.parse(options.body); calls.push(args);
    if (args[0] === 'GET') { const snapshot = raw; gets++; await new Promise(resolve => setImmediate(resolve)); return { ok: true, json: async () => ({ result: snapshot }) }; }
    if (args[0] === 'EVAL') {
      assert.match(args[1], /redis.call\('GET', KEYS\[1\]\)/); assert.equal(args[2], '1');
      if ((raw ?? '') !== args[4]) return { ok: true, json: async () => ({ result: 0 }) };
      raw = args[5]; return { ok: true, json: async () => ({ result: 1 }) };
    }
    throw new Error('unexpected');
  };
  const store = new RedisStore({ url: 'https://redis.example', token: 'private-token', fetchImpl: fakeFetch });
  const result = await Promise.all([store.transact(state => { state.counter = (state.counter ?? 0) + 1; return state.counter; }), store.transact(state => { state.counter = (state.counter ?? 0) + 1; return state.counter; })]);
  assert.deepEqual(result.sort(), [1, 2]); assert.equal(JSON.parse(raw).counter, 2); assert.ok(gets >= 3);
  assert.ok(calls.some(args => args[0] === 'EVAL'));
});

test('storage failure stays 503 and never exposes Redis diagnostics or secrets', async () => {
  const store = { rateLimit: async () => { throw new Error('Bearer SECRET database password'); } };
  const handle = createAgentService({ env: ENV, store });
  const result = await handle({ method: 'POST', url: '/api/agent?action=login', headers: { origin: ENV.SITE_ORIGIN, 'content-type': 'application/json' }, body: { password: ENV.AGENT_ADMIN_PASSWORD } });
  assert.equal(result.status, 503); assert.deepEqual(result.body, { error: 'storage_unavailable' });
  const redis = new RedisStore({ url: 'https://test.example', token: 'secret', fetchImpl: async () => ({ ok: false }) });
  await assert.rejects(redis.command(['GET', 'key']), /storage_unavailable/);
});

test('Node request reader checks both declared and actual byte size', async () => {
  const huge = Readable.from([Buffer.alloc(MAX_BODY_BYTES + 1)]); huge.headers = {};
  await assert.rejects(readRequestBody(huge), /body_too_large/);
  const declared = Readable.from(['{}']); declared.headers = { 'content-length': String(MAX_BODY_BYTES + 1) };
  await assert.rejects(readRequestBody(declared), /body_too_large/);
  const parsed = { headers: {}, body: { text: '字'.repeat(MAX_BODY_BYTES / 2) } };
  await assert.rejects(readRequestBody(parsed), /body_too_large/);
  const fine = Readable.from(['{"workerId":"test"}']); fine.headers = {};
  assert.equal(await readRequestBody(fine), '{"workerId":"test"}');
});

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

test('reports accept up to fifteen domestic and international items while keeping old reports compatible', () => {
  assert.equal(sanitizeReport(fixtureReport()).items[0].region, undefined);
  const report = fixtureReport();
  report.items = Array.from({ length: 15 }, (_, index) => ({ ...report.items[0], itemId: `story${index + 1}`, region: index % 2 ? 'international' : 'domestic' }));
  const safe = sanitizeReport(report);
  assert.equal(safe.items.length, 15);
  assert.equal(safe.items[0].region, 'domestic'); assert.equal(safe.items[1].region, 'international');
  report.items.push({ ...report.items[0], itemId: 'story16' });
  assert.throws(() => sanitizeReport(report), /invalid_report/);
  report.items.pop(); report.items[0].region = 'private';
  assert.throws(() => sanitizeReport(report), /invalid_report/);
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

function publicReport(runId = 'public-run') {
  return { ...fixtureReport(runId), localConnections: [], learningSuggestions: [] };
}
async function finishPublic(f, job, { success = false } = {}) {
  await f.worker('poll');
  return f.worker('update', success
    ? { jobId: job.id, status: 'completed', phase: 'complete', runId: `public-${job.id}`, mailSent: true }
    : { jobId: job.id, status: 'failed', phase: 'failed', error: 'test_failed' });
}

test('public endpoints enforce same Origin, JSON, strict email and exact payload shape without requiring login', async () => {
  const f = fixture(); await f.worker('poll');
  const body = { requestId: randomUUID(), email: 'visitor@example.org' };
  for (const origin of ['', 'https://evil.example', 'https://demo.example.evil']) {
    for (const action of ['public-start', 'public-status']) assert.equal((await f.request(action, action === 'public-start' ? body : { requestId: body.requestId }, { headers: { origin } })).status, 403);
  }
  assert.equal((await f.request('public-start', body, { headers: { 'content-type': 'text/plain' } })).status, 415);
  for (const email of ['a@example.org\r\nBcc:other@example.org', 'a@example.org,b@example.org', 'a@example.org; pwd', '用户@example.org', 'a@localhost', '.a@example.org', 'a..b@example.org', 'a.@example.org', 'a@-example.org', 'a@example-.org', 'a@example.c', 'a@example.12', `${'a'.repeat(65)}@example.org`, `a@${'b'.repeat(64)}.org`, `${'a'.repeat(64)}@${'b'.repeat(63)}.${'c'.repeat(63)}.${'d'.repeat(63)}.org`, 'https://example.org', 'C:\\private\\a']) {
    const result = await f.request('public-start', { ...body, email });
    assert.equal(result.status, 400, email); assert.equal(result.body.error, 'invalid_email', email);
  }
  for (const injected of [{ mode: 'send' }, { command: 'anything' }, { path: 'C:\\private' }, { recipient: body.email }]) {
    assert.equal((await f.request('public-start', { ...body, ...injected })).status, 400);
  }
  assert.equal((await f.request('public-status', { requestId: body.requestId, email: body.email })).status, 400);
  assert.equal((await f.request('public-status', { requestId: '../../private' })).status, 400);
  assert.equal((await f.request('public-status', { requestId: body.requestId }, { method: 'GET' })).status, 405);
  assert.equal(f.store.state.publicAccepted, undefined);
  const accepted = await f.request('public-start', { ...body, email: '  Visitor+News@Example.ORG  ' });
  assert.equal(accepted.status, 202);
  assert.equal((await f.worker('poll')).body.job.recipient, 'visitor+news@example.org');
});

test('public capability responses expose only their own job and never the private report, email, run ID or owner state', async () => {
  const f = fixture(); await f.login(); await f.worker('poll');
  await f.worker('publish', { runId: 'owner-secret-run', report: fixtureReport('owner-secret-run'), mailSent: true, completedAt: new Date(f.time()).toISOString() });
  const ownerRequestId = randomUUID();
  const ownerJob = (await f.owner('start', { requestId: ownerRequestId, mode: 'preview' })).body.job;
  assert.deepEqual((await f.request('public-status', { requestId: ownerRequestId })).body, { error: 'not_found' });
  const busy = await f.request('public-start', { requestId: ownerRequestId, email: 'visitor@example.org' });
  assert.deepEqual(busy.body, { error: 'job_active' });
  await f.worker('poll'); await f.worker('update', { jobId: ownerJob.id, status: 'failed', phase: 'failed' });
  const accepted = await f.request('public-start', { requestId: ownerRequestId, email: 'visitor@example.org' });
  const job = accepted.body.job;
  assert.equal(accepted.status, 202); assert.notEqual(job.id, ownerJob.id);
  assert.deepEqual(Object.keys(job).sort(), ['createdAt', 'error', 'id', 'itemCount', 'keywords', 'mode', 'phase', 'status', 'updatedAt']);
  assert.deepEqual((await f.worker('poll')).body.job, { id: job.id, mode: 'public-send', recipient: 'visitor@example.org', itemCount: 12, keywords: '' });
  assert.equal((await finishPublic(f, job, { success: true })).status, 200);
  const result = await f.request('public-status', { requestId: ownerRequestId });
  assert.equal(result.body.job.status, 'completed'); assert.equal(result.body.job.mailSent, true);
  assert.equal((await f.owner('status')).body.latestReport.runId, 'owner-secret-run');
  assert.equal(f.store.state.history[0].recipient, undefined);
  for (const secret of ['visitor@example.org', 'owner-secret-run', 'localConnections', 'report', 'runId', 'worker', 'schedule']) assert.ok(!JSON.stringify(result.body).includes(secret), secret);
  assert.equal((await f.request('public-status', { requestId: randomUUID() })).status, 404);
});

test('public submissions are atomically idempotent, bind the normalized recipient, and only accepted new jobs consume quota', async () => {
  const f = fixture(); const body = { requestId: randomUUID(), email: 'Visitor@Example.org' };
  assert.deepEqual((await f.request('public-start', body)).body, { error: 'worker_offline' });
  assert.equal(f.store.state.publicAccepted, undefined);
  await f.worker('poll');
  const results = await Promise.all([f.request('public-start', body), f.request('public-start', { ...body, email: 'visitor@example.org' })]);
  assert.deepEqual(results.map(item => item.status), [202, 202]);
  assert.equal(results[0].body.job.id, results[1].body.job.id); assert.equal(f.store.state.publicAccepted.length, 1);
  assert.deepEqual((await f.request('public-start', { ...body, email: 'different@example.org' })).body, { error: 'request_conflict' });
  assert.deepEqual((await f.request('public-start', { requestId: randomUUID(), email: 'different@example.org' })).body, { error: 'job_active' });
  assert.equal(f.store.state.publicAccepted.length, 1);
  await finishPublic(f, results[0].body.job);
  f.advance(ONLINE_MS + 1);
  const retry = await f.request('public-start', body);
  assert.equal(retry.status, 202); assert.equal(retry.body.job.status, 'failed'); assert.equal(f.store.state.publicAccepted.length, 1);
  assert.equal(retry.body.job.error, 'run_failed');
  const stored = JSON.stringify(f.store.state);
  assert.ok(!stored.includes('visitor@example.org')); assert.ok(!stored.includes('203.0.113.9'));
  assert.match(f.store.state.publicAccepted[0].emailHash, /^[a-f0-9]{64}$/);
  assert.match(f.store.state.publicAccepted[0].ipHash, /^[a-f0-9]{64}$/);
});

test('public quotas independently bound recipient, IP and global daily acceptance with a retry time', async () => {
  const f = fixture(); await f.worker('poll');
  for (let index = 0; index < 3; index++) {
    const result = await f.request('public-start', { requestId: randomUUID(), email: 'visitor0@example.org' });
    assert.equal(result.status, 202); await finishPublic(f, result.body.job, { success: true });
  }
  let limited = await f.request('public-start', { requestId: randomUUID(), email: 'visitor0@example.org' }, { ip: '203.0.113.10' });
  assert.equal(limited.status, 429); assert.equal(limited.body.error, 'public_email_limit');
  assert.equal(limited.body.retryAfterSeconds, 86400); assert.equal(limited.headers['Retry-After'], '86400');
  for (let index = 3; index < 9; index++) {
    const result = await f.request('public-start', { requestId: randomUUID(), email: `visitor${index}@example.org` });
    assert.equal(result.status, 202); await finishPublic(f, result.body.job, { success: true });
  }
  limited = await f.request('public-start', { requestId: randomUUID(), email: 'visitor9@example.org' });
  assert.equal(limited.status, 429); assert.equal(limited.body.error, 'public_ip_limit');
  for (let index = 9; index < 30; index++) {
    const result = await f.request('public-start', { requestId: randomUUID(), email: `visitor${index}@example.org` }, { ip: `203.0.113.${index + 10}` });
    assert.equal(result.status, 202); await finishPublic(f, result.body.job, { success: true });
  }
  limited = await f.request('public-start', { requestId: randomUUID(), email: 'visitor30@example.org' }, { ip: '203.0.113.100' });
  assert.equal(limited.status, 429); assert.equal(limited.body.error, 'public_daily_limit'); assert.equal(f.store.state.publicAccepted.length, 30);
  f.advance(24 * 60 * 60_000); await f.worker('poll');
  assert.equal((await f.request('public-start', { requestId: randomUUID(), email: 'visitor0@example.org' })).status, 202);
  assert.equal(f.store.state.publicAccepted.length, 1);
});

test('concurrent visitors cannot bypass busy or acceptance quotas', async () => {
  const f = fixture(); await f.worker('poll');
  const responses = await Promise.all(Array.from({ length: 12 }, (_, i) => f.request('public-start', { requestId: randomUUID(), email: `visitor${i}@example.org` }, { ip: `203.0.113.${i + 1}` })));
  assert.equal(responses.filter(item => item.status === 202).length, 1);
  assert.equal(responses.filter(item => item.body.error === 'job_active').length, 11);
  assert.equal(f.store.state.publicAccepted.length, 1);
});

test('public worker cannot read local material, report unsent success, or replace the private latest report', async () => {
  const f = fixture(); await f.worker('poll');
  const job = (await f.request('public-start', { requestId: randomUUID(), email: 'visitor@example.org' })).body.job;
  await f.worker('poll');
  assert.equal((await f.worker('update', { jobId: job.id, status: 'running', phase: 'local-read' })).body.error, 'invalid_job_transition');
  // The Python worker includes an empty optional localContext for public reports.
  const done = { jobId: job.id, status: 'completed', phase: 'complete', runId: 'public-run', mailSent: true, report: { ...publicReport(), localContext: '' } };
  assert.equal((await f.worker('update', { ...done, mailSent: false })).body.error, 'invalid_job_transition');
  assert.equal((await f.worker('update', { ...done, report: fixtureReport('public-run') })).body.error, 'public_report_private_data');
  for (const field of ['localContext', 'localRelevanceNote']) assert.equal((await f.worker('update', { ...done, report: { ...publicReport(), [field]: '本地私人资料' } })).body.error, 'public_report_private_data');
  assert.equal(f.store.state.activeJob.status, 'running'); assert.equal(f.store.state.latestReport, null);
  assert.equal((await f.worker('update', done)).status, 200);
  assert.equal((await f.worker('update', done)).status, 200);
  assert.equal((await f.worker('update', { ...done, report: fixtureReport('public-run') })).body.error, 'public_report_private_data');
  assert.equal(f.store.state.latestReport, null);
  assert.equal((await f.worker('publish', { runId: 'public-run', report: publicReport(), mailSent: true, completedAt: new Date(f.time()).toISOString() })).body.error, 'public_report_publish_forbidden');
});

test('public insufficient-news failure preserves the safe actionable error and unsent result', async () => {
  const f = fixture(); await f.worker('poll');
  const requestId = randomUUID();
  const job = (await f.request('public-start', { requestId, email: 'visitor@example.org' })).body.job;
  await f.worker('poll');
  assert.equal((await f.worker('update', { jobId: job.id, status: 'failed', phase: 'failed', error: 'insufficient_news', mailSent: false })).status, 200);
  const result = await f.request('public-status', { requestId });
  assert.equal(result.body.job.status, 'failed');
  assert.equal(result.body.job.error, 'insufficient_news');
  assert.equal(result.body.job.mailSent, false);
  assert.equal(result.body.job.report, undefined);
  assert.equal(result.body.job.recipient, undefined);
});

test('public queue expiration and capability status remain honest after owner history displaces the job', async () => {
  const f = fixture(); await f.worker('poll'); await f.login();
  const requestId = randomUUID(); const body = { requestId, email: 'visitor@example.org' };
  const queued = await f.request('public-start', body);
  f.advance(QUEUE_TTL_MS);
  const expired = await f.request('public-status', { requestId });
  assert.equal(expired.body.job.status, 'failed'); assert.equal(expired.body.job.error, 'queue_expired');
  assert.equal((await f.worker('poll')).body.job, null);
  for (let index = 0; index < 23; index++) {
    const job = (await f.owner('start', { requestId: randomUUID(), mode: 'preview' })).body.job;
    await f.worker('poll'); await f.worker('update', { jobId: job.id, status: 'failed', phase: 'failed' });
  }
  assert.ok(!f.store.state.history.some(job => job.id === queued.body.job.id));
  assert.deepEqual((await f.request('public-status', { requestId })).body, expired.body);
  assert.equal((await f.request('public-start', body)).body.job.id, queued.body.job.id);
  f.advance(24 * 60 * 60_000);
  assert.equal((await f.request('public-status', { requestId })).status, 404);
});

test('public preferences validate counts and plain keywords, canonicalize whitespace and reach only the fixed worker fields', async () => {
  const f = fixture(); await f.worker('poll');
  const body = { requestId: randomUUID(), email: 'reader@example.org' };
  for (const itemCount of [4, 21, 5.5, '12', null, true, [], {}]) {
    const result = await f.request('public-start', { ...body, itemCount });
    assert.equal(result.status, 400); assert.equal(result.body.error, 'invalid_item_count');
  }
  for (const keywords of [null, 12, [], {}, 'x'.repeat(201), 'AI\0robot', 'AI\u202e12', 'AI\u00adrobot', 'AI\u0085robot', 'AI\ud800robot']) {
    const result = await f.request('public-start', { ...body, keywords });
    assert.equal(result.status, 400, String(keywords)); assert.equal(result.body.error, 'invalid_keywords', String(keywords));
  }
  const accepted = await f.request('public-start', { ...body, itemCount: 20, keywords: '  AI,\n\t机器人，  Node.js C++ 代码执行漏洞  ' });
  assert.equal(accepted.status, 202);
  const preferences = { itemCount: 20, keywords: 'AI, 机器人， Node.js C++ 代码执行漏洞' };
  assert.equal(accepted.body.job.itemCount, preferences.itemCount); assert.equal(accepted.body.job.keywords, preferences.keywords);
  assert.deepEqual((await f.worker('poll')).body.job, { id: accepted.body.job.id, mode: 'public-send', recipient: 'reader@example.org', ...preferences });
  assert.equal((await f.worker('update', { jobId: accepted.body.job.id, status: 'completed', phase: 'complete', runId: 'public-twenty', mailSent: true })).status, 200);
  assert.equal(f.store.state.latestReport, null);
  const second = await f.request('public-start', { requestId: randomUUID(), email: body.email, itemCount: 5, keywords: '字'.repeat(200) });
  assert.equal(second.status, 202); assert.equal(second.body.job.keywords.length, 200);
  assert.equal(f.store.state.publicAccepted.length, 2);
});

test('keyword syntax remains literal data for the worker without rejecting URLs or command topics', async () => {
  const f = fixture(); await f.worker('poll');
  const keywords = 'https://example.org <script> curl bash `whoami` $(whoami) 执行 shell 命令 run python';
  const accepted = await f.request('public-start', { requestId: randomUUID(), email: 'reader@example.org', keywords });
  assert.equal(accepted.status, 202);
  assert.equal(accepted.body.job.keywords, keywords);
  const polled = (await f.worker('poll')).body.job;
  assert.equal(polled.keywords, keywords);
  assert.deepEqual(Object.keys(polled).sort(), ['id', 'itemCount', 'keywords', 'mode', 'recipient']);
});

test('a failed public run cannot refund quota using a stale in-progress unsent flag', async () => {
  const f = fixture(); await f.worker('poll');
  const requestId = randomUUID();
  const accepted = await f.request('public-start', { requestId, email: 'reader@example.org' });
  const jobId = accepted.body.job.id;
  await f.worker('poll');
  assert.equal((await f.worker('update', { jobId, status: 'running', phase: 'send', mailSent: false })).status, 200);
  assert.equal((await f.worker('update', { jobId, status: 'failed', phase: 'failed', error: 'run_failed' })).status, 200);
  const status = await f.request('public-status', { requestId });
  assert.equal(status.body.job.deliveryUncertain, true);
  assert.equal(status.body.job.mailSent, undefined);
  assert.equal(f.store.state.publicAccepted.length, 1);
});

test('public request identity binds normalized recipient and preferences including legacy defaults', async () => {
  const f = fixture(); await f.worker('poll');
  const body = { requestId: randomUUID(), email: 'reader@example.org', itemCount: 8, keywords: 'AI,\n机器人' };
  const accepted = await f.request('public-start', body);
  assert.equal((await f.request('public-start', { ...body, email: 'READER@example.org', keywords: ' AI,  机器人 ' })).body.job.id, accepted.body.job.id);
  for (const change of [{ itemCount: 9 }, { keywords: '量子' }, { email: 'other@example.org' }]) {
    assert.deepEqual((await f.request('public-start', { ...body, ...change })).body, { error: 'request_conflict' });
  }
  assert.equal(f.store.state.publicAccepted.length, 1);
  await finishPublic(f, accepted.body.job, { success: true });
  const oldBody = { requestId: randomUUID(), email: body.email };
  const old = await f.request('public-start', oldBody);
  for (const item of [f.store.state.activeJob, f.store.state.requests[`public:${oldBody.requestId}`]]) {
    delete item.itemCount; delete item.keywords;
  }
  delete f.store.state.publicAccepted[1].jobId;
  assert.deepEqual((await f.worker('poll')).body.job, { id: old.body.job.id, mode: 'public-send', recipient: body.email, itemCount: 12, keywords: '' });
  assert.equal((await f.request('public-start', { ...oldBody, itemCount: 12, keywords: ' \t ' })).body.job.id, old.body.job.id);
  assert.equal((await f.request('public-start', { ...oldBody, itemCount: 13 })).body.error, 'request_conflict');
  assert.equal(f.store.state.publicAccepted.length, 2);
});

test('definitely unsent public failures refund once while completed and uncertain deliveries retain quota', async () => {
  const f = fixture(); await f.worker('poll');
  const email = 'reader@example.org';
  const request = () => f.request('public-start', { requestId: randomUUID(), email });
  const failed = (await request()).body.job; await f.worker('poll');
  const failure = { jobId: failed.id, status: 'failed', phase: 'failed', error: 'no_matching_news', mailSent: false };
  assert.equal((await f.worker('update', failure)).status, 200);
  assert.equal((await f.worker('update', failure)).status, 200);
  assert.equal(f.store.state.publicAccepted.length, 0);
  assert.equal(f.store.state.history[0].mailSent, false);
  const sent = (await request()).body.job; await finishPublic(f, sent, { success: true });
  for (const error of ['delivery_unknown_check_local', 'worker_interrupted_check_local']) {
    const uncertain = (await request()).body.job; await f.worker('poll');
    // Existing workers deliberately omit mailSent while the outcome is uncertain.
    assert.equal((await f.worker('update', { jobId: uncertain.id, status: 'failed', phase: 'failed', error })).status, 200);
  }
  assert.equal(f.store.state.publicAccepted.length, 3);
  assert.equal((await request()).body.error, 'public_email_limit');
  const records = Object.entries(f.store.state.requests).filter(([, value]) => value.mode === 'public-send');
  const failedId = records.find(([, value]) => value.jobId === failed.id)[0].slice('public:'.length);
  const failedStatus = (await f.request('public-status', { requestId: failedId })).body.job;
  assert.equal(failedStatus.error, 'no_matching_news'); assert.equal(failedStatus.deliveryUncertain, false);
  for (const [key, remembered] of records.filter(([, value]) => ![sent.id, failed.id].includes(value.jobId))) {
    const status = (await f.request('public-status', { requestId: key.slice('public:'.length) })).body.job;
    assert.equal(status.deliveryUncertain, true); assert.equal(status.mailSent, undefined);
  }
});

test('uncertainty errors cannot accidentally refund even with a false flag; unclaimed expired jobs do refund', async () => {
  const f = fixture(); await f.worker('poll');
  const first = await f.request('public-start', { requestId: randomUUID(), email: 'reader@example.org' });
  await f.worker('poll');
  await f.worker('update', { jobId: first.body.job.id, status: 'failed', phase: 'failed', error: 'delivery_unknown_check_local', mailSent: false });
  assert.equal(f.store.state.publicAccepted.length, 1);
  const requestId = randomUUID();
  await f.request('public-start', { requestId, email: 'reader@example.org' });
  assert.equal(f.store.state.publicAccepted.length, 2);
  f.advance(QUEUE_TTL_MS);
  const result = await f.request('public-status', { requestId });
  assert.equal(result.body.job.error, 'queue_expired'); assert.equal(result.body.job.mailSent, false); assert.equal(result.body.job.deliveryUncertain, false);
  assert.equal(f.store.state.publicAccepted.length, 1);
});

test('same-recipient last quota slot is atomic and rolling expiry releases only the aged reservation', async () => {
  const f = fixture(); await f.worker('poll');
  const email = 'reader@example.org';
  const start = () => f.request('public-start', { requestId: randomUUID(), email });
  const first = (await start()).body.job; await finishPublic(f, first, { success: true });
  f.advance(60_000); await f.worker('poll');
  const second = (await start()).body.job; await finishPublic(f, second, { success: true });
  const contenders = await Promise.all([start(), start(), start()]);
  assert.equal(contenders.filter(item => item.status === 202).length, 1);
  assert.equal(f.store.state.publicAccepted.length, 3);
  await finishPublic(f, contenders.find(item => item.status === 202).body.job, { success: true });
  assert.equal((await start()).body.error, 'public_email_limit');
  f.advance(24 * 60 * 60_000 - 60_000); await f.worker('poll');
  assert.equal((await start()).status, 202);
  assert.equal(f.store.state.publicAccepted.length, 3);
});

test('legacy quota entries without job IDs count and can refund only when matched to a definitely unsent job', async () => {
  const f = fixture(); await f.worker('poll');
  const body = { requestId: randomUUID(), email: 'reader@example.org' };
  const job = (await f.request('public-start', body)).body.job;
  delete f.store.state.publicAccepted[0].jobId;
  await f.worker('poll');
  await f.worker('update', { jobId: job.id, status: 'failed', phase: 'failed', error: 'insufficient_news', mailSent: false });
  assert.equal(f.store.state.publicAccepted.length, 0);
  const accepted = await f.request('public-start', { ...body, requestId: randomUUID() });
  await finishPublic(f, accepted.body.job, { success: true });
  delete f.store.state.publicAccepted[0].jobId;
  assert.equal((await f.request('public-start', { ...body, requestId: randomUUID() })).status, 202);
  assert.equal(f.store.state.publicAccepted.length, 2);
});

test('private completion still requires a report and public completion still requires confirmed sending', async () => {
  const f = fixture(); const privateJob = await started(f); await f.worker('poll');
  assert.equal((await f.worker('update', { jobId: privateJob.id, status: 'completed', phase: 'complete', mailSent: false })).body.error, 'report_required');
  await f.worker('update', { jobId: privateJob.id, status: 'failed', phase: 'failed' });
  const publicJob = (await f.request('public-start', { requestId: randomUUID(), email: 'reader@example.org', itemCount: 20 })).body.job;
  await f.worker('poll');
  assert.equal((await f.worker('update', { jobId: publicJob.id, status: 'completed', phase: 'complete', runId: 'twenty-public' })).body.error, 'invalid_job_transition');
  assert.equal((await f.worker('update', { jobId: publicJob.id, status: 'completed', phase: 'complete', runId: 'twenty-public', mailSent: true })).status, 200);
  assert.equal(f.store.state.latestReport, null);
});

import { buildIndex, attachVectors, search } from './search.mjs';
import { createEncoder, encodeQuery } from './embedding.mjs';
import { MODEL_ID, MODEL_REVISION, MODEL_DIMENSION } from './model-config.mjs';

let index, encoder, initialized = false;
const base = new URL('./', import.meta.url);
const send = data => self.postMessage(data);
const capabilities = () => ({ bm25: Boolean(index), dense: Boolean(encoder && index?.vectors) });
const stats = () => ({ chunkCount: index.chunks.length, companies: index.companies, dimension: MODEL_DIMENSION, model: MODEL_ID });

async function fetchRequired(path, type = 'json') {
  const response = await fetch(new URL(path, base));
  if (!response.ok) throw new Error(`资源 ${path} 加载失败（${response.status}）。`);
  return type === 'buffer' ? response.arrayBuffer() : response.json();
}
async function hash(buffer) { return [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(v => v.toString(16).padStart(2, '0')).join(''); }

async function init(requestId) {
  if (initialized) { if (index) send({ type: 'ready', requestId, stats: stats(), capabilities: capabilities(), partial: !capabilities().dense }); return; }
  initialized = true;
  send({ type: 'progress', requestId, stage: 'chunks', message: '正在载入年报文本与关键词索引。' });
  let raw;
  try {
    raw = await fetchRequired('data/chunks.json', 'buffer');
    const chunks = JSON.parse(new TextDecoder().decode(raw).replace(/^\uFEFF/, ''));
    index = buildIndex(chunks);
    send({ type: 'ready', requestId, stats: stats(), capabilities: capabilities(), partial: true });
  } catch (error) { initialized = false; throw error; }
  try {
    send({ type: 'progress', requestId, stage: 'dense', message: '关键词检索已可用，正在载入本地语义模型。' });
    const [metadata, vectorBytes] = await Promise.all([fetchRequired('data/index-meta.json'), fetchRequired('data/vectors.bin', 'buffer')]);
    if (metadata.modelRevision !== MODEL_REVISION || metadata.dimension !== MODEL_DIMENSION || metadata.chunkCount !== index.chunks.length || metadata.chunkIds?.length !== index.chunks.length || !metadata.chunkIds.every((id, i) => id === index.chunks[i].id)) throw new Error('向量索引与当前文本或模型版本不一致。');
    if (await hash(raw) !== metadata.chunksSha256 || await hash(vectorBytes) !== metadata.vectorsSha256) throw new Error('索引文件校验不一致，请重新建库。');
    const view = new DataView(vectorBytes); const vectors = new Float32Array(vectorBytes.byteLength / 4);
    for (let i = 0; i < vectors.length; i++) vectors[i] = view.getFloat32(i * 4, true);
    attachVectors(index, vectors, metadata.dimension);
    encoder = await createEncoder({ modelPath: new URL('models/', base).href, wasmPath: new URL('vendor/', base).href, browser: true,
      progress: info => send({ type: 'progress', requestId, stage: 'model', message: '正在载入本地语义模型；问题在浏览器内编码。', progress: info.progress ?? null }),
    });
    send({ type: 'ready', requestId, stats: stats(), capabilities: capabilities(), partial: false });
  } catch (error) {
    send({ type: 'progress', requestId, stage: 'dense-unavailable', message: `语义检索暂不可用：${error.message} 可选择 BM25 关键词检索。` });
  }
}

let queries = Promise.resolve();
self.onmessage = ({ data }) => {
  const { type, requestId } = data ?? {};
  const failure = error => send({ type: 'error', requestId, code: error.message === 'DENSE_NOT_READY' ? 'DENSE_NOT_READY' : 'RETRIEVAL_ERROR', message: error.message === 'DENSE_NOT_READY' ? '语义模型尚不可用；请选择 BM25 模式或等待模型载入。' : error.message });
  if (type === 'init') { init(requestId).catch(failure); return; }
  if (type !== 'search') { failure(new Error('未知 worker 请求。')); return; }
  queries = queries.then(async () => {
    if (!index) throw new Error('请先初始化检索器。');
    const mode = data.mode ?? 'hybrid';
    if (mode !== 'bm25' && !capabilities().dense) throw new Error('DENSE_NOT_READY');
    const start = performance.now();
    const queryVector = mode === 'bm25' ? null : await encodeQuery(encoder, data.query);
    const encoded = performance.now();
    const result = search(index, data.query, queryVector, data);
    send({ type: 'results', requestId, query: data.query, mode, ...result, timings: { embeddingMs: encoded - start, retrievalMs: performance.now() - encoded, totalMs: performance.now() - start } });
  }).catch(failure);
};

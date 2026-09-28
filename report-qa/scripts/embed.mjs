import { readFile, writeFile, mkdir, rename, open, unlink } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createEncoder, encodeDocument } from '../lib/embedding.mjs';
import { embeddingText, buildIndex, DEFAULTS } from '../lib/search.mjs';
import { MODEL_ID, MODEL_REVISION, MODEL_DIMENSION, MAX_TOKENS, QUERY_PREFIX } from '../lib/model-config.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const input = resolve(process.argv[2] ?? join(root, 'site/data/chunks.json'));
const out = resolve(process.argv[3] ?? dirname(input));
const raw = await readFile(input);
const chunks = JSON.parse(raw.toString('utf8').replace(/^\uFEFF/, ''));
const lexical = buildIndex(chunks);
await mkdir(out, { recursive: true });
const inputHash = createHash('sha256').update(raw).digest('hex');
const checkpointKey = `${inputHash}:${MODEL_REVISION}:q8:cls:token-weighted:${MAX_TOKENS}`;
const checkpointFile = join(out, '.embed-checkpoint.json');
const partialFile = join(out, '.embed-vectors.partial');
const matrix = new Float32Array(chunks.length * MODEL_DIMENSION);
let spanCounts = [], resumeAt = 0;
try {
  const checkpoint = JSON.parse(await readFile(checkpointFile, 'utf8'));
  const partial = await readFile(partialFile);
  if (checkpoint.key === checkpointKey && Number.isInteger(checkpoint.completed) && checkpoint.completed <= chunks.length && checkpoint.completed > 0 && partial.length >= checkpoint.completed * MODEL_DIMENSION * 4 && checkpoint.spanCounts.length === checkpoint.completed) {
    resumeAt = checkpoint.completed; spanCounts = checkpoint.spanCounts;
    for (let i = 0; i < resumeAt * MODEL_DIMENSION; i++) matrix[i] = partial.readFloatLE(i * 4);
    console.log(JSON.stringify({ status: 'resuming', completed: resumeAt, total: chunks.length }));
  }
} catch { /* Missing or unmatched checkpoints are never reused. */ }
const encoder = await createEncoder({ modelPath: join(root, 'site/models') + '/' });
let partialHandle;
try { partialHandle = await open(partialFile, 'r+'); } catch { partialHandle = await open(partialFile, 'w+'); }
await partialHandle.truncate(resumeAt * MODEL_DIMENSION * 4);
let persisted = resumeAt;
async function checkpoint(completed) {
  const block = Buffer.alloc((completed - persisted) * MODEL_DIMENSION * 4);
  for (let i = 0; i < block.length / 4; i++) block.writeFloatLE(matrix[persisted * MODEL_DIMENSION + i], i * 4);
  await partialHandle.write(block, 0, block.length, persisted * MODEL_DIMENSION * 4);
  await partialHandle.sync();
  await writeFile(checkpointFile + '.tmp', JSON.stringify({ key: checkpointKey, completed, spanCounts }));
  await rename(checkpointFile + '.tmp', checkpointFile);
  persisted = completed;
}
const started = Date.now();
try {
  for (let i = resumeAt; i < chunks.length; i++) {
    const result = await encodeDocument(encoder, embeddingText(chunks[i]));
    matrix.set(result.vector, i * MODEL_DIMENSION);
    spanCounts.push(result.spanCount);
    if ((i + 1) % 25 === 0 || i + 1 === chunks.length) {
      await checkpoint(i + 1);
      console.log(JSON.stringify({ completed: i + 1, total: chunks.length, elapsedSeconds: Math.round((Date.now() - started) / 1000) }));
    }
  }
} finally { await encoder.dispose(); await partialHandle.close(); }
const bytes = Buffer.alloc(matrix.length * 4);
for (let i = 0; i < matrix.length; i++) bytes.writeFloatLE(matrix[i], i * 4);
const meta = {
  schemaVersion: 1, model: MODEL_ID, modelRevision: MODEL_REVISION, transformersVersion: '3.8.1', dtype: 'q8',
  dimension: MODEL_DIMENSION, chunkCount: chunks.length, chunkIds: chunks.map(c => c.id),
  pooling: 'cls', normalization: 'l2', documentLongText: 'token-weighted mean of normalized CLS vectors; then L2 normalize',
  maxTokens: MAX_TOKENS, queryPrefix: QUERY_PREFIX, spanCounts, splitChunkCount: spanCounts.filter(n => n > 1).length,
  chunksSha256: inputHash, vectorsSha256: createHash('sha256').update(bytes).digest('hex'),
  vectorFormat: 'float32-little-endian-row-major', bm25: DEFAULTS, tokenizer: 'NFKC lowercase; Han bigrams; Latin words; numeric runs',
  companies: lexical.companies, createdAtUtc: new Date().toISOString(), durationSeconds: (Date.now() - started) / 1000,
};
await writeFile(join(out, 'vectors.bin.tmp'), bytes);
await rename(join(out, 'vectors.bin.tmp'), join(out, 'vectors.bin'));
await writeFile(join(out, 'index-meta.json'), JSON.stringify(meta, null, 2) + '\n');
await Promise.all([unlink(checkpointFile).catch(() => {}), unlink(partialFile).catch(() => {})]);
console.log(JSON.stringify({ status: 'complete', chunks: chunks.length, dimension: MODEL_DIMENSION, splitChunks: meta.splitChunkCount, vectorBytes: bytes.length }));

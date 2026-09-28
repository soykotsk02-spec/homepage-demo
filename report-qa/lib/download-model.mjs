import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { MODEL_ID, MODEL_FOLDER, MODEL_REVISION } from './model-config.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', 'onnx/model_quantized.onnx'];
const base = resolve(root, 'site/models', MODEL_FOLDER);
const manifest = { model: MODEL_ID, revision: MODEL_REVISION, dtype: 'q8', files: [] };
const expected = JSON.parse(await readFile(resolve(root, 'lib/model-manifest.json'), 'utf8'));
if (expected.model !== MODEL_ID || expected.revision !== MODEL_REVISION) throw new Error('Model manifest revision mismatch.');
for (const name of files) {
  const destination = resolve(base, name);
  const url = `https://huggingface.co/${MODEL_ID}/resolve/${MODEL_REVISION}/${name}`;
  let bytes;
  try { bytes = await readFile(destination); } catch {}
  const entry = expected.files.find(file => file.path === name);
  if (!entry) throw new Error(`Missing model checksum (${name}).`);
  if (bytes && createHash('sha256').update(bytes).digest('hex') !== entry.sha256) bytes = null;
  if (!bytes) {
    console.log(`Downloading ${name}`);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Model download failed: HTTP ${response.status} (${name})`);
    bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length >= 100_000_000) throw new Error('模型文件超过本仓库的单文件大小上限。');
    if (bytes.length !== entry.bytes || createHash('sha256').update(bytes).digest('hex') !== entry.sha256) throw new Error(`Model checksum verification failed (${name}).`);
    await mkdir(dirname(destination), { recursive: true });
    await writeFile(destination, bytes);
  }
  manifest.files.push({ path: name, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), source: url });
}
await writeFile(resolve(base, 'model-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify({ downloaded: manifest.files.length, revision: MODEL_REVISION, totalBytes: manifest.files.reduce((sum, f) => sum + f.bytes, 0) }));

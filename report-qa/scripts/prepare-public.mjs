// Restore pinned public model/runtime assets; no Python/model inference at deploy time.
import { readFile, writeFile, mkdir, rename, access } from 'node:fs/promises';
import { dirname, resolve, join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { gunzipSync } from 'node:zlib';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const hash = b => createHash('sha256').update(b).digest('hex');
async function exists(path) { try { await access(path); return true; } catch { return false; } }
function inside(base, relative) {
  const target = resolve(base, relative);
  if (!target.startsWith(resolve(base) + sep)) throw new Error('Unsafe public asset path');
  return target;
}
async function restore(manifestPath) {
  const directory = dirname(manifestPath);
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  for (const file of manifest.files) {
    const target = inside(directory, file.path);
    if (await exists(target) && hash(await readFile(target)) === file.sha256) continue;
    const url = new URL(file.source);
    if (url.protocol !== 'https:' || !['huggingface.co', 'cdn.jsdelivr.net'].includes(url.hostname)) throw new Error('Unexpected asset source');
    let bytes;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const response = await fetch(url, { signal: AbortSignal.timeout(180000) });
        if (!response.ok) throw new Error(`Download ${response.status}: ${url}`);
        bytes = Buffer.from(await response.arrayBuffer());
        if (bytes.length !== file.bytes || hash(bytes) !== file.sha256) throw new Error('Asset checksum mismatch: ' + file.path);
        break;
      } catch (error) { if (attempt === 2) throw error; }
    }
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target + '.partial', bytes);
    await rename(target + '.partial', target);
    console.log('Restored verified public asset: ' + file.path);
  }
}
// Restore packaged binaries before verifying the upstream manifests. A complete
// checkout builds offline: small config/.mjs files are committed, while ONNX/WASM
// and the public index are reconstructed from checksum-verified Git parts.
const packagePath = join(root, 'datasets/package.json');
if (await exists(packagePath)) {
  const pack = JSON.parse(await readFile(packagePath, 'utf8'));
  for (const item of pack.outputs) {
    const outputRoot = inside(join(root, 'site'), item.root ?? 'data');
    const target = inside(outputRoot, item.path);
    if (await exists(target)) {
      const current = await readFile(target);
      if (current.length === item.bytes && hash(current) === item.sha256) continue;
    }
    const parts = [];
    for (const part of item.parts) {
      const data = await readFile(inside(join(root, 'datasets'), part.path));
      if (data.length !== part.bytes || hash(data) !== part.sha256) throw new Error('Packaged asset checksum mismatch: ' + part.path);
      parts.push(data);
    }
    const buffer = Buffer.concat(parts);
    if (!['gzip', 'none'].includes(item.compression)) throw new Error('Unknown packaged asset compression');
    const restored = item.compression === 'gzip' ? gunzipSync(buffer) : buffer;
    if (restored.length !== item.bytes || hash(restored) !== item.sha256) throw new Error('Restored asset checksum mismatch: ' + item.path);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target + '.partial', restored);
    await rename(target + '.partial', target);
  }
}
await Promise.all([
  restore(join(root, 'site/models/bge-small-zh-v1.5/model-manifest.json')),
  restore(join(root, 'site/vendor/runtime-manifest.json')),
]);

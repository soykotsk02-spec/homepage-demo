import { build } from 'esbuild';
import { mkdir, cp, readdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const transformersRoot = dirname(dirname(require.resolve('@huggingface/transformers')));
const vendor = join(root, 'site/vendor');
await mkdir(vendor, { recursive: true });
await build({ entryPoints: [join(root, 'lib/worker-entry.mjs')], outfile: join(root, 'site/worker.js'), bundle: true, format: 'esm', platform: 'browser', target: ['es2022'], minify: true, sourcemap: false });
const ortEntry = require.resolve('onnxruntime-web', { paths: [transformersRoot] });
const ortPackage = join(dirname(dirname(ortEntry)), 'package.json');
const ortDist = join(dirname(ortPackage), 'dist');
let copied = 0;
for (const name of await readdir(ortDist)) {
  if (/^ort-wasm.*\.(wasm|mjs)$/.test(name)) { await cp(join(ortDist, name), join(vendor, name)); copied++; }
}
await cp(join(transformersRoot, 'LICENSE'), join(vendor, 'transformers-LICENSE.txt'));
const ortLicense = join(root, 'lib/onnxruntime-LICENSE.txt');
await cp(ortLicense, join(vendor, 'onnxruntime-LICENSE.txt'));
const pkg = JSON.parse(await readFile(ortPackage, 'utf8'));
const runtimeFiles = [];
for (const name of await readdir(vendor)) if (/^ort-wasm.*\.(wasm|mjs)$/.test(name)) {
  const bytes = await readFile(join(vendor, name));
  runtimeFiles.push({ path: name, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), source: `https://cdn.jsdelivr.net/npm/onnxruntime-web@${pkg.version}/dist/${name}` });
}
await writeFile(join(vendor, 'runtime-manifest.json'), JSON.stringify({ package: 'onnxruntime-web', version: pkg.version, files: runtimeFiles }, null, 2) + '\n');
await writeFile(join(vendor, 'README.md'), `# Browser runtime\n\nLocally bundled @huggingface/transformers 3.8.1 (Apache-2.0) and onnxruntime-web ${pkg.version} (MIT). License texts are included here.\n\nBuilt with scripts/build-browser.mjs. WASM binaries are served from this directory; model execution does not call a hosted inference API.\n`);
console.log(JSON.stringify({ status: 'built', runtimeFiles: copied, worker: 'site/worker.js' }));

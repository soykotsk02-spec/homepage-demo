import { mkdir, copyFile, rm, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'public');
await import('../report-qa/scripts/prepare-public.mjs');
if (path.dirname(output) !== root || path.basename(output) !== 'public') {
  throw new Error('Refusing to clean an unexpected output directory.');
}
// Remove only this generated output folder so stale files cannot be published.
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
// Only these reviewed frontend files are public. Runtime data never enters this folder.
for (const name of ['index.html', 'agents.html', 'agents.js', 'agents.css', 'schedule-countdown.js']) {
  await copyFile(path.join(root, name), path.join(output, name));
}
const researchRoot = path.join(root, 'report-qa', 'site');
const files = JSON.parse(await readFile(path.join(root, 'report-qa', 'public-files.json'), 'utf8'));
for (const name of files) {
  if (typeof name !== 'string' || name.includes('..') || path.isAbsolute(name) || !/^[a-zA-Z0-9_./-]+$/.test(name)) throw new Error('Unsafe public filename');
  const target = path.join(output, 'report-qa', name);
  await mkdir(path.dirname(target), { recursive: true });
  await copyFile(path.join(researchRoot, name), target);
}
console.log(`Public website built: five existing frontend files and ${files.length} reviewed research assets.`);

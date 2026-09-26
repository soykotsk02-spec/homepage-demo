import { mkdir, copyFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'public');
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
console.log('Public website built from five explicit frontend files.');

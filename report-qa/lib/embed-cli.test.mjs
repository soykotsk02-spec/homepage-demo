import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve, join, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
test('Real encoder CLI writes normalized vectors and resumes matching checkpoints', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'report-qa-embed-'));
  try {
    const chunks = [
      { id: 'fixture-a', company: '甲银行', reportYear: 2025, text: '金融科技投入与数据治理。' },
      { id: 'fixture-b', company: '乙银行', reportYear: 2025, text: '营业收入和贷款总额均稳步增长。'.repeat(60) },
    ];
    const input = join(folder, 'chunks.json');
    await writeFile(input, JSON.stringify(chunks));
    const execute = () => spawnSync(process.execPath, [join(root, 'scripts/embed.mjs'), input, folder], { encoding: 'utf8', timeout: 60000 });
    let result = execute(); assert.equal(result.status, 0, result.stderr);
    const metadata = JSON.parse(await readFile(join(folder, 'index-meta.json'), 'utf8'));
    const vectors = await readFile(join(folder, 'vectors.bin'));
    assert.equal(vectors.length, 2 * 512 * 4); assert.ok(metadata.splitChunkCount >= 1);
    for (let row = 0; row < 2; row++) {
      let norm = 0; for (let col = 0; col < 512; col++) norm += vectors.readFloatLE((row * 512 + col) * 4) ** 2;
      assert.ok(Math.abs(Math.sqrt(norm) - 1) < 0.0001);
    }
    const key = `${metadata.chunksSha256}:${metadata.modelRevision}:q8:cls:token-weighted:${metadata.maxTokens}`;
    await writeFile(join(folder, '.embed-checkpoint.json'), JSON.stringify({ key, completed: 1, spanCounts: metadata.spanCounts.slice(0, 1) }));
    await writeFile(join(folder, '.embed-vectors.partial'), vectors.subarray(0, 512 * 4));
    result = execute(); assert.equal(result.status, 0, result.stderr); assert.match(result.stdout, /resuming/);
    const restored = await readFile(join(folder, 'vectors.bin'));
    assert.equal(createHash('sha256').update(restored).digest('hex'), metadata.vectorsSha256);
    await writeFile(join(folder, '.embed-checkpoint.json'), JSON.stringify({ key: 'different-source', completed: 1, spanCounts: [1] }));
    await writeFile(join(folder, '.embed-vectors.partial'), vectors.subarray(0, 512 * 4));
    result = execute(); assert.equal(result.status, 0, result.stderr); assert.doesNotMatch(result.stdout, /resuming/);
  } finally {
    const target = resolve(folder);
    if (dirname(target) !== resolve(tmpdir()) || !basename(target).startsWith('report-qa-embed-')) throw new Error('Refusing cleanup outside the generated test directory.');
    await rm(target, { recursive: true, force: true });
  }
});

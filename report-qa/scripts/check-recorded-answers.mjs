// Verify that the frozen evaluation answers still match the real UI assembler.
// No search, gold answers, or labels are passed into answer generation.
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import assert from 'node:assert/strict';
import { buildAnswer } from '../site/answer.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const data = JSON.parse(await readFile(resolve(root, 'site/data/evaluation.json'), 'utf8'));
let checked = 0;
for (const question of data.questions) for (const mode of ['bm25', 'dense', 'hybrid']) {
  const result = question.results[mode];
  const companies = result.answer.groups.map(({ company, code }) => ({ company, code }));
  const rebuilt = buildAnswer(result, question.question, { companies, crossCompany: Boolean(question.crossCompany) });
  for (const key of ['groups', 'evidence', 'paragraphs', 'citations']) {
    assert.deepEqual(rebuilt[key], result.answer[key], `${question.id}/${mode}/${key}: frozen answer differs from UI`);
  }
  checked++;
}
assert.equal(checked, 30);
console.log(JSON.stringify({ checkedAnswerSnapshots: checked, unchanged: true, retrievalRerun: false }));

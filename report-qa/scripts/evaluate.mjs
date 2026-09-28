// Execute all three retrieval methods against the same frozen ten questions.
import { readFile, writeFile } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { buildIndex, attachVectors, search } from '../lib/search.mjs';
import { createEncoder, encodeQuery } from '../lib/embedding.mjs';
import { buildAnswer } from '../site/answer.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const readJSON = async name => JSON.parse((await readFile(join(root, name), 'utf8')).replace(/^\uFEFF/, ''));
const sha = value => createHash('sha256').update(value).digest('hex');
const chunksBytes = await readFile(join(root, 'site/data/chunks.json'));
const chunks = JSON.parse(chunksBytes);
const meta = await readJSON('site/data/index-meta.json');
const vectorBytes = await readFile(join(root, 'site/data/vectors.bin'));
if (sha(chunksBytes) !== meta.chunksSha256 || sha(vectorBytes) !== meta.vectorsSha256) throw new Error('Index provenance mismatch');
const index = buildIndex(chunks);
attachVectors(index, new Float32Array(vectorBytes.buffer.slice(vectorBytes.byteOffset, vectorBytes.byteOffset + vectorBytes.byteLength)), meta.dimension);
const frozen = await readJSON('questions.json');
const encoder = await createEncoder({ modelPath: join(root, 'site/models') + '/' });
const normalize = value => String(value).normalize('NFKC').replace(/[\s,，]/g, '').toLowerCase();
function contains(text, value) {
  text = normalize(text); value = normalize(value);
  if (/^[\d.]+$/.test(value)) return new RegExp('(?<![\\d.])' + value.replaceAll('.', '\\.') + '(?![\\d.])').test(text);
  return text.includes(value);
}
function measure(question, result, answer) {
  const details = question.goldEvidence.map(gold => {
    const canonical = result.hits.filter(hit => hit.code === gold.code && hit.pdfPage === gold.pdfPage);
    const candidateText = canonical.map(hit => hit.text).join('\n');
    const displayed = answer.groups.find(group => group.code === gold.code)?.hits || [];
    const displayedText = displayed.map(hit => [hit.excerpt, ...(hit.contextLines || []), hit.table?.unitContext || '', JSON.stringify(hit.table?.headers || []), JSON.stringify(hit.table?.rows || [])].join('\n')).join('\n');
    const values = gold.values.map(value => ({ value, recalled: contains(candidateText, value), displayed: contains(displayedText, value) }));
    const relevant = result.hits.map((hit, i) => ({ hit, rank: i + 1 })).filter(({ hit }) => hit.code === gold.code && hit.pdfPage === gold.pdfPage && gold.values.some(value => contains(hit.text, value)));
    const scopeRanks = result.groups?.find(group => group.code === gold.code)?.hits || result.hits;
    const firstRank = scopeRanks.findIndex(hit => relevant.some(item => item.hit.id === hit.id)) + 1;
    return { ...gold, values, canonicalEvidenceRecalled: values.every(item => item.recalled), displayedFactsComplete: values.every(item => item.displayed), firstRelevantRank: firstRank || null, retrievedGoldChunkIds: relevant.map(item => item.hit.id) };
  });
  return {
    evidenceRecall: details.filter(item => item.canonicalEvidenceRecalled).length / details.length,
    meanReciprocalRank: details.reduce((sum, item) => sum + (item.firstRelevantRank ? 1 / item.firstRelevantRank : 0), 0) / details.length,
    displayedFactCoverage: details.filter(item => item.displayedFactsComplete).length / details.length,
    returnedCompanies: new Set(result.hits.map(hit => hit.code)).size,
    expectedCompanies: new Set(question.goldEvidence.map(item => item.code)).size,
    cutoff: question.crossCompany ? 'top-2 per company (24 blocks)' : 'top-10',
    goldDetails: details,
  };
}
const questions = [];
try {
  for (const question of frozen.questions) {
    const queryVector = await encodeQuery(encoder, question.question);
    const results = {};
    for (const mode of ['bm25', 'dense', 'hybrid']) {
      const start = performance.now();
      const retrieved = search(index, question.question, mode === 'bm25' ? null : queryVector, {
        mode, topK: 10, company: question.company, crossCompany: Boolean(question.crossCompany), perCompany: 2,
      });
      const result = { ...retrieved, query: question.question, mode, timings: { searchMs: performance.now() - start } };
      const answer = buildAnswer(result, question.question, { companies: index.companies, crossCompany: Boolean(question.crossCompany) });
      results[mode] = { ...result, answer, metrics: measure(question, result, answer) };
    }
    questions.push({ ...question, results, verdict: '待人工核验', errorAnalysis: '数字覆盖只是辅助检查；需另行核对年份、单位、主体口径和解释完整性。' });
    console.log(JSON.stringify({ question: question.id, facts: Object.fromEntries(Object.entries(results).map(([mode, value]) => [mode, value.metrics.displayedFactCoverage])), evidence: Object.fromEntries(Object.entries(results).map(([mode, value]) => [mode, value.metrics.evidenceRecall])) }));
  }
} finally { await encoder.dispose(); }
const summary = { note: '十题在检索前固定。普通题Top-10；两道全景题分别对12家银行取Top-2。证据召回采用人工指定的规范原页，重复披露在其他页可能答对但不计规范证据命中。数字覆盖不等同于回答正确率。', methods: {} };
for (const mode of ['bm25', 'dense', 'hybrid']) summary.methods[mode] = {
  evidenceRecall: questions.reduce((sum, q) => sum + q.results[mode].metrics.evidenceRecall, 0) / questions.length,
  meanReciprocalRank: questions.reduce((sum, q) => sum + q.results[mode].metrics.meanReciprocalRank, 0) / questions.length,
  displayedFactCoverage: questions.reduce((sum, q) => sum + q.results[mode].metrics.displayedFactCoverage, 0) / questions.length,
};
const evaluation = { schemaVersion: 1, generatedAt: new Date().toISOString(), provenance: { chunksSha256: meta.chunksSha256, vectorsSha256: meta.vectorsSha256, model: meta.model, modelRevision: meta.modelRevision, questionsSha256: sha(await readFile(join(root, 'questions.json'))), answerMode: 'deterministic extractive; no generative language model' }, summary, questions };
await writeFile(join(root, 'site/data/evaluation.json'), JSON.stringify(evaluation, null, 2) + '\n');
console.log(JSON.stringify(summary));

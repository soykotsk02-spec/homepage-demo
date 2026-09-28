import test from 'node:test';
import assert from 'node:assert/strict';
import { buildIndex, attachVectors, search, tokenize } from './search.mjs';

const chunks = [
  { id: 'a1', company: '甲银行', code: 'A', reportYear: 2025, text: '净利润增长，贷款余额上升。', pdfPage: 2 },
  { id: 'a2', company: '甲银行', code: 'A', reportYear: 2025, text: '数据中心运营及信息技术投入。', pdfPage: 3 },
  { id: 'b1', company: '乙银行', code: 'B', reportYear: 2025, text: '净利润下降，贷款余额增加。', pdfPage: 4 },
];
test('Chinese and numeric tokenization is deterministic', () => {
  assert.deepEqual(tokenize('净利润 ２０２５ 1.25%'), ['净利', '利润', '2025', '1.25%']);
});
test('BM25 ranks actual matching text and filters exact company', () => {
  const index = buildIndex(chunks);
  const result = search(index, '净利润', null, { mode: 'bm25', company: 'B' });
  assert.equal(result.hits.length, 1); assert.equal(result.hits[0].id, 'b1');
  assert.ok(result.hits[0].bm25Score > 0); assert.equal(result.hits[0].denseScore, null);
});
test('Dense ranking comes from stored vectors and RRF reconciles both ranks', () => {
  const index = buildIndex(chunks); attachVectors(index, new Float32Array([1, 0, 0, 1, -1, 0]), 2);
  const vector = new Float32Array([0, 1]);
  assert.equal(search(index, '净利润', vector, { mode: 'dense' }).hits[0].id, 'a2');
  const hybrid = search(index, '净利润', vector, { mode: 'hybrid' }).hits;
  assert.ok(hybrid.every(hit => hit.denseRank && hit.bm25Score !== undefined));
  const a1 = hybrid.find(hit => hit.id === 'a1');
  assert.equal(a1.score, 1 / (60 + a1.bm25Rank) + 1 / (60 + a1.denseRank));
});
test('Panorama retains evidence from each company', () => {
  const result = search(buildIndex(chunks), '净利润', null, { mode: 'bm25', crossCompany: true, perCompany: 1 });
  assert.equal(result.groups.length, 2); assert.deepEqual(result.coverage, { totalCompanies: 2, coveredCompanies: 2 });
});
test('No fake dense fallback and malformed vectors rejected', () => {
  const index = buildIndex(chunks);
  assert.throws(() => search(index, '利润'), /DENSE_NOT_READY/);
  assert.throws(() => attachVectors(index, new Float32Array([1, 2]), 2), /不一致/);
  assert.throws(() => buildIndex([chunks[0], chunks[0]]), /重复/);
});
test('Company in a question limits results, with manual scope taking priority', () => {
  const index = buildIndex(chunks);
  assert.ok(search(index, '甲银行净利润', null, { mode: 'bm25' }).hits.every(hit => hit.company === '甲银行'));
  assert.ok(search(index, '甲银行净利润', null, { mode: 'bm25', company: 'B' }).hits.every(hit => hit.company === '乙银行'));
  assert.equal(search(index, '甲银行净利润', null, { mode: 'bm25', crossCompany: true }).groups.length, 2);
});

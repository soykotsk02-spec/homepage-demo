import test from 'node:test';
import assert from 'node:assert/strict';
import { splitForEmbedding } from './embedding.mjs';

const count = text => Array.from(text).length + 2;
test('Long text is completely retained and every encoded span fits the limit', () => {
  const text = '甲银行：收入稳步增长。\n' + '这是没有断句的一行表格数据'.repeat(50) + '！结论。';
  const spans = splitForEmbedding(text, count, 30);
  assert.ok(spans.length > 2);
  assert.equal(spans.join(''), text);
  assert.ok(spans.every(span => count(span) <= 30));
});
test('Short documents remain identical and no blank embedding is made', () => {
  assert.deepEqual(splitForEmbedding('完整段落。', count), ['完整段落。']);
  assert.throws(() => splitForEmbedding(' ', count), /空正文/);
});

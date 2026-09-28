import { createEncoder, encodeDocument, encodeQuery } from './embedding.mjs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const started = performance.now();
const encoder = await createEncoder({ modelPath: join(root, 'site/models') + '/' });
const loaded = performance.now();
const text = ('本行积极推进数字化转型，持续加强数据治理和科技基础设施建设。银行围绕实体经济发展需求，加大制造业和科技创新企业的贷款支持力度。报告期内营业收入稳步增长，客户存款和贷款规模持续增加，不良贷款率保持稳定。').repeat(4);
const times = [];
for (let i = 0; i < 5; i++) {
  const before = performance.now();
  const result = await encodeDocument(encoder, '示例银行 2025年 经营情况讨论与分析\n' + text);
  times.push({ milliseconds: performance.now() - before, spans: result.spanCount, tokens: result.tokenCount, dimension: result.vector.length });
}
const queryStart = performance.now();
await encodeQuery(encoder, '银行怎样利用科技改造业务流程？');
console.log(JSON.stringify({ loadMs: loaded - started, characters: text.length, trials: times, queryMs: performance.now() - queryStart }, null, 2));
await encoder.dispose();

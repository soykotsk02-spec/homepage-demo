export const DEFAULTS = Object.freeze({ k1: 1.2, b: 0.75, rrfK: 60, candidateK: 50 });

// Identical tokenization in Node and browser, independent of installed dictionaries.
export function tokenize(text) {
  const normalized = String(text ?? '').normalize('NFKC').toLowerCase();
  const tokens = [];
  for (const match of normalized.matchAll(/[\p{Script=Han}]+|[a-z]+(?:[.-][a-z]+)*|\d+(?:[.,]\d+)*(?:%|％)?/gu)) {
    const word = match[0];
    if (/\p{Script=Han}/u.test(word)) {
      const chars = Array.from(word);
      if (chars.length === 1) tokens.push(word);
      else for (let i = 0; i < chars.length - 1; i++) tokens.push(chars[i] + chars[i + 1]);
    } else tokens.push(word);
  }
  return tokens;
}

export function embeddingText(chunk) {
  return `${chunk.company ?? ''} ${chunk.reportYear ?? ''}年 ${chunk.section ?? ''}\n${chunk.text ?? ''}`.trim();
}

export function buildIndex(chunks, options = {}) {
  if (!Array.isArray(chunks) || !chunks.length) throw new Error('索引没有文本块。');
  const ids = new Set();
  const postings = new Map();
  const lengths = new Uint32Array(chunks.length);
  const companies = new Map();
  chunks.forEach((chunk, index) => {
    if (!chunk?.id || ids.has(chunk.id) || typeof chunk.text !== 'string') throw new Error('文本块 id 重复或缺少正文。');
    ids.add(chunk.id);
    const tokens = tokenize(embeddingText(chunk));
    lengths[index] = tokens.length;
    const counts = new Map();
    for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
    for (const [token, count] of counts) {
      if (!postings.has(token)) postings.set(token, []);
      postings.get(token).push([index, count]);
    }
    const key = `${chunk.company}\u0000${chunk.code ?? ''}`;
    if (!companies.has(key)) companies.set(key, { company: chunk.company, code: chunk.code ?? '', count: 0 });
    companies.get(key).count++;
  });
  const avgLength = lengths.reduce((sum, n) => sum + n, 0) / chunks.length || 1;
  return { chunks, postings, lengths, avgLength, companies: [...companies.values()], options: { ...DEFAULTS, ...options }, vectors: null, dimension: 0 };
}

export function attachVectors(index, vectors, dimension) {
  if (!(vectors instanceof Float32Array) || !Number.isInteger(dimension) || dimension < 1 || vectors.length !== index.chunks.length * dimension) throw new Error('向量数量或维度与文本块不一致。');
  for (let row = 0; row < index.chunks.length; row++) {
    let norm = 0;
    for (let col = 0; col < dimension; col++) { const value = vectors[row * dimension + col]; if (!Number.isFinite(value)) throw new Error('向量含非有限数值。'); norm += value * value; }
    if (Math.abs(Math.sqrt(norm) - 1) > 0.025) throw new Error('向量未按约定归一化。');
  }
  index.vectors = vectors;
  index.dimension = dimension;
}

function eligible(chunk, options) {
  const selected = options.companies ?? (options.company ? [options.company] : null);
  return !selected?.length || selected.some(value => String(value) === String(chunk.company) || String(value) === String(chunk.code));
}

const BANK_ALIASES = [
  ['工商银行', '工行'], ['农业银行', '农行'], ['中国银行', '中行'], ['建设银行', '建行'],
  ['交通银行', '交行'], ['邮政储蓄银行', '邮储银行', '邮储'], ['招商银行', '招行'],
  ['兴业银行'], ['浦东发展银行', '浦发银行', '浦发'], ['民生银行'], ['中信银行'],
  ['平安银行'], ['华夏银行'], ['光大银行'],
];
export function resolveScope(index, query, options = {}) {
  if (options.company || options.companies?.length) return { options, notes: ['已优先使用页面选定的公司范围。'] };
  if (options.crossCompany) return { options, notes: ['全景模式未指定公司，已覆盖库中全部公司。'] };
  const identified = index.companies.filter(({ company, code }) => {
    const basic = company.replace(/股份有限公司|股份公司|有限公司/g, '');
    const names = new Set([company, basic]);
    for (const group of BANK_ALIASES) if (group.some(alias => basic.includes(alias))) for (const alias of group) names.add(alias);
    if ([...names].some(name => name.length >= 2 && query.includes(name))) return true;
    return String(code).length >= 6 && query.split(/[^0-9A-Za-z]+/).includes(String(code));
  });
  if (!identified.length) return { options, notes: [] };
  return { options: { ...options, companies: identified.map(c => c.company) }, notes: [`根据问题中明确出现的公司名称限定范围：${identified.map(c => c.company).join('、')}。`] };
}

function ranked(scores, indices, positiveOnly = false) {
  return indices.filter(i => !positiveOnly || scores[i] > 0).sort((a, b) => scores[b] - scores[a] || a - b);
}

function searchScope(index, query, queryVector, options) {
  const mode = options.mode ?? 'hybrid';
  if (!['hybrid', 'bm25', 'dense'].includes(mode)) throw new Error('未知检索模式。');
  const hasDense = queryVector && index.vectors;
  if (mode !== 'bm25' && !hasDense) throw new Error('DENSE_NOT_READY');
  if (hasDense && queryVector.length !== index.dimension) throw new Error('查询向量维度不一致。');
  const indices = index.chunks.map((_, i) => i).filter(i => eligible(index.chunks[i], options));
  const allowed = new Set(indices);
  const bm25 = new Float64Array(index.chunks.length);
  const dense = hasDense ? new Float64Array(index.chunks.length) : null;
  const { k1, b, rrfK, candidateK } = index.options;
  for (const token of new Set(tokenize(query))) {
    const posting = index.postings.get(token);
    if (!posting) continue;
    const idf = Math.log(1 + (index.chunks.length - posting.length + 0.5) / (posting.length + 0.5));
    for (const [i, tf] of posting) if (allowed.has(i)) {
      bm25[i] += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * index.lengths[i] / index.avgLength));
    }
  }
  if (hasDense) for (const i of indices) {
    let dot = 0;
    for (let j = 0; j < index.dimension; j++) dot += queryVector[j] * index.vectors[i * index.dimension + j];
    dense[i] = dot;
  }
  const bm25Order = ranked(bm25, indices, true);
  const denseOrder = hasDense ? ranked(dense, indices) : [];
  const bm25Ranks = new Map(bm25Order.map((i, rank) => [i, rank + 1]));
  const denseRanks = new Map(denseOrder.map((i, rank) => [i, rank + 1]));
  const candidates = new Set([...bm25Order.slice(0, candidateK), ...denseOrder.slice(0, candidateK)]);
  const fused = new Float64Array(index.chunks.length);
  for (const i of candidates) {
    const rb = bm25Ranks.get(i); const rd = denseRanks.get(i);
    fused[i] = (rb && rb <= candidateK ? 1 / (rrfK + rb) : 0) + (rd && rd <= candidateK ? 1 / (rrfK + rd) : 0);
  }
  const hybridOrder = ranked(fused, [...candidates]);
  const hybridRanks = new Map(hybridOrder.map((i, rank) => [i, rank + 1]));
  const order = mode === 'bm25' ? bm25Order : mode === 'dense' ? denseOrder : hybridOrder;
  const limit = Math.max(1, Math.min(100, Number(options.topK) || 10));
  return order.slice(0, limit).map(i => ({
    ...index.chunks[i], score: mode === 'bm25' ? bm25[i] : mode === 'dense' ? dense[i] : fused[i],
    denseScore: dense ? dense[i] : null, bm25Score: bm25[i],
    denseRank: denseRanks.get(i) ?? null, bm25Rank: bm25Ranks.get(i) ?? null,
    hybridRank: hasDense ? hybridRanks.get(i) ?? null : null,
  }));
}

export function search(index, query, queryVector = null, options = {}) {
  if (typeof query !== 'string' || !query.trim() || query.length > 3000) throw new Error('问题须为 1–3000 个字符。');
  const scope = resolveScope(index, query, options);
  options = scope.options;
  if (!options.crossCompany) return { hits: searchScope(index, query, queryVector, options), notes: scope.notes };
  const chosen = index.companies.filter(company => eligible(company, options));
  const groups = chosen.map(company => ({ ...company, hits: searchScope(index, query, queryVector, { ...options, companies: [company.company], topK: options.perCompany ?? 2 }) }));
  return {
    hits: groups.flatMap(group => group.hits), groups,
    coverage: { totalCompanies: chosen.length, coveredCompanies: groups.filter(group => group.hits.length).length },
    notes: [...scope.notes, '已逐家公司检索；覆盖数仅表示有检索候选，不证明每家公司均有充分答案证据。'],
  };
}

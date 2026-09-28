/** Deterministic extractive answers. No generated facts and no model requests. */
export function citationHref(sourceUrl, page) {
  try {
    const url = new URL(sourceUrl);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
    if (Number.isInteger(Number(page)) && Number(page) > 0) url.hash = `page=${Number(page)}`;
    return url.href;
  } catch { return null; }
}

export function queryTerms(query) {
  const input = String(query || '').toLowerCase();
  const terms = new Set(input.match(/[a-z][a-z0-9.%-]*|\d+(?:\.\d+)?%?/g) || []);
  for (const part of input.match(/[\u3400-\u9fff]+/g) || []) {
    if (part.length <= 4) terms.add(part);
    for (let index = 0; index < part.length - 1; index++) terms.add(part.slice(index, index + 2));
  }
  for (const word of ['如何', '哪些', '什么', '披露', '年报', '银行', '各家', '情况', '以及', '是否', '进行', '变化']) terms.delete(word);
  return [...terms].filter(term => term.length > 1);
}

/** Choose original, contiguous sentences; ellipses explicitly mark omitted text. */
export function selectExcerpt(text, query, maxLength = 440) {
  const original = String(text || '').trim();
  if (!original) return { text: '', truncated: false };
  if (original.length <= maxLength) return { text: original, truncated: false };
  const terms = queryTerms(query);
  const parts = [...original.matchAll(/[^。！？\n]+[。！？\n]*/g)];
  if (!parts.length) return { text: `${original.slice(0, maxLength)}…`, truncated: true };
  const ranked = parts.map((part, index) => {
    const lower = part[0].toLowerCase();
    const overlap = terms.reduce((total, term) => total + (lower.includes(term) ? Math.min(term.length, 8) : 0), 0);
    // Numeric disclosures outrank equally relevant headings, without making
    // unrelated numbers outrank a sentence that actually matches the query.
    const numericBonus = overlap && /\d[\d,.]*\s*(?:%|％|亿元|万元|元|个|家|万|亿|个百分点)/.test(lower) ? 1.5 : 0;
    const score = overlap + numericBonus;
    return { part, index, score };
  }).sort((left, right) => right.score - left.score || left.index - right.index);
  const best = ranked[0];
  let start = best.part.index;
  let end = start + best.part[0].length;
  if (end - start > maxLength) {
    const lower = best.part[0].toLowerCase();
    const positions = terms.map(term => lower.indexOf(term)).filter(index => index >= 0);
    const offset = positions.length ? Math.max(0, Math.min(...positions) - 60) : 0;
    start += Math.min(offset, best.part[0].length - maxLength);
    end = Math.min(original.length, start + maxLength);
  } else {
    let next = best.index + 1;
    while (next < parts.length && parts[next].index + parts[next][0].length - start <= maxLength) {
      end = parts[next].index + parts[next][0].length;
      next++;
    }
  }
  const excerpt = original.slice(start, end).trim();
  return { text: `${start > 0 ? '…' : ''}${excerpt}${end < original.length ? '…' : ''}`, truncated: start > 0 || end < original.length };
}

function normalizeEvidence(hit, query, index) {
  const excerpt = selectExcerpt(hit.text, query);
  // A text chunk can cross section boundaries. A unit near its end may belong
  // to the next table, so never promote a body-text unit to chunk-wide context.
  // Only table metadata and actual header cells define table-scoped context.
  const table = hit.type === 'table' && hit.table && typeof hit.table === 'object' ? hit.table : null;
  const candidates = table ? [
    typeof table.unitContext === 'string' ? table.unitContext : '',
    ...(Array.isArray(table.headers) ? table.headers.filter(header => typeof header === 'string' &&
      /单位(?:说明)?\s*[:：]|(?:人民币|金额单位).{0,16}(?:元|%)|(?:百万元|千元).{0,12}(?:人民币|单位)/.test(header)) : []),
  ] : [];
  const contextLines = [...new Set(candidates.map(line => line.trim()).filter(Boolean))];
  return { ...hit, citation: index + 1, excerpt: excerpt.text, excerptTruncated: excerpt.truncated,
    contextLines, citationUrl: citationHref(hit.sourceUrl, hit.pdfPage) };
}

export function buildAnswer(result, query, { companies = [], crossCompany = false } = {}) {
  const hits = Array.isArray(result?.hits) ? result.hits : [];
  const seen = new Set();
  const clean = hits.filter(hit => {
    if (!hit || typeof hit.text !== 'string' || !hit.text.trim() || !hit.id || seen.has(String(hit.id))) return false;
    seen.add(String(hit.id));
    return true;
  });
  const evidence = clean.map((hit, index) => normalizeEvidence(hit, query, index));
  const grouped = new Map();
  if (crossCompany) for (const company of companies) grouped.set(company.company, { company: company.company, code: company.code, hits: [] });
  for (const hit of evidence) {
    const name = String(hit.company || '未标注公司');
    if (!grouped.has(name)) grouped.set(name, { company: name, code: hit.code || '', hits: [] });
    grouped.get(name).hits.push(hit);
  }
  // Groups may explicitly declare a company with zero hits.
  if (crossCompany && Array.isArray(result?.groups)) for (const group of result.groups) {
    if (!grouped.has(group.company)) grouped.set(group.company, { company: group.company, code: group.code || '', hits: [] });
  }
  const groups = [...grouped.values()];
  return { query: String(query), mode: result?.mode || 'bm25', groups, evidence,
    paragraphs: groups.map(group => ({ company: group.company, code: group.code,
      found: group.hits.length > 0, excerpts: group.hits.map(hit => ({ text: hit.excerpt, contextLines: hit.contextLines, citation: hit.citation, table: hit.table || null })) })),
    citations: evidence.map(hit => ({ citation: hit.citation, id: hit.id, company: hit.company,
      section: hit.section, pdfPage: hit.pdfPage, sourceUrl: hit.sourceUrl, url: hit.citationUrl })),
    totalCompanies: crossCompany ? grouped.size : companies.length,
    coveredCompanies: [...grouped.values()].filter(group => group.hits.length).length,
    crossCompany, notes: result?.notes, timings: result?.timings };
}

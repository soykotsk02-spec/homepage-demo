// A separate, source-checked audit of the frozen answers, never an answer input.
const labels = { complete: '完整', partial: '部分', missing: '缺失' };
const modes = { bm25: 'BM25 关键词', dense: 'BGE 语义向量', hybrid: 'RRF 混合检索' };
const order = ['bm25', 'dense', 'hybrid'];
function node(tag, text, className = '') {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  value.className = className;
  return value;
}
function table(headers, rows) {
  const shell = node('div', undefined, 'table-shell');
  const value = node('table', undefined, 'evidence-table audit-table');
  const head = node('thead'); const row = node('tr');
  headers.forEach(label => { const th = node('th', label); th.scope = 'col'; row.append(th); });
  head.append(row); value.append(head);
  const body = node('tbody');
  for (const values of rows) { const tr = node('tr'); values.forEach(v => tr.append(node('td', String(v)))); body.append(tr); }
  value.append(body); shell.append(value); return shell;
}
export function renderAuditSummary(audit, parent) {
  const panel = node('section', undefined, 'audit-summary panel');
  panel.append(node('h3', '召回证据与实际答案，分别检查'));
  panel.append(node('p', audit.reviewNote, 'result-notes'));
  panel.append(table(['方法', '规范页命中', '证据完整题数', '答案完整题数'], order.map(mode => {
    const item = audit.summary.methods[mode];
    return [modes[mode], `${(100 * item.canonicalPageHitRate).toFixed(1)}%`, `${item.retrieval.complete} / 10`, `${item.answer.complete} / 10`];
  })));
  panel.append(node('p', '全景题须 12 家全部完整才记为整题完整。规范页命中保留原始自动指标；复核会接受其他原页的有效重复披露。数字出现不等于单位、年份和主体均正确。', 'result-notes'));
  parent.append(panel);
}
function recordDetail(record, parent) {
  const block = node('div', undefined, 'audit-record');
  if (record.company) block.append(node('h5', record.company));
  block.append(node('p', `实际展示概述：${record.answer.actualAnswerSummary}`, 'eval-detail'));
  block.append(node('p', `召回证据：${labels[record.retrieval.status]}。${record.retrieval.reason}`, 'eval-detail'));
  block.append(node('p', `答案核验：${labels[record.answer.status]}。${record.answer.reason}`, 'eval-detail'));
  const ids = [...new Set([...(record.retrieval.supportingIds || []), ...(record.answer.supportingIds || [])])];
  block.append(node('p', `对应块：${ids.join('、') || '无目标证据'}`, 'chunk-id'));
  for (const source of record.sourceChecks || []) {
    const p = node('p', undefined, 'eval-detail');
    const a = node('a', `核对原页 ${source.pdfPage} ↗`, 'source-link');
    try {
      const url = new URL(source.sourceUrl);
      if (url.protocol !== 'https:' || url.hostname !== 'static.cninfo.com.cn') continue;
      url.hash = `page=${Number(source.pdfPage)}`;
      a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
      p.append(a, document.createTextNode(` · ${source.scope || ''} · ${source.referenceFacts || ''}`)); block.append(p);
    } catch { /* Invalid source URLs are not rendered as links. */ }
  }
  parent.append(block);
}
export function renderQuestionAudit(review, parent) {
  const wrap = node('div', undefined, 'question-audit');
  wrap.append(node('p', review.scope || '分别核对召回原文与实际展示摘录、单位行和表格。', 'eval-detail'));
  for (const mode of order) {
    const item = review.methods[mode]; const detail = node('details', undefined, 'eval-evidence audit-method');
    detail.append(node('summary', `${modes[mode]} · 证据${labels[item.retrieval.status]} / 答案${labels[item.answer.status]}`));
    if (item.companies) {
      detail.append(table(['公司', '召回证据', '实际答案'], item.companies.map(company => [company.company, labels[company.retrieval.status], labels[company.answer.status]])));
      item.companies.forEach(company => recordDetail(company, detail));
    } else recordDetail(item, detail);
    wrap.append(detail);
  }
  parent.append(wrap);
}

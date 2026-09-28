import { buildAnswer, citationHref } from './answer.mjs';

const $ = selector => document.querySelector(selector);
const modes = { hybrid: '混合检索', bm25: 'BM25 关键词', dense: '语义向量' };
const state = { worker: null, capabilities: { bm25: false, dense: false }, companies: [], manifest: null,
  evaluation: null, activeRequest: null, busy: false, timer: null, started: 0, selected: new Set(), view: 'research',
  initialMode: 'hybrid', modeChosenByUser: false, hasSearched: false, automaticModeFallback: false };
const number = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN') : '—';
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined && text !== null) node.textContent = String(text); return node; }
function append(parent, ...children) { for (const child of children) if (child) parent.append(child); return parent; }
function requestId() { return globalThis.crypto?.randomUUID?.() || `request-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function selectedMode() { return $('input[name="mode"]:checked')?.value || 'bm25'; }
function selectedCompanies() { return state.companies.filter(company => state.selected.has(company.company)); }
function progressDetail(value) { if (typeof value === 'number') return `${Math.max(0, Math.min(100, Math.round(value <= 1 ? value * 100 : value)))}%`; return ''; }

function setRuntime(title, detail, kind = 'ready', badge = '') {
  $('#runtime-title').textContent = title;
  $('#runtime-detail').textContent = detail;
  $('#runtime-dot').className = `status-dot${kind === 'loading' ? ' loading' : kind === 'error' ? ' error' : ''}`;
  $('#runtime-badge').textContent = badge || (kind === 'loading' ? '载入中' : kind === 'error' ? '待检查' : '已就绪');
}
function updateModes() {
  for (const radio of document.querySelectorAll('input[name="mode"]')) radio.disabled = radio.value !== 'bm25' && !state.capabilities.dense;
  $('#submit-query').disabled = !state.capabilities.bm25 || state.busy;
  const mode = selectedMode();
  $('#mode-note').textContent = mode === 'bm25' ? '匹配关键词与年报中的专有表述' : mode === 'dense' ? '匹配语义相近的原文段落' : '关键词与语义排序共同参与';
}
function switchView(view) {
  state.view = view;
  $('#research-view').hidden = view !== 'research';
  $('#evaluation-view').hidden = view !== 'evaluation';
  for (const item of document.querySelectorAll('[data-view]')) {
    const active = item.dataset.view === view;
    item.classList.toggle('active', active);
    if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  }
}
function normalizeCompanies(reports) {
  const seen = new Set();
  return reports.filter(report => report && (report.company || report.name)).map(report => ({ ...report, company: report.company || report.name, code: String(report.code || '') }))
    .filter(report => { if (seen.has(report.company)) return false; seen.add(report.company); return true; });
}
function setCompanies(reports) {
  const hadCompanies = state.companies.length > 0;
  const allWereSelected = state.selected.size === state.companies.length;
  state.companies = normalizeCompanies(reports);
  if (!hadCompanies || allWereSelected) state.selected = new Set(state.companies.map(company => company.company));
  else state.selected = new Set([...state.selected].filter(company => state.companies.some(item => item.company === company)));
  renderCompanies();
}
function renderCompanies() {
  const container = $('#company-options'); container.replaceChildren();
  for (const company of state.companies) {
    const label = element('label', 'company-option');
    const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = state.selected.has(company.company); checkbox.value = company.company;
    checkbox.addEventListener('change', () => { if (checkbox.checked) state.selected.add(company.company); else state.selected.delete(company.company); updateScope(); });
    append(label, checkbox, element('span', '', company.company)); container.append(label);
  }
  if (!state.companies.length) container.append(element('span', 'muted', '暂无可用公司'));
  updateScope();
}
function updateScope() {
  const count = state.selected.size;
  $('#scope-summary').textContent = count === state.companies.length && count ? `全部 ${count} 家银行` : count ? `已选 ${count} 家银行` : '尚未选择';
}
function renderReports(manifest) {
  const reports = Array.isArray(manifest?.reports) ? manifest.reports : [];
  if (reports.length) setCompanies(reports);
  const pages = manifest?.totalPages ?? manifest?.stats?.totalPages ?? reports.reduce((sum, report) => sum + Number(report.pageCount || report.pages || 0), 0);
  const chunks = manifest?.totalChunks ?? manifest?.stats?.totalChunks ?? reports.reduce((sum, report) => sum + Number(report.chunkCount || report.chunks || 0), 0);
  $('#stat-reports').textContent = reports.length ? number(reports.length) : '—';
  $('#stat-pages').textContent = pages ? number(pages) : '—';
  if (chunks) $('#stat-chunks').textContent = number(chunks);
  const container = $('#report-list'); container.replaceChildren();
  for (const report of reports) {
    const href = citationHref(report.sourceUrl || report.url, 1);
    const link = element(href ? 'a' : 'div', 'report-link');
    if (href) { link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.setAttribute('aria-label', `${report.company} ${report.reportYear || 2025} 年报 PDF`); }
    append(link, append(element('div'), element('strong', '', report.company || report.name), element('small', '', [report.code, report.pageCount || report.pages ? `${report.pageCount || report.pages} PAGES` : 'ANNUAL REPORT'].filter(Boolean).join(' · '))), element('span', '', href ? '↗' : '—'));
    container.append(link);
  }
  if (!reports.length) container.append(element('div', 'empty-mini', '资料清单尚未生成；统计以实际索引为准。'));
}
function isPanoramic(question) { return /各家|各行|各银行|逐家|全景|全行业|十二家|12\s*家|所有银行|不同银行|哪[些几]家|哪[些几]银行|对比.*银行|银行.*对比/.test(question); }
function useQuestion(question, options = {}) {
  switchView('research'); $('#query').value = question;
  $('#cross-company').checked = Boolean(options.crossCompany ?? isPanoramic(question));
  if (Array.isArray(options.companies) && options.companies.length) {
    const requested = new Set(options.companies);
    state.selected = new Set(state.companies.filter(company => requested.has(company.company) || requested.has(company.code)).map(company => company.company));
    renderCompanies();
  } else if (options.crossCompany) {
    state.selected = new Set(state.companies.map(company => company.company));
    renderCompanies();
  }
  $('#query').focus();
  $('#query-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (options.run && state.capabilities.bm25 && !state.busy) runSearch();
}

function unitText(value) { return typeof value === 'string' ? value.trim().replace(/^(?:单位说明|原文口径|单位)\s*[:：]\s*/, '') : ''; }
function renderTable(table, parent) {
  if (!table || !Array.isArray(table.rows) || !table.rows.length) return;
  const headers = Array.isArray(table.headers) ? table.headers : [];
  const rows = table.rows.filter(row => Array.isArray(row) || (row && typeof row === 'object'));
  if (!rows.length) return;
  const columns = headers.length || (Array.isArray(rows[0]) ? rows[0].length : Object.keys(rows[0]).length);
  const names = headers.length ? headers : Array.isArray(rows[0]) ? Array.from({ length: columns }, (_, index) => `列 ${index + 1}`) : Object.keys(rows[0]);
  const units = unitText(table.unitContext);
  if (units) parent.append(element('p', 'table-caption table-unit', `原表单位：${units}`));
  const shell = element('div', 'table-shell'); shell.tabIndex = 0; shell.setAttribute('role', 'region'); shell.setAttribute('aria-label', '年报表格，可横向滚动');
  const view = element('table', 'evidence-table');
  const head = element('thead'); const tr = element('tr');
  for (const name of names) { const cell = element('th', '', Array.isArray(name) ? name.join(' / ') : name); cell.scope = 'col'; tr.append(cell); }
  head.append(tr); view.append(head);
  const body = element('tbody');
  for (const row of rows) { const line = element('tr'); const values = Array.isArray(row) ? row : names.map(key => row[key]); for (let index = 0; index < names.length; index++) line.append(element('td', '', values[index] ?? '—')); body.append(line); }
  view.append(body); shell.append(view); parent.append(shell);
  const range = Number.isInteger(Number(table.rowStart)) ? ` · 原表起始行 ${Number(table.rowStart)}` : '';
  parent.append(element('p', 'table-caption', `保留提取到的行列结构${range}。表头、单位与跨页关系请以原 PDF 为准。${headers.length ? '' : ' 未提取到表头，列序号仅用于阅读。'}`));
}
function scoreText(value) { return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : '未参与'; }
function renderEvidence(hit, mode) {
  const block = element('article', 'evidence-item');
  const top = element('div', 'evidence-topline');
  append(top, element('span', 'citation-number', `[${hit.citation}]`), element('span', 'evidence-section', hit.section || '未识别章节'));
  if (hit.citationUrl) { const source = element('a', 'source-link', `PDF 第 ${hit.pdfPage || '?'} 页 ↗`); source.href = hit.citationUrl; source.target = '_blank'; source.rel = 'noopener noreferrer'; top.append(source); }
  else top.append(element('span', 'hit-count', '来源链接不可用'));
  block.append(top);
  const tableUnits = unitText(hit.table?.unitContext);
  const contextLines = (hit.contextLines || []).filter(line => !tableUnits || !tableUnits.includes(unitText(line)));
  if (contextLines.length) block.append(element('p', 'table-caption', `原文口径：${contextLines.join(' / ')}`));
  block.append(element('blockquote', 'evidence-quote', hit.excerpt));
  if (hit.excerptTruncated) block.append(element('p', 'excerpt-note', '原文连续摘录；省略号表示前后内容已截短，可展开完整检索块。'));
  if (hit.type === 'table' || hit.table) renderTable(hit.table, block);
  const detail = element('details', 'retrieval-detail'); detail.append(element('summary', '', '查看检索依据与完整原文'));
  const content = element('div', 'retrieval-detail-content'); const scores = element('div', 'score-grid');
  for (const [label, value] of [
    [mode === 'hybrid' ? '融合得分' : '当前得分', scoreText(hit.score)],
    ['BM25 得分', scoreText(hit.bm25Score)], ['向量相似度', scoreText(hit.denseScore)],
    ['BM25 排名', hit.bm25Rank ? `#${hit.bm25Rank}` : '未参与'], ['向量排名', hit.denseRank ? `#${hit.denseRank}` : '未参与'],
  ]) scores.append(append(element('div', 'score-item'), element('span', '', label), element('strong', '', value)));
  append(content, scores, element('p', 'raw-chunk', hit.text), element('p', 'chunk-id', `块 ID：${hit.id} · ${hit.type === 'table' ? '表格' : '文本'} · ${hit.reportYear || 2025} 年报`));
  detail.append(content); block.append(detail); return block;
}
function renderAnswer(result, context) {
  const answer = buildAnswer(result, context.query, context);
  const container = $('#answer-content'); container.className = 'answer-results'; container.replaceChildren();
  const header = element('div', 'answer-header'); header.append(element('h3', 'answer-title', context.query));
  const meta = element('div', 'answer-meta');
  append(meta, element('span', 'meta-chip gold', '实时摘录式回答'), element('span', 'meta-chip', modes[answer.mode] || answer.mode), element('span', 'meta-chip', `${answer.evidence.length} 个证据块`), element('span', 'meta-chip', `${((performance.now() - state.started) / 1000).toFixed(2)} 秒`));
  append(header, meta, element('p', 'answer-disclosure', '以下内容直接摘自本次检索命中的年报原文，未调用生成式大模型。检索命中不等于事实已经确认；比较数据时，请核对年份、单位、集团或母行等口径。'));
  if (context.crossCompany) header.append(element('p', 'coverage-summary', `逐公司检索：${answer.totalCompanies} 家范围内，${answer.coveredCompanies} 家返回可展示的原文。未检索到证据不代表该公司没有披露。`));
  container.append(header);
  if (!answer.evidence.length && !answer.groups.length) {
    container.append(element('p', 'no-evidence', '本次没有找到可展示的相关原文。可以缩短问题、换用年报中的关键词，或扩大公司范围。不会以其他问题的结果代替本次答案。'));
    return;
  }
  const list = element('div', 'finding-list');
  answer.groups.forEach((group, index) => {
    const card = element('section', 'finding-card'); const head = element('div', 'finding-head');
    append(head, append(element('div', 'finding-company'), element('span', 'finding-index', String(index + 1).padStart(2, '0')), element('h3', '', group.company), element('span', 'company-code', group.code)), element('span', 'hit-count', `${group.hits.length} 条出处`));
    card.append(head);
    if (!group.hits.length) card.append(element('p', 'no-evidence', '本次检索未找到可展示的相关证据；不能据此判断该银行未披露此项内容。'));
    else { const body = element('div', 'finding-body'); for (const hit of group.hits) body.append(renderEvidence(hit, answer.mode)); card.append(body); }
    list.append(card);
  });
  container.append(list);
  const notes = Array.isArray(answer.notes) ? answer.notes.filter(note => typeof note === 'string').join('\n') : typeof answer.notes === 'string' ? answer.notes : '';
  if (notes) container.append(element('p', 'result-notes', notes));
  $('#answer-heading').textContent = '检索结果与原文证据';
}
function showSearchError(message, code = '') {
  const container = $('#answer-content'); container.className = 'error-card'; container.replaceChildren();
  append(container, element('h3', '', '这次检索尚未完成'), element('p', '', message || '检索环境未能返回结果，请查看上方状态后重试。'));
  if (code) container.append(element('p', 'chunk-id', `状态：${code}`));
  const retry = element('button', '', '重新检索'); retry.type = 'button'; retry.addEventListener('click', runSearch); container.append(retry);
}
function finishSearch() { clearInterval(state.timer); state.timer = null; state.busy = false; $('#search-progress').hidden = true; $('#submit-query').replaceChildren(document.createTextNode('检索并整理 '), element('span', '', '↗')); updateModes(); }
function runSearch(event) {
  event?.preventDefault();
  if (state.busy) return;
  const query = $('#query').value.trim();
  if (!query) { $('#query').focus(); return; }
  const companies = selectedCompanies();
  if (!companies.length) { showSearchError('请至少选择一家银行后再开始检索。', 'NO_COMPANY_SELECTED'); $('#scope-picker').open = true; return; }
  if (!state.capabilities.bm25 || !state.worker) { showSearchError('检索索引还未准备完成。请查看页面上方的载入状态。', 'INDEX_NOT_READY'); return; }
  const mode = selectedMode();
  if (mode !== 'bm25' && !state.capabilities.dense) { showSearchError('向量模型尚未就绪。可以先切换到 BM25 关键词检索。', 'DENSE_NOT_READY'); return; }
  state.hasSearched = true;
  state.busy = true; state.started = performance.now();
  state.activeRequest = { requestId: requestId(), query, companies, mode, crossCompany: $('#cross-company').checked && companies.length > 1 };
  $('#search-progress').hidden = false; $('#search-progress-title').textContent = state.activeRequest.crossCompany ? `正在逐家检索 ${companies.length} 家银行` : '正在检索相关原文';
  $('#search-progress-detail').textContent = `${modes[mode]} · 结果返回后，实时整理原文摘录与出处`; $('#search-elapsed').textContent = '0.0s';
  $('#submit-query').textContent = '检索中…'; updateModes();
  state.timer = setInterval(() => { $('#search-elapsed').textContent = `${((performance.now() - state.started) / 1000).toFixed(1)}s`; }, 100);
  $('#answer-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  const companyFilter = state.activeRequest.crossCompany || companies.length !== state.companies.length ? { companies: companies.map(company => company.company) } : {};
  try { state.worker.postMessage({ type: 'search', requestId: state.activeRequest.requestId, query, mode, ...companyFilter, topK: 10, crossCompany: state.activeRequest.crossCompany, perCompany: 2 }); }
  catch (error) { finishSearch(); showSearchError(error.message, 'WORKER_UNAVAILABLE'); }
}

function questionList(data) { return Array.isArray(data) ? data : Array.isArray(data?.questions) ? data.questions : []; }
const metricLabels = {
  evidenceRecall: '规范原页证据召回', meanReciprocalRank: '平均倒数排名（MRR）',
  displayedFactCoverage: '关键事实覆盖', returnedCompanies: '返回公司数',
  expectedCompanies: '目标公司数', cutoff: '截取范围',
};
function shortMetric(value, key = '') {
  if (key === 'goldDetails' || value === null || value === undefined) return null;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—';
    if (key === 'evidenceRecall' || key === 'displayedFactCoverage') return `${(value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`;
    if (key === 'meanReciprocalRank') return value.toFixed(3);
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  }
  if (typeof value === 'boolean') return value ? '是' : '否';
  return typeof value === 'string' ? value : null;
}
function renderMetrics(metrics, parent) {
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) return;
  const box = element('div', 'evaluation-metrics');
  for (const [key, value] of Object.entries(metrics)) {
    if (key === 'goldDetails') continue;
    const text = shortMetric(value, key);
    if (text !== null) box.append(append(element('span'), document.createTextNode(metricLabels[key] || modes[key] || key), element('strong', '', text)));
    else if (value && typeof value === 'object' && !Array.isArray(value)) for (const [metric, score] of Object.entries(value)) {
      const detail = shortMetric(score, metric); if (detail !== null) box.append(append(element('span'), document.createTextNode(`${modes[key] || key} · ${metricLabels[metric] || metric}`), element('strong', '', detail)));
    }
  }
  if (box.childElementCount) parent.append(box);
}
function renderRecordedEvaluation(results, parent) {
  if (!results || typeof results !== 'object') return;
  const available = ['hybrid', 'bm25', 'dense'].filter(mode => results[mode]);
  if (!available.length) return;
  const details = element('details', 'eval-evidence');
  details.append(element('summary', '', '查看已记录的评测结果与答案摘录'));
  details.append(element('p', '', '以下为评测脚本保存的运行记录；点击“重新检索”可在当前浏览器再次执行。'));
  for (const mode of available) {
    const result = results[mode];
    const section = element('section', 'recorded-result');
    section.append(element('h4', '', modes[mode]));
    renderMetrics(result.metrics, section);
    if (result.error) section.append(element('p', '', `本次评测未完成：${typeof result.error === 'string' ? result.error : result.error.message || '运行错误'}`));
    const groups = result.answer?.groups;
    if (Array.isArray(groups)) for (const group of groups) {
      section.append(element('p', 'recorded-company', group.company));
      if (!group.hits?.length) section.append(element('p', '', '未找到可展示的证据。'));
      for (const hit of group.hits || []) {
        const row = element('p', '', hit.excerpt || hit.text || '');
        const href = citationHref(hit.sourceUrl, hit.pdfPage);
        if (href) { const link = element('a', 'source-link', ` PDF 第 ${hit.pdfPage} 页 ↗`); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.append(document.createTextNode(' '), link); }
        section.append(row);
        if (hit.table) renderTable(hit.table, section);
      }
    }
    else if (typeof result.answer === 'string') section.append(element('p', '', result.answer));
    details.append(section);
  }
  parent.append(details);
}
function renderEvaluation(data) {
  const questions = questionList(data); const list = $('#evaluation-list'); list.replaceChildren();
  const summary = $('#evaluation-summary'); summary.replaceChildren();
  const panoramic = questions.filter(question => question.crossCompany || /panoram|cross|全景|跨公司/i.test(question.type || '')).length;
  const summaryCards = [
    ['评测问题', String(questions.length), '每题可重新检索，不使用预设答案'],
    ['跨公司全景', String(panoramic), '逐家公司召回，单独检查覆盖情况'],
    ['评测方式', '3', 'BM25 / 向量 / 混合检索'],
  ];
  for (const [label, value, detail] of summaryCards) summary.append(append(element('div', 'eval-stat'), element('span', '', label), element('strong', '', value), element('small', '', detail)));
  if (typeof data?.summary === 'string') { const note = element('p', 'result-notes', data.summary); list.append(note); }
  else if (data?.summary?.note) list.append(element('p', 'result-notes', data.summary.note));
  questions.forEach((item, index) => {
    const question = item.question || item.query || '';
    const crossCompany = Boolean(item.crossCompany || /panoram|cross|全景|跨公司/i.test(item.type || ''));
    const card = element('article', 'evaluation-card'); card.append(element('span', 'question-index', String(index + 1).padStart(2, '0')));
    const body = element('div'); body.append(element('h3', '', question));
    const typeNames = { single: '单公司事实', fact: '事实查找', factual: '事实查找', table: '表格查找', comparison: '比较分析', qualitative: '定性披露', footnote: '口径与脚注', reasoning: '原因解释', panoramic: '全景检索', 'cross-company': '全景检索' };
    const label = typeNames[item.type] || item.type || '检索问题';
    const companies = Array.isArray(item.companies) ? item.companies : item.company ? [item.company] : [];
    append(body, append(element('div', 'eval-labels'), element('span', 'eval-type', label), element('span', 'eval-companies', companies.length ? companies.join(' / ') : '全部银行')));
    if (item.description || item.purpose || item.note) body.append(element('p', 'eval-detail', item.description || item.purpose || item.note));
    if (Array.isArray(item.expectedTopics) && item.expectedTopics.length) body.append(element('p', 'eval-detail', `核验重点：${item.expectedTopics.join('、')}`));
    const methodMetrics = item.results ? Object.fromEntries(['hybrid', 'bm25', 'dense'].filter(mode => item.results[mode]?.metrics).map(mode => [mode, item.results[mode].metrics])) : null;
    renderMetrics(item.metrics || item.results?.metrics || methodMetrics, body);
    if (typeof item.verdict === 'string') body.append(element('p', 'eval-detail', `人工核查：${item.verdict}`));
    else if (item.verdict && typeof item.verdict === 'object') renderMetrics(item.verdict, body);
    const errorAnalysis = typeof item.errorAnalysis === 'string' ? item.errorAnalysis : Array.isArray(item.errorAnalysis) ? item.errorAnalysis.filter(value => typeof value === 'string').join('；') : '';
    if (errorAnalysis) body.append(element('p', 'eval-detail', `误差分析：${errorAnalysis}`));
    renderRecordedEvaluation(item.results, body);
    if (typeof item.resultNote === 'string') body.append(element('p', 'eval-detail', item.resultNote));
    const referenceEvidence = item.evidence || item.goldEvidence;
    if (Array.isArray(referenceEvidence) && referenceEvidence.length) {
      const details = element('details', 'eval-evidence'); details.append(element('summary', '', `人工核验参考出处 · ${referenceEvidence.length} 条`));
      for (const evidence of referenceEvidence) {
        const row = element('p', '', `${evidence.company || ''} · ${evidence.section || '原始年报'} · PDF 第 ${evidence.pdfPage || '?'} 页`);
        const href = citationHref(evidence.sourceUrl, evidence.pdfPage);
        if (href) { const link = element('a', 'source-link', ' 原文 ↗'); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.append(link); }
        details.append(row);
      }
      body.append(details);
    }
    card.append(body); const button = element('button', 'eval-run', '重新检索 ↗'); button.type = 'button';
    button.addEventListener('click', () => useQuestion(question, { crossCompany, companies, run: true })); card.append(button); list.append(card);
  });
  if (!questions.length) list.append(element('div', 'panel empty-mini', '评测文件尚未包含问题。不会展示虚构的评测结果。'));
}

async function loadJSON(path) { const response = await fetch(path, { cache: 'no-cache' }); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
async function loadMetadata() {
  const results = await Promise.allSettled([loadJSON('./data/manifest.json'), loadJSON('./data/evaluation.json')]);
  if (results[0].status === 'fulfilled') { state.manifest = results[0].value; renderReports(state.manifest); }
  else $('#report-list').replaceChildren(element('div', 'empty-mini', '资料清单暂时未载入。实际检索能力请看上方状态。'));
  if (results[1].status === 'fulfilled') { state.evaluation = results[1].value; renderEvaluation(state.evaluation); }
  else $('#evaluation-list').replaceChildren(element('div', 'panel empty-mini', '评测数据尚未就绪，暂不展示评测成绩。研究工作台仍可使用已就绪的检索方式。'));
}
function initWorker() {
  try {
    state.worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
    state.worker.addEventListener('message', event => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'ready') {
        state.capabilities = data.capabilities || { bm25: true, dense: !data.partial };
        let restoredDefault = false;
        if (!state.capabilities.dense && selectedMode() !== 'bm25') {
          state.automaticModeFallback = !state.modeChosenByUser && !state.hasSearched;
          $('input[name="mode"][value="bm25"]').checked = true;
        } else if (state.capabilities.dense && state.automaticModeFallback && !state.modeChosenByUser && !state.hasSearched) {
          $(`input[name="mode"][value="${state.initialMode}"]`).checked = true;
          state.automaticModeFallback = false;
          restoredDefault = true;
        }
        if (data.stats?.companies && !state.manifest?.reports?.length) setCompanies(data.stats.companies);
        if (data.stats?.chunkCount) $('#stat-chunks').textContent = number(data.stats.chunkCount);
        if (!state.manifest?.reports?.length && state.companies.length) $('#stat-reports').textContent = number(state.companies.length);
        const model = data.stats?.model;
        $('#index-note').textContent = [model ? `检索向量模型：${typeof model === 'string' ? model : model.id || model.name || '已配置'}` : '', data.stats?.dimension ? `向量维度：${data.stats.dimension}` : ''].filter(Boolean).join(' · ');
        const readyDetail = restoredDefault ? '向量模型载入完成，已恢复默认混合检索。检索在当前浏览器内运行。' : `当前使用${modes[selectedMode()]}；检索在当前浏览器内运行，可以打开原 PDF 核验。`;
        const loadingDetail = state.modeChosenByUser || state.hasSearched ? '当前使用 BM25；向量模型继续载入，完成后会保留你的当前选择。' : '暂时使用 BM25；向量模型载入后会恢复默认混合检索。主动选模式或开始检索后，将保留你的选择。';
        setRuntime(state.capabilities.dense ? '三种检索方式均已就绪' : '关键词检索已就绪', state.capabilities.dense ? readyDetail : loadingDetail, state.capabilities.dense ? 'ready' : 'loading', state.capabilities.dense ? '本地检索' : 'BM25 可用');
        updateModes();
      } else if (data.type === 'progress') {
        const detail = [data.message, progressDetail(data.progress)].filter(Boolean).join(' · ');
        if (data.requestId === state.activeRequest?.requestId && state.busy) $('#search-progress-detail').textContent = detail || '正在检索相关证据…';
        else if (data.stage === 'dense-unavailable') setRuntime('当前使用 BM25 关键词检索', data.message || '向量模型未能载入；混合与向量检索暂不可用。', 'ready', 'BM25 可用');
        else if (!state.capabilities.dense) setRuntime(state.capabilities.bm25 ? '关键词检索可用 · 向量模型载入中' : '正在准备检索环境', detail || '读取本地检索资源…', 'loading', state.capabilities.bm25 ? 'BM25 可用' : '初始化');
      } else if (data.type === 'results' && data.requestId === state.activeRequest?.requestId) {
        renderAnswer(data, state.activeRequest); finishSearch();
      } else if (data.type === 'error') {
        if (data.requestId === state.activeRequest?.requestId) { finishSearch(); showSearchError(data.message, data.code); }
        else if (!state.capabilities.bm25) { setRuntime('检索索引暂不可用', data.message || '请确认部署包含完整数据与检索资源。', 'error'); updateModes(); }
        else setRuntime('当前使用 BM25 关键词检索', data.message || '向量检索暂不可用。', 'ready', 'BM25 可用');
      }
    });
    state.worker.addEventListener('error', () => {
      state.capabilities = { bm25: false, dense: false }; finishSearch();
      setRuntime('检索程序未能启动', '请确认 worker.js、索引与本地模型文件均已部署；刷新页面可重试。', 'error');
      if (state.activeRequest) showSearchError('检索进程中断，没有返回本次结果。', 'WORKER_ERROR');
    });
    state.worker.postMessage({ type: 'init', requestId: requestId() });
  } catch (error) { setRuntime('浏览器暂不支持此检索环境', error.message || '请使用支持模块 Worker 的现代浏览器。', 'error'); updateModes(); }
}

$('#query-form').addEventListener('submit', runSearch);
$('#query').addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); $('#query-form').requestSubmit(); } });
$('#query').addEventListener('input', () => { if (isPanoramic($('#query').value)) $('#cross-company').checked = true; });
for (const input of document.querySelectorAll('input[name="mode"]')) {
  const rememberModeChoice = () => { state.modeChosenByUser = true; state.automaticModeFallback = false; };
  input.addEventListener('click', rememberModeChoice);
  input.addEventListener('change', () => {
    rememberModeChoice(); updateModes();
    if (state.capabilities.dense) setRuntime('三种检索方式均已就绪', `当前使用${modes[selectedMode()]}；检索在当前浏览器内运行，可以打开原 PDF 核验。`, 'ready', '本地检索');
  });
}
for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => switchView(button.dataset.view));
for (const button of document.querySelectorAll('[data-question]')) button.addEventListener('click', () => useQuestion(button.dataset.question, { crossCompany: button.dataset.panoramic === 'true' }));
$('#select-all').addEventListener('click', () => { state.selected = new Set(state.companies.map(company => company.company)); renderCompanies(); });
$('#select-none').addEventListener('click', () => { state.selected.clear(); renderCompanies(); });
updateModes(); loadMetadata(); initWorker();

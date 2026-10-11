"""Validate a separately authored review and publish traceable deliverables.

This script never infers review labels from numeric string coverage. It refuses
stale evaluation snapshots, unsupported citations and incomplete review grids.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODES = ('bm25', 'dense', 'hybrid')
NAMES = {'bm25': 'BM25', 'dense': 'BGE 向量', 'hybrid': 'RRF 融合'}
STATUS = {'complete': '完整', 'partial': '部分', 'missing': '缺失'}
sha = lambda data: hashlib.sha256(data).hexdigest()


def aggregate(statuses):
    if all(s == 'complete' for s in statuses):
        return 'complete'
    if all(s == 'missing' for s in statuses):
        return 'missing'
    return 'partial'


def counts(statuses):
    c = Counter(statuses)
    return {key: c[key] for key in STATUS}


def validate(evaluation_bytes, review, manifest, answer_bytes):
    evaluation = json.loads(evaluation_bytes)
    out = deepcopy(review)
    if out.get('schemaVersion') != 2 or out.get('evaluationSha256') != sha(evaluation_bytes):
        raise ValueError('Review is not bound to these exact frozen evaluation bytes')
    if out.get('answerModuleSha256') != sha(answer_bytes):
        raise ValueError('Answer presentation changed; perform a new review')
    if any(not isinstance(out.get(k), str) or not out[k].strip() for k in ('reviewNote', 'reviewedAt', 'lessonAlignment')) or not isinstance(out.get('protocol'), list) or not out['protocol'] or any(not isinstance(p, str) or not p.strip() for p in out['protocol']):
        raise ValueError('Review metadata and protocol must be complete before publication')
    if len(out.get('questions', [])) != 10:
        raise ValueError('Ten separately reviewed questions are required')
    actual = {q['id']: q for q in evaluation['questions']}
    if set(q['id'] for q in out['questions']) != set(actual):
        raise ValueError('Question IDs disagree')
    reports = {r['code']: r for r in manifest['reports']}
    reviewed_units = 0
    for question in out['questions']:
        original = actual[question['id']]
        if set(question.get('methods', {})) != set(MODES):
            raise ValueError(f"{question['id']}: all three methods need separate reviews")
        question['question'] = original['question']
        question['crossCompany'] = bool(original.get('crossCompany'))
        for mode in MODES:
            result = original['results'][mode]
            method = question['methods'][mode]
            records = method.get('companies') if question['crossCompany'] else [method]
            if question['crossCompany']:
                if len(records or []) != 12 or {r['code'] for r in records} != set(reports):
                    raise ValueError(f"{question['id']}/{mode}: exactly twelve companies required")
            for record in records:
                code = record.get('code') or original['goldEvidence'][0]['code']
                if code not in reports or (not question['crossCompany'] and code != original['goldEvidence'][0]['code']):
                    raise ValueError('Review company does not match the question')
                if record.get('company') and record['company'] != reports[code]['company']:
                    raise ValueError('Review company name disagrees with manifest')
                hits = {h['id']: h for h in result['hits'] if h['code'] == code}
                answer_hits = {h['id']: h for g in result['answer']['groups'] if g['code'] == code for h in g['hits']}
                for dimension, available in [('retrieval', hits), ('answer', answer_hits)]:
                    judgment = record[dimension]
                    if judgment.get('status') not in STATUS or not judgment.get('reason'):
                        raise ValueError(f"{question['id']}/{mode}/{code}: missing dimension verdict")
                    if not isinstance(judgment.get('supportingIds'), list) or any(i not in available for i in judgment['supportingIds']):
                        raise ValueError('Review cites evidence outside this method/company output')
                    if judgment['status'] == 'complete' and not judgment['supportingIds']:
                        raise ValueError('Complete review without supporting evidence')
                if not record['answer'].get('actualAnswerSummary') or not record.get('sourceChecks'):
                    raise ValueError('Actual answer and source checks must both be recorded')
                for source in record['sourceChecks']:
                    report = reports[code]
                    page = source.get('pdfPage')
                    if source.get('code', code) != code:
                        raise ValueError('Source company differs from reviewed company')
                    if source.get('sourceUrl') != report['sourceUrl'] or not isinstance(page, int) or not 1 <= page <= report['pageCount']:
                        raise ValueError('Source page or official URL disagrees with manifest')
                    if not source.get('scope') or not source.get('referenceFacts') or not source.get('checkedTextOrTable'):
                        raise ValueError('A source check needs scope, reference facts and checked content')
                reviewed_units += 1
            if question['crossCompany']:
                for dimension in ('retrieval', 'answer'):
                    method[dimension] = {'status': aggregate([r[dimension]['status'] for r in records]),
                        'companyCounts': counts(r[dimension]['status'] for r in records)}
    if reviewed_units != 96:
        raise ValueError('Expected 24 single-company and 72 panoramic company/method units')
    out['summary'] = {'questionCount': 10, 'retrievalRuns': 30, 'reviewedUnits': reviewed_units, 'methods': {}}
    for mode in MODES:
        summary = {dimension: counts(q['methods'][mode][dimension]['status'] for q in out['questions']) for dimension in ('retrieval', 'answer')}
        summary.update({'canonicalPageHitRate': evaluation['summary']['methods'][mode]['evidenceRecall'],
                        'canonicalMRR': evaluation['summary']['methods'][mode]['meanReciprocalRank'],
                        'automaticNumericCoverage': evaluation['summary']['methods'][mode]['displayedFactCoverage']})
        out['summary']['methods'][mode] = summary
    return out


def md(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value.replace('|', '\\|').replace('\r', '').replace('\n', '<br>')


def count_string(count):
    return f"{count['complete']}/{count['partial']}/{count['missing']}"


def write_record(evaluation, audit, target):
    lines = ['# 财报问答：证据与答案分开核验', '', f"复核日期：{audit['reviewedAt']}；原始检索日期：{evaluation['generatedAt']}。", '', audit['reviewNote'], '',
             f"冻结 evaluation.json SHA-256：`{audit['evaluationSha256']}`", '',
             '## 评价口径', '', *['- ' + text for text in audit['protocol']], '',
             '三路召回和原始答案保持不变。本报告中的“实际展示概述”是复核者的描述；下方折叠区才是当时程序的答案快照。参考值仅用于核对，不回填答案。', '',
             '## 三种检索比较', '', '| 方法 | 原始规范页命中 | 原始 MRR | 自动数字覆盖 | 证据完整题数 | 答案完整题数 |', '|---|---:|---:|---:|---:|---:|']
    for mode in MODES:
        s = audit['summary']['methods'][mode]
        lines.append(f"| {NAMES[mode]} | {s['canonicalPageHitRate']:.1%} | {s['canonicalMRR']:.3f} | {s['automaticNumericCoverage']:.1%} | {s['retrieval']['complete']}/10 | {s['answer']['complete']}/10 |")
    lines += ['', '完整题数采用整题严格口径，全景题必须全部 12 家完整。不能把 96 个公司/方法单元当作 96 个独立测试题。原始规范页命中 BM25 74.2% 高于融合 60.0% 的结论保留；这些自动指标不等于正确率。']
    originals = {q['id']: q for q in evaluation['questions']}
    for question in audit['questions']:
        original = originals[question['id']]
        lines += ['', f"## {question['id']} · {question['question']}", '',
                  '题型：' + ('逐公司全景题，每公司 Top-2' if question['crossCompany'] else '单公司题，Top-10'), '',
                  '参考答案（不是实际输出）：' + original.get('expectedAnswer', ''), '', question.get('scope', '')]
        for mode in MODES:
            review = question['methods'][mode]; result = original['results'][mode]
            lines += ['', f"### {NAMES[mode]}", '', f"召回证据：**{STATUS[review['retrieval']['status']]}**；实际答案：**{STATUS[review['answer']['status']]}**。", '']
            records = review.get('companies', [review])
            if question['crossCompany']:
                lines += ['| 公司 | 召回证据 | 实际答案 |', '|---|---|---|']
                lines += [f"| {r['company']} | {STATUS[r['retrieval']['status']]} | {STATUS[r['answer']['status']]} |" for r in records]
            for record in records:
                if record.get('company'):
                    lines += ['', f"#### {record['company']} · {record['code']}"]
                lines += ['', '**实际展示概述：** ' + record['answer']['actualAnswerSummary'], '',
                          '**证据核验：** ' + record['retrieval']['reason'], '', '**答案核验：** ' + record['answer']['reason'], '',
                          '证据对应块：' + ', '.join('`' + x + '`' for x in record['retrieval']['supportingIds']), '',
                          '答案对应块：' + ', '.join('`' + x + '`' for x in record['answer']['supportingIds']), '']
                for source in record['sourceChecks']:
                    lines += [f"- [官方 PDF 第 {source['pdfPage']} 物理页]({source['sourceUrl']}#page={source['pdfPage']})：{source['scope']}；参考事实：{source['referenceFacts']}。核对内容：{source['checkedTextOrTable']}"]
                if record['answer'].get('errors'):
                    lines += ['', '问题类型：' + '；'.join(map(str, record['answer']['errors']))]
                extras = {k: v for k, v in record.items() if k not in ('code', 'company', 'retrieval', 'answer', 'sourceChecks')}
                if extras:
                    lines += ['', '口径敏感性及补充记录：', '```json', json.dumps(extras, ensure_ascii=False, indent=2), '```']
            lines += ['', '<details><summary>当时实际展示的答案快照（未用参考答案补全）</summary>', '']
            for group in result['answer']['groups']:
                lines += ['', '**' + group['company'] + '**']
                for hit in group['hits']:
                    lines += ['', f"`{hit['id']}` · {hit['section']} · [PDF {hit['pdfPage']}]({hit['sourceUrl']}#page={hit['pdfPage']})", '']
                    lines.extend('> ' + line for line in hit['excerpt'].splitlines())
                    for context in hit.get('contextLines', []):
                        lines += ['', '展示单位/上下文：' + context]
                    table = hit.get('table')
                    if table and table.get('headers'):
                        headers = table['headers']
                        lines += ['', '| ' + ' | '.join(md(cell) for cell in headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
                        for row in table.get('rows', []):
                            lines.append('| ' + ' | '.join(md(cell) for cell in row) + ' |')
            lines += ['', '</details>', '', '<details><summary>全部实际召回块、排名与分数</summary>', '',
                      '| 排名 | 公司 | 块 ID | PDF页 | BM25分 | 向量分 | 排序分 |', '|---:|---|---|---:|---:|---:|---:|']
            for i, hit in enumerate(result['hits'], 1):
                number = lambda key: '—' if hit.get(key) is None else f"{hit[key]:.6f}"
                lines.append(f"| {i} | {hit['company']} | `{hit['id']}` | {hit['pdfPage']} | {number('bm25Score')} | {number('denseScore')} | {number('score')} |")
            lines += ['', '</details>']
    lines += ['', '## 复现与版本边界', '',
              '运行 `node scripts/check-recorded-answers.mjs` 复建 30 组摘录；运行 `python scripts/make_deliverables.py --validate-only` 核验复核版本、逐公司覆盖和引用。重新跑检索后必须重做源文核验，不能复制本次标签。', '',
              '原始 evaluation.json 与旧 manual-review.json 保留作为历史记录；其中“correct”曾表示候选证据充分，不再作为答案准确率。本次补充复核才分别评价证据和展示答案。', '',
              audit.get('lessonAlignment', ''), '']
    target.write_text('\n'.join(lines), encoding='utf-8')


def conclusion_content(audit):
    hybrid = audit['summary']['methods']['hybrid']
    panoramic = [q for q in audit['questions'] if q['crossCompany']]
    company_rows = []
    for q in panoramic:
        for mode in MODES:
            m = q['methods'][mode]
            company_rows.append([q['id'] + ' ' + NAMES[mode], count_string(m['retrieval']['companyCounts']), count_string(m['answer']['companyCounts'])])
    return {
        'title': '命中证据，不等于完成回答',
        'lead': f"保留同一批三路检索与 30 组答案，逐项复核 96 个公司/方法单元。融合检索证据完整 {hybrid['retrieval']['complete']}/10 题，实际答案完整 {hybrid['answer']['complete']}/10 题；全景题须 12 家全部完整。",
        'good': '精确指标适合建库：可回到原页核对年度、单位和主体，摘录式回答也能提供可验证信息。判断对象是实际展示的摘录、单位行及表格，而不是检索全文中的任意数字。',
        'failure': 'Q04 向量结果有两年公司贷款金额，足以计算增幅，但实际答案未完成该计算。Q06 部分结果缺表头年份。全景每家仅取两块，地区、行业、分部或母行指标容易挤占整体口径证据。',
        'scope': 'Q09/Q10 原题未明定集团口径；本次为跨公司比较采用集团/整体口径。南京母行 0.80% 是有出处的另一口径，须与集团 0.83% 区分，不能称为伪造；逐公司记录保留口径敏感性。',
        'why': 'BM25 规范页命中 74.2%，融合 60.0%，融合没有获胜。精确财务词有利于关键词检索；RRF 的语义相近候选可能挤掉目标证据。数字覆盖不检查口径，因此不能当答对率。',
        'next': '下一步先补表头与脚注、逐公司补检索，再核验答案中的数值、单位、期间、主体及引用。保留此次失败记录，改动后用新题重新评价，避免用金标准回填回答。',
        'companyRows': company_rows,
    }


def write_html(audit, target):
    c = conclusion_content(audit); esc = html.escape
    rows = []
    for mode in MODES:
        s = audit['summary']['methods'][mode]
        rows.append([NAMES[mode], f"{s['canonicalPageHitRate']:.1%}", f"{s['canonicalMRR']:.3f}", f"{s['retrieval']['complete']}/10", f"{s['answer']['complete']}/10"])
    def table(headers, data):
        return '<table><thead><tr>' + ''.join('<th>' + esc(x) + '</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(x) + '</td>' for x in row) + '</tr>' for row in data) + '</tbody></table>'
    target.write_text(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base href="/report-qa/"><title>一页结论 · 证据与答案分别核验</title><link rel="stylesheet" href="./conclusion.css"></head><body>
<nav class="screen-nav"><a href="./#evaluation">返回研究台</a><a href="./downloads/evaluation-record.md">逐项核验与实际答案快照</a></nav>
<main class="sheet"><header><p class="eyebrow">2025 银行年报 / 2026-10-11 补充复核</p><h1>{esc(c['title'])}</h1><p class="lead">{esc(c['lead'])}</p><p class="corpus">12 份全文 / 3,265 页 / 17,474 个块 / 2,607 张提取表</p></header>
<section class="metrics"><div class="section-title"><span>01</span><h2>检索与答案分别评价</h2></div>{table(['方法','规范页命中','MRR','证据完整','答案完整'],rows)}<p class="fine-print">前两列保留原自动指标；后两列是本次逐项复核。完整题数不等于数字覆盖，也不表示首条命中率；小样本不外推。</p></section>
<section><div class="section-title"><span>02</span><h2>全景逐公司核验：完整 / 部分 / 缺失</h2></div>{table(['题目与方法','召回证据','实际答案'],c['companyRows'])}<p class="fine-print">每行 12 家。实际回答、原始召回、逐公司理由及原页链接见逐题记录。</p></section>
<div class="findings"><section><div class="section-title"><span>03</span><h2>什么适合建库</h2></div><p>{esc(c['good'])}</p></section><section><div class="section-title"><span>04</span><h2>真实失败</h2></div><p>{esc(c['failure'])}</p></section></div>
<section class="reason"><div class="section-title"><span>05</span><h2>口径、原因与下一步</h2></div><p>{esc(c['scope'])}</p><p>{esc(c['why'])}</p><p>{esc(c['next'])}</p></section>
<footer><p>Agent 辅助逐项核对原始 PDF，未经独立人工双评。回答为确定性摘录，无生成式大模型。复核绑定冻结评测和答案模块哈希，旧证据充分标签不作答对率。</p><div><span>原始检索 2026-09-28 / 复核 2026-10-11</span><span>1 / 1</span></div></footer></main></body></html>''', encoding='utf-8')


def write_pdf(audit, target):
    from functools import partial
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from pypdf import PdfReader
    import os
    font = Path(os.environ.get('REPORT_QA_CJK_FONT', str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/simhei.ttf')))
    if not font.is_file():
        raise ValueError('Set REPORT_QA_CJK_FONT to a local CJK TrueType font')
    pdfmetrics.registerFont(TTFont('ReviewCJK', str(font)))
    styles = {name: ParagraphStyle(name, fontName='ReviewCJK', fontSize=size, leading=lead, textColor=colors.HexColor('#18273f'), wordWrap='CJK', spaceAfter=after) for name, size, lead, after in [('title',21,27,10),('body',9,13.2,5),('small',7.5,10.7,5),('heading',10.5,15,5)]}
    p = lambda text, kind='body': Paragraph(html.escape(str(text)), styles[kind])
    c = conclusion_content(audit)
    story = [p('2025 银行业年报 / 证据与答案分开复核','small'),p(c['title'],'title'),p(c['lead']),p('12 份全文 / 3,265 页 / 17,474 个块 / 2,607 张提取表','small')]
    def add_table(headers, rows, widths):
        t = Table([[p(x,'small') for x in headers]] + [[p(x,'small') for x in r] for r in rows], colWidths=widths, hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf1f5')),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#dfe3e6')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),1)]))
        story.extend([t,Spacer(1,6)])
    story.append(p('01  检索表现与答案表现','heading'))
    rows=[]
    for mode in MODES:
        s=audit['summary']['methods'][mode]
        rows.append([NAMES[mode],f"{s['canonicalPageHitRate']:.1%}",f"{s['canonicalMRR']:.3f}",f"{s['retrieval']['complete']}/10",f"{s['answer']['complete']}/10"])
    add_table(['方法','规范页命中','MRR','证据完整','答案完整'],rows,[120,103,65,103,104])
    story.append(p('规范页命中与 MRR 保留原自动指标。完整题数来自本次复核；数字覆盖不等于正确率，不按出现目标数字就判对。','small'))
    story.append(p('02  全景逐公司：完整 / 部分 / 缺失','heading'))
    add_table(['题目与方法','召回证据（12家）','实际答案（12家）'],c['companyRows'],[175,160,160])
    for title,key in [('03  适用与失败','good'),('', 'failure'),('04  口径与原因','scope'),('', 'why'),('05  下一步','next')]:
        if title: story.append(p(title,'heading'))
        story.append(p(c[key]))
    story.append(p('原文逐项复核由 Agent 辅助完成，未经独立人工双评。30组答案与网页组装器复建一致；完整快照、对应块、公司级理由和官方页码见仓库逐题记录。','small'))
    def footer(canvas, doc):
        canvas.setFont('ReviewCJK',7)
        canvas.setFillColor(colors.HexColor('#697584'))
        canvas.drawString(50,25,'原检索 2026-09-28 / 补充复核 2026-10-11')
        canvas.drawRightString(545,25,f'{doc.page} / 1')
    SimpleDocTemplate(str(target),pagesize=A4,leftMargin=50,rightMargin=50,topMargin=31,bottomMargin=40,title=c['title'],author='银行年报研究台').build(story,onFirstPage=footer,onLaterPages=footer,canvasmaker=partial(Canvas,invariant=1))
    if len(PdfReader(str(target)).pages)!=1:
        raise ValueError('Conclusion must fit one page; render and adjust layout')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evaluation',type=Path,default=ROOT/'site/data/evaluation.json')
    parser.add_argument('--review',type=Path,default=ROOT/'reports/source-answer-review.json')
    parser.add_argument('--manifest',type=Path,default=ROOT/'site/data/manifest.json')
    parser.add_argument('--output-dir',type=Path,default=ROOT.parents[1]/'财报问答交付')
    parser.add_argument('--html',type=Path,default=ROOT/'site/conclusion.html')
    parser.add_argument('--validate-only',action='store_true')
    parser.add_argument('--skip-pdf',action='store_true')
    args=parser.parse_args()
    source=args.evaluation.read_bytes()
    audit=validate(source,json.loads(args.review.read_text(encoding='utf-8')),json.loads(args.manifest.read_text(encoding='utf-8')),(ROOT/'site/answer.mjs').read_bytes())
    if args.validate_only:
        print(json.dumps(audit['summary'],ensure_ascii=False)); return
    evaluation=json.loads(source)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(audit,ensure_ascii=False,indent=2)+'\n'
    (ROOT/'site/data/source-answer-review.json').write_text(encoded,encoding='utf-8')
    write_record(evaluation,audit,ROOT/'reports/evaluation-record.md')
    record=(ROOT/'reports/evaluation-record.md').read_bytes()
    (ROOT/'site/downloads/evaluation-record.md').write_bytes(record)
    (args.output_dir/'逐题评测记录.md').write_bytes(record)
    write_html(audit,args.html)
    if not args.skip_pdf:
        write_pdf(audit,ROOT/'reports/conclusion.pdf')
        pdf=(ROOT/'reports/conclusion.pdf').read_bytes()
        (ROOT/'site/downloads/conclusion.pdf').write_bytes(pdf)
        (args.output_dir/'一页结论.pdf').write_bytes(pdf)
    print(json.dumps(audit['summary'],ensure_ascii=False))


if __name__=='__main__':
    main()

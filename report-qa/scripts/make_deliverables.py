"""Build the one-page conclusion and traceable evaluation record from final review.

Requires all ten hybrid manualReview entries; baseline results are never assigned
an invented manual verdict. Set REPORT_QA_CJK_FONT to a local TrueType CJK font
when the platform does not provide one. Font paths never appear in public output.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MODES = ("bm25", "dense", "hybrid")
LABELS = {"bm25": "BM25 关键词", "dense": "BGE 语义向量", "hybrid": "RRF 混合检索"}
VERDICTS = {"correct": "正确", "partial": "部分正确", "incorrect": "错误"}


def pct(value):
    return f"{float(value):.1%}"


def number(value):
    return "—" if value is None else f"{float(value):.6f}"


def review_status(review):
    return review.get("status", review.get("verdict", ""))


def review_reason(review):
    return str(review.get("reason", review.get("errorAnalysis", "")))


def validate(evaluation, manifest):
    questions = evaluation["questions"]
    if len(questions) != 10 or len({q["id"] for q in questions}) != 10:
        raise ValueError("The final evaluation must contain ten unique questions")
    counts = Counter()
    for question in questions:
        for mode in MODES:
            result = question["results"][mode]
            if not isinstance(result.get("hits"), list) or "metrics" not in result:
                raise ValueError(f"{question['id']} / {mode}: actual retrieval evidence missing")
        review = question["results"]["hybrid"].get("manualReview")
        if not isinstance(review, dict) or review_status(review) not in VERDICTS or not review_reason(review):
            raise ValueError(f"{question['id']}: final hybrid manual review is required")
        counts[review_status(review)] += 1
    if evaluation["summary"].get("manualReviewMode") != "hybrid":
        raise ValueError("summary.manualReviewMode must explicitly identify hybrid")
    declared = evaluation["summary"].get("manualCounts")
    if declared and any(int(declared.get(k, 0)) != counts[k] for k in VERDICTS):
        raise ValueError("Manual review counts disagree with question-level verdicts")
    if len(manifest["reports"]) < 10:
        raise ValueError("At least ten full annual reports are required")
    return counts


def content(evaluation, manifest, counts):
    """Small, source-bound editorial layer shared by HTML and the PDF."""
    methods = evaluation["summary"]["methods"]
    best = max(MODES, key=lambda mode: methods[mode]["evidenceRecall"])
    return {
        "eyebrow": "2025 银行业年报 · 检索问答实验",
        "title": "能找到原文证据，仍需核对口径",
        "lead": f"默认混合检索经 10 题人工复核：{counts['correct']} 题证据充分、{counts['partial']} 题未完整答对（部分回答）。按完整摘录核验，不是首条准确率；全景题仍有缺失和集团/母行口径错误。",
        "stats": f"{len(manifest['reports'])} 份完整年报  /  {manifest['totalPages']:,} 页  /  {manifest['totalChunks']:,} 个检索块  /  {manifest['totalTables']:,} 张提取表",
        "good": "单公司、指标名称明确的数字题较稳。公司过滤缩小范围，保留表格行列、单位和年份后，可回到原页核验。Q01-Q06、Q08 的完整摘录证据足以作答；部分题需联合多条证据。",
        "failure": "Q07 混入季度相关片段，未形成干净的年度回答。Q09 全景题仅 3 家完整、5 家部分、4 家缺失；Q10 按集团口径仅 5 家完整、3 家部分、4 家缺失。南京银行 0.80% 属母公司口径，是真实值但不能当成集团答案。",
        "why": f"本题集以精确财务名词为主，{LABELS[best]}的规范页命中最高；语义相近不保证年份、单位与口径正确。全景每家只取 2 块，多层表头和重复披露会占用名额。RRF 融合也可能把含正确证据的候选排后。",
        "next": ["先按公司、年度、集团/母公司、单位规范化指标，再生成答案；冲突时并列原文，不自动选值。", "全景题逐公司补检索：扩大候选池，按“公司 × 指标”检查覆盖，缺失项明确标出。", "加入表头与跨页表衔接、相关性重排；扩充人工题集，分别评估数值、单位、期间和口径。"],
        "scope": "真实 512 维 BGE-small-zh 向量 + BM25 + RRF；回答为检索原文摘录，未使用生成式大模型。普通题 Top-10；2 道全景题逐公司 Top-2。",
        "metrics_note": "指标均为 10 题宏平均。“规范页命中”只检查指定来源页，不代表全部相关证据召回；MRR 衡量首次规范证据排名；“数字覆盖”是自动字符串检查，不是人工正确率。仅混合检索有人工作答核验；小样本不外推至所有财报问题。",
        "limits": "未做图片 OCR；跨页表未自动合并；跨公司单位与财务口径未自动统一。表格为 PDF 提取结果，仍须查看原表。",
        "date": str(evaluation.get("generatedAt", ""))[:10],
    }


def metric_rows(evaluation):
    result = []
    for mode in MODES:
        data = evaluation["summary"]["methods"][mode]
        result.append([LABELS[mode], pct(data["evidenceRecall"]), f"{data['meanReciprocalRank']:.3f}", pct(data["displayedFactCoverage"])])
    return result


def write_html(evaluation, manifest, counts, target):
    c = content(evaluation, manifest, counts)
    esc = html.escape
    rows = "\n".join("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in row) + "</tr>" for row in metric_rows(evaluation))
    steps = "".join(f"<li>{esc(step)}</li>" for step in c["next"])
    doc = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<base href="/report-qa/"><title>一页结论 · 银行年报研究台</title><meta name="description" content="12份银行年报、10道真实问题的检索问答实验结论与边界。"><link rel="stylesheet" href="./conclusion.css"></head>
<body><nav class="screen-nav" aria-label="页面导航"><a href="./">← 返回研究台</a><a href="./data/evaluation.json">完整评测数据</a></nav>
<main class="sheet"><header><p class="eyebrow">{esc(c['eyebrow'])}</p><p class="edition">RESEARCH NOTE / 01</p><h1>{esc(c['title'])}</h1><p class="lead">{esc(c['lead'])}</p><p class="corpus">{esc(c['stats'])}</p></header>
<section class="metrics"><div class="section-title"><span>01</span><h2>三种检索方式的实际表现</h2></div><table><thead><tr><th scope="col">方法</th><th scope="col">规范页命中</th><th scope="col">MRR</th><th scope="col">自动数字覆盖</th></tr></thead><tbody>{rows}</tbody></table><p class="fine-print">{esc(c['metrics_note'])}</p></section>
<div class="findings"><section><div class="section-title"><span>02</span><h2>哪里有效</h2></div><p>{esc(c['good'])}</p></section><section><div class="section-title"><span>03</span><h2>哪里翻车</h2></div><p>{esc(c['failure'])}</p></section></div>
<section class="reason"><div class="section-title"><span>04</span><h2>为什么</h2></div><p>{esc(c['why'])}</p></section>
<section class="next"><div class="section-title"><span>05</span><h2>下一轮直接改这三件事</h2></div><ol>{steps}</ol></section>
<footer><p>{esc(c['scope'])}</p><p>{esc(c['limits'])}</p><div><span>依据：固定题集、实际召回与人工逐题核验</span><span>{esc(c['date'])} · 1 / 1</span></div></footer></main></body></html>'''
    target.write_text(doc + "\n", encoding="utf-8")


def md(value):
    return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def write_record(evaluation, manifest, counts, target, evaluation_hash):
    lines = ["# 银行年报问答：逐题评测记录", "", f"评测生成时间：{evaluation.get('generatedAt', '')}", "", f"评测 JSON SHA-256：`{evaluation_hash}`", "", f"语料：{len(manifest['reports'])} 份完整 2025 年报、{manifest['totalPages']:,} 个物理页、{manifest['totalChunks']:,} 个检索块、{manifest['totalTables']:,} 张提取表。", "", "## 口径与复核范围", "", "- 10 道固定问题；普通题取 Top-10，2 道全景题逐公司取 Top-2。排名及分数保留实际检索输出；不同方法的原始分数不可直接比较。", "- 仅默认混合检索（hybrid）的 10 道题完成人工复核；BM25 和 dense 两组只报告自动指标和真实召回，不赋予人工对错标签。", f"- 混合检索人工判断：{counts['correct']} 题证据充分、{counts['partial']} 题未完整答对（部分回答）。按完整摘录核验，不是首条准确率，也不表示每条片段中的数值、期间和口径都正确。", "- 规范页命中（evidenceRecall）只检查人工预先指定的物理页；其他重复披露页可能包含正确答案，但不计该规范页命中。它不是全部相关证据的召回率。", "- displayedFactCoverage 是展示文本对预设数字字符串的自动覆盖，不验证单位、年份和集团/母公司口径，不是人工正确率。", "- 真实向量为 512 维 BGE-small-zh；回答按召回原文摘录组装，未调用生成式大模型。", "", "## 三种方法的自动指标", "", "| 方法 | 规范页命中 | MRR | 自动数字覆盖 |", "|---|---:|---:|---:|"]
    lines += ["| " + " | ".join(row) + " |" for row in metric_rows(evaluation)]
    for q in evaluation["questions"]:
        lines += ["", f"## {q['id']} · {q['question']}", "", f"题型：{'全景题（逐公司 Top-2）' if q['type'] != 'single' else '单公司题（Top-10）'}", "", f"参考答案：{q.get('expectedAnswer', '')}", "", "预先指定的规范证据：", ""]
        for gold in q.get("goldEvidence", []):
            link = f"{gold['sourceUrl'].split('#')[0]}#page={gold['pdfPage']}"
            lines.append(f"- {gold['company']} · [PDF 物理页 {gold['pdfPage']}]({link}) · {gold.get('term', '')} · 参考值 {' / '.join(map(str, gold.get('values', [])))} · 单位 {gold.get('unit', '未列')}。{gold.get('note', '')}")
        for mode in MODES:
            r = q["results"][mode]; metrics = r["metrics"]
            lines += ["", f"### {LABELS[mode]}", ""]
            if mode == "hybrid":
                review = r["manualReview"]
                lines += [f"人工判断：**{VERDICTS[review_status(review)]}**。{review_reason(review)}", ""]
                detail = {k: v for k, v in review.items() if k not in {"status", "verdict", "reason", "errorAnalysis"}}
                if detail:
                    lines += ["人工复核明细（沿用原记录）：", "", "```json", json.dumps(detail, ensure_ascii=False, indent=2), "```", ""]
            else:
                lines += ["人工判断：**未逐题人工评分**。以下为真实召回及自动检查；不能据数字覆盖判定答对。", ""]
            lines += [f"自动指标：规范页命中 {pct(metrics['evidenceRecall'])}；MRR {metrics['meanReciprocalRank']:.3f}；自动数字覆盖 {pct(metrics['displayedFactCoverage'])}。", "", "| 序号 | 公司 | 检索块 ID | 物理页 | 章节 | 总分 | BM25分 | 向量分 | BM25排名 | 向量排名 | 混合排名 |", "|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|"]
            for i, hit in enumerate(r["hits"], 1):
                values = [i, hit["company"], f"`{hit['id']}`", hit["pdfPage"], hit["section"], number(hit.get("score")), number(hit.get("bm25Score")), number(hit.get("denseScore")), hit.get("bm25Rank") or "—", hit.get("denseRank") or "—", hit.get("hybridRank") or "—"]
                lines.append("| " + " | ".join(md(v) for v in values) + " |")
            lines += ["", "规范证据逐项自动核对：", "", "| 公司 / 页 | 规范页命中 | 该项预设数字全部展示 | 首次相关排名 | 命中块 |", "|---|---|---|---:|---|"]
            for gold in metrics.get("goldDetails", []):
                values = [f"{gold['company']} / {gold['pdfPage']}", "是" if gold["canonicalEvidenceRecalled"] else "否", "是" if gold["displayedFactsComplete"] else "否", gold.get("firstRelevantRank") or "—", ", ".join(gold.get("retrievedGoldChunkIds", [])) or "无"]
                lines.append("| " + " | ".join(md(v) for v in values) + " |")
    lines += ["", "## 原始材料与复现", "", "全部命中原文、结构化表格、实际展示回答、引用与查询耗时保存在网站 `data/evaluation.json`；报告来源、页数与哈希保存在 `data/manifest.json`。本记录不替代原表核验。", ""]
    target.write_text("\n".join(lines), encoding="utf-8")


def cjk_font():
    override = os.environ.get("REPORT_QA_CJK_FONT")
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise ValueError("REPORT_QA_CJK_FONT does not point to a font file")
    system = Path(os.environ.get("WINDIR", "C:/Windows"))
    candidates = [system / "Fonts" / "simhei.ttf", Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("A CJK TrueType font is required; set REPORT_QA_CJK_FONT")


def write_pdf(evaluation, manifest, counts, target):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from pypdf import PdfReader

    pdfmetrics.registerFont(TTFont("ReportCJK", str(cjk_font())))
    c = content(evaluation, manifest, counts)
    ink = colors.HexColor("#18273f"); muted = colors.HexColor("#566474"); gold = colors.HexColor("#9a793e")
    styles = {
        "eyebrow": ParagraphStyle("eyebrow", fontName="ReportCJK", fontSize=9, leading=13, textColor=gold),
        "title": ParagraphStyle("title", fontName="ReportCJK", fontSize=22, leading=29, textColor=ink, spaceAfter=10),
        "lead": ParagraphStyle("lead", fontName="ReportCJK", fontSize=10.2, leading=16, textColor=ink, spaceAfter=11, wordWrap="CJK"),
        "body": ParagraphStyle("body", fontName="ReportCJK", fontSize=9.2, leading=14.3, textColor=ink, wordWrap="CJK", spaceAfter=5),
        "small": ParagraphStyle("small", fontName="ReportCJK", fontSize=7.6, leading=11.6, textColor=muted, wordWrap="CJK"),
        "section": ParagraphStyle("section", fontName="ReportCJK", fontSize=11, leading=17, textColor=ink, spaceBefore=11, spaceAfter=6),
    }
    def p(text, style="body"):
        return Paragraph(html.escape(text), styles[style])
    def section(n, title):
        return p(f"{n}  {title}", "section")
    story = [p(c["eyebrow"], "eyebrow"), Spacer(1, 8), p(c["title"], "title"), p(c["lead"], "lead"), p(c["stats"], "small"), section("01", "三种检索方式的实际表现")]
    data = [[p(x, "small") for x in ["方法", "规范页命中", "MRR", "自动数字覆盖"]]]
    data += [[p(value, "body") for value in row] for row in metric_rows(evaluation)]
    table = Table(data, colWidths=[196, 96, 70, 133], hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf1f5")), ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#f6f1e7")), ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#dfe3e6")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [table, Spacer(1, 7), p(c["metrics_note"], "small")]
    story += [section("02", "哪里有效"), p(c["good"]), section("03", "哪里翻车"), p(c["failure"]), section("04", "为什么"), p(c["why"]), section("05", "下一轮直接改这三件事")]
    story += [p(f"{i}. {step}") for i, step in enumerate(c["next"], 1)]
    story += [Spacer(1, 10), p(c["scope"], "small"), Spacer(1, 4), p(c["limits"], "small")]
    def footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor("#dfe3e6")); canvas.line(50, 40, 545, 40)
        canvas.setFont("ReportCJK", 7); canvas.setFillColor(muted)
        canvas.drawString(50, 27, "依据：固定题集、实际召回与人工逐题核验")
        canvas.drawRightString(545, 27, f"{c['date']} · {doc.page} / 1")
    doc = SimpleDocTemplate(str(target), pagesize=A4, rightMargin=50, leftMargin=50, topMargin=39, bottomMargin=52, title=c["title"], author="银行年报研究台")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    if len(PdfReader(str(target)).pages) != 1:
        raise RuntimeError("One-page layout exceeded one page; adjust layout and render again before delivery")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation", type=Path, default=ROOT / "site/data/evaluation.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "site/data/manifest.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT.parents[1] / "财报问答交付")
    parser.add_argument("--html", type=Path, default=ROOT / "site/conclusion.html")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    source = args.evaluation.read_bytes()
    evaluation = json.loads(source); manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    counts = validate(evaluation, manifest)
    if args.validate_only:
        print(json.dumps({"status": "validated", "manualReviewMode": "hybrid", "counts": counts}, ensure_ascii=False))
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.html.parent.mkdir(parents=True, exist_ok=True)
    write_html(evaluation, manifest, counts, args.html)
    write_record(evaluation, manifest, counts, args.output_dir / "逐题评测记录.md", hashlib.sha256(source).hexdigest())
    write_pdf(evaluation, manifest, counts, args.output_dir / "一页结论.pdf")
    print(json.dumps({"status": "generated", "pdfPages": 1, "questions": 10, "retrievalRuns": 30, "manualReviews": 10, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()

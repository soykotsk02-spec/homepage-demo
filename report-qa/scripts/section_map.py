"""Verified physical-page chapter map for the twelve pinned 2025 bank reports.

Pages are one-based PDF pages, never printed financial-statement page numbers.
Main chapter starts were checked against each PDF's contents AND start-page text.
Bookmarks are not authoritative: Qingdao has three bookmarks one page early.
This module deliberately does not guess a chapter for unknown reports/pages.

Run with --workdir PATH --output PATH to write the auditable public JSON map.
It reads existing extraction caches only for short source-heading evidence.
"""
from bisect import bisect_right
from pathlib import Path
import argparse
import hashlib
import json

UNRECOGNIZED = '未识别章节'
FRONT = '封面、目录与前言'

# code: (company, PDF page count, TOC physical page, main-text physical offset,
#        [(physical start, normalized chapter title)], verification notes)
SPECS = {
    '600036': ('招商银行', 350, 2, 1, [
        (1, FRONT), (11, '第一章 公司简介'), (14, '第二章 会计数据和财务指标摘要'),
        (19, '第三章 管理层讨论与分析'), (71, '第四章 环境、社会与治理(ESG)'),
        (82, '第五章 公司治理'), (108, '第六章 重要事项'),
        (114, '第七章 股份变动及股东情况'), (119, '第八章 财务报告'),
        (350, '封底'),
    ], '目录印刷页加1；正文页眉逐项复核。财务报告持续至物理349页，350页为封底。页眉页码不属于章节名称。'),
    '000001': ('平安银行', 288, 3, 1, [
        (1, FRONT), (9, '第一章 公司简介'), (17, '第二章 会计数据和财务指标'),
        (25, '第三章 管理层讨论与分析'), (64, '第四章 公司治理、环境和社会'),
        (92, '第五章 重要事项'), (97, '第六章 股份变动及股东情况'),
        (106, '第七章 债券相关情况'), (107, '第八章 财务报告'),
    ], '主章节书签、目录印刷页加1及正文起始页一致；财务附件归属第八章。'),
    '002142': ('宁波银行', 237, 3, 1, [
        (1, '封面'), (2, '第一节 重要提示、目录及释义'), (6, '第二节 公司简介'),
        (9, '第三节 会计数据和财务指标摘要'), (12, '第四节 董事长致辞'),
        (14, '第五节 行长致辞'), (16, '第六节 管理层讨论与分析'),
        (53, '第七节 公司治理、环境和社会'), (67, '第八节 重要事项'),
        (70, '第九节 股份变动及股东情况'), (74, '第十节 财务报告'),
    ], '主章节书签、目录印刷页加1及正文起始页一致；附后审计报告及报表仍属于第十节。'),
    '600919': ('江苏银行', 227, 2, 1, [
        (1, FRONT), (9, '第一节 公司简介'), (17, '第二节 财务概要'),
        (27, '第三节 经营情况讨论与分析'), (59, '第四节 公司治理'),
        (83, '第五节 环境与社会责任'), (91, '第六节 重要事项'),
        (97, '第七节 股份变动及股东情况'), (101, '第八节 优先股相关情况'),
        (107, '第九节 财务报告'), (227, '封底'),
    ], '无可用章节书签。目录印刷页008/016/026/058/082/090/096/100/106全部加1，并逐项核对实际标题；107页直接开始审计报告，226页仍为财务补充资料。'),
    '601009': ('南京银行', 252, 3, 2, [
        (1, FRONT), (5, '第一节 重要提示及释义'), (7, '董事长致辞'), (10, '行长致辞'),
        (12, '第二节 公司简介'), (15, '第三节 主要会计数据和财务指标'),
        (20, '第四节 管理层讨论与分析'), (60, '第五节 公司治理、环境与社会'),
        (101, '第六节 重要事项'), (106, '第七节 股份变动及股东情况'),
        (114, '第八节 债券相关情况'), (117, '第九节 财务报告'), (252, '封底'),
    ], '目录印刷页加2，与不带章节序号的PDF书签一致。15/20/60/101/106/114/117页为相应章节扉页，正文从后页继续；117页已属财务报告，119页开始审计报告。'),
    '600926': ('杭州银行', 278, 3, 0, [
        (1, FRONT), (8, '第一节 释义；第二节 公司简介（同页）'),
        (9, '第二节 公司简介'), (13, '第三节 会计数据和财务指标'),
        (17, '第四节 经营情况讨论与分析'), (58, '第五节 公司治理、环境和社会'),
        (86, '第六节 重要事项'), (93, '第七节 股份变动及股东情况'),
        (100, '第八节 可转换公司债券情况'), (102, '第九节 财务报告'),
    ], '年报主体印刷页等于物理页。物理8页确有第一、第二节两标题，因此页级元数据明确同时列出；9页继续第二节。102页指向财务附件，103页起附件重新编号，仍归财务报告。'),
    '601838': ('成都银行', 267, 4, 0, [
        (1, '封面'), (2, '第一节 重要提示、目录和释义'), (6, '第二节 董事长致辞'),
        (9, '第三节 行长致辞'), (12, '第四节 公司简介和主要财务指标'),
        (21, '第五节 管理层讨论与分析'), (66, '第六节 公司治理、环境和社会'),
        (104, '第七节 重要事项'), (110, '第八节 股份变动及股东情况'),
        (121, '第九节 财务报告'),
    ], '年报主体印刷页等于物理页，全部主章节实际起始页已核对；122页起财务附件重新编号，仍归第九节。'),
    '002966': ('苏州银行', 254, 3, 0, [
        (1, '封面'), (2, '第一节 重要提示、目录和释义'), (6, '第二节 董事长致辞'),
        (8, '第三节 行长致辞'), (10, '第四节 公司简介和主要财务指标'),
        (17, '第五节 管理层讨论与分析'), (57, '第六节 公司治理、环境和社会'),
        (76, '第七节 重要事项'), (88, '第八节 股份变动及股东情况'),
        (94, '第九节 债券相关情况'), (97, '第十节 财务报告'),
    ], '年报主体印刷页等于物理页，全部主章节实际起始页已核对；98页起财务附件重新编号，仍归第十节。'),
    '601577': ('长沙银行', 226, 3, 1, [
        (1, FRONT), (9, '第一节 释义'), (10, '第二节 公司简介和主要财务指标'),
        (14, '第三节 管理层讨论与分析'), (53, '第四节 公司治理、环境和社会'),
        (77, '第五节 重要事项'), (83, '第六节 股份变动及股东情况'),
        (89, '第七节 财务报告'), (90, '第八节 备查文件目录'),
        (91, '第七节 财务报告（附后财务报告封面及目录）'),
        (94, '第七节 财务报告（附后审计报告）'),
        (101, '第七节 财务报告（附后财务报表）'),
        (109, '第七节 财务报告（附后财务报表附注）'),
        (223, '第七节 财务报告（审计机构资质附件）'),
    ], '主体目录印刷页加1。89页明示财务及审计报告详见附件，90页仅为备查目录。91页为已审财务报表封面；93页附件目录将附件印刷1/8/16/130页分别定位到物理94/101/109/223页，均已核对。不得把91—226页标为备查目录。'),
    '002948': ('青岛银行', 310, 4, 1, [
        (1, '封面'), (2, '第一节 重要提示、目录和释义'),
        (6, '第二节 公司简介和主要财务指标'), (12, '第三节 董事长致辞'),
        (13, '第四节 行长致辞'), (14, '第五节 管理层讨论与分析'),
        (74, '第六节 公司治理、环境和社会'), (110, '第七节 重要事项'),
        (120, '第八节 股份变动及股东情况'), (127, '第九节 董事会报告'),
        (134, '第十节 附件（财务报表及审计报告）'),
    ], '以目录印刷页加1及实际起始标题为准。第三、第七、第十节的PDF书签错误地指向物理11/109/133页，实际应为12/110/134页。134页明示附件为财务报表；135页起附后报告属于该节。'),
    '601187': ('厦门银行', 273, 9, 0, [
        (1, FRONT), (10, '第一节 释义'), (11, '第二节 公司简介和主要财务指标'),
        (18, '第三节 管理层讨论与分析'), (59, '第四节 公司治理、环境和社会'),
        (90, '第五节 重要事项'), (99, '第六节 普通股股份变动及股东情况'),
        (107, '第七节 财务报告'), (108, '年度报告书面确认意见'),
        (109, '第七节 财务报告（附后财务报表及审计报告）'),
    ], '主体目录印刷页等于物理页。107页明示财务全文见附件；108页为董事、高管年度报告书面确认意见；109页为已审财务报表封面，110页为附件目录，111页起审计正文。'),
    '601229': ('上海银行', 303, 3, 1, [
        (1, FRONT), (11, '第一章 公司简介'), (16, '第二章 会计数据和财务指标概要'),
        (20, '第三章 管理层讨论与分析'), (77, '第四章 公司治理、环境和社会'),
        (119, '第五章 重要事项'), (133, '第六章 股份变动及股东情况'),
        (141, '第七章 债券相关情况'), (145, '第八章 财务报告'),
        (146, '年度报告书面确认意见'), (147, '第八章 财务报告（附后财务报表及审计报告）'),
    ], '主体目录印刷页加1。145页明示财务全文见附件；146页为年度报告书面确认意见；147页为财务报表封面，148页起审计报告；附件重新编号。'),
}


def section_for(code, page):
    """Return a verified page-level chapter label or explicit unrecognized value."""
    spec = SPECS.get(str(code).zfill(6))
    if spec is None or isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= spec[1]:
        return UNRECOGNIZED
    rows = spec[4]
    return rows[bisect_right([row[0] for row in rows], page) - 1][1]


def report_page_count(code):
    spec = SPECS.get(str(code).zfill(6))
    return spec[1] if spec else None


def write_map(workdir, output):
    sources = {r['code']: r for r in json.loads((workdir / 'downloads.json').read_text(encoding='utf-8'))}
    reports = []
    for code, (company, count, toc, offset, rows, notes) in SPECS.items():
        source = sources[code]
        actual_hash = hashlib.sha256((workdir / source['file']).read_bytes()).hexdigest()
        if actual_hash != source['sha256']:
            raise ValueError(f'{code}: source PDF hash mismatch')
        cached = json.loads((workdir / 'extracted' / f'{code}.json').read_text(encoding='utf-8'))
        if len(cached['pages']) != count:
            raise ValueError(f'{code}: source page count mismatch')
        entries = []
        for index, (start, title) in enumerate(rows):
            end = rows[index + 1][0] - 1 if index + 1 < len(rows) else count
            page = cached['pages'][start - 1]
            # Short headings only; no financial tables or contact details copied.
            evidence = ' | '.join(page['text'].splitlines()[:3])[:180]
            entries.append({'pdfStart': start, 'pdfEnd': end, 'section': title, 'evidencePdfPage': start, 'headingEvidence': evidence})
        reports.append({'code': code, 'company': company, 'reportYear': 2025, 'pageCount': count,
                        'sourceUrl': source['sourceUrl'], 'pdfSha256': actual_hash, 'tocPdfPage': toc,
                        'mainContentsPrintedToPhysicalOffset': offset,
                        'verification': '目录与实际起始页交叉核对；书签仅作为辅助，未直接采用未经核对的书签。',
                        'notes': notes, 'sections': entries})
    result = {'schemaVersion': 1, 'reportYear': 2025, 'pageNumbering': 'one-based PDF physical pages',
              'unrecognizedLabel': UNRECOGNIZED,
              'limitations': ['此映射仅适用于清单SHA-256所固定的2025年报文件，不能套用其他版本。',
                              '物理页是页级来源定位；同页含两个章节时明确同时标注，未声称逐字符章节定位。',
                              '财务附件重新编号时保持物理页定位，不能沿用主体印刷页偏移。'],
              'reports': reports}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'reports' / 'sections.json')
    args = parser.parse_args()
    result = write_map(args.workdir, args.output)
    print(json.dumps({'reports': len(result['reports']), 'ranges': sum(len(r['sections']) for r in result['reports']), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

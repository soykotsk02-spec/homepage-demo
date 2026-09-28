#!/usr/bin/env python3
"""Re-run source-grounded extraction checks against the downloaded official PDFs.

Usage (from report-qa):
    python scripts/verify_extraction.py --workdir /path/to/downloaded/pdfs
    python scripts/verify_extraction.py --workdir /path/to/pdfs --json report.json

Requires pdfplumber and the adjacent table_extract.py. No network calls, model,
retriever, generated chunks, or questions.json are used. The fixed expectations
below were transcribed from the original 2025 annual reports. Page numbers are
one-based PHYSICAL PDF pages, not the printed page numbers. Each check verifies
both the original PDF words at the labelled source row and the extracted cells.
The JSON result includes input SHA-256 hashes and official source links.

This is a representative regression sample, not proof that every table in all
reports is perfect. Layout checks cover the discovered native-grid, header-only,
shaded-column, ornamental-cell, and borderless-table failure modes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

import pdfplumber

from table_extract import extract_tables


# Independently transcribed source facts: code, bank, physical page, original
# currency unit, 2025 revenue. Sources are the official PDFs in ../sources.json.
REVENUE = [
    ("600036", "招商银行", 14, "百万元", "337,532"),
    ("000001", "平安银行", 17, "百万元", "131,442"),
    ("002142", "宁波银行", 9, "百万元", "71,969"),
    ("600919", "江苏银行", 17, "千元", "87,942,367"),
    ("601009", "南京银行", 16, "千元", "55,541,916"),
    ("600926", "杭州银行", 13, "千元", "38,798,611"),
    ("601838", "成都银行", 15, "千元", "23,602,625"),
    ("002966", "苏州银行", 12, "千元", "12,355,561"),
    ("601577", "长沙银行", 12, "千元", "25,470,844"),
    ("002948", "青岛银行", 8, "千元", "14,572,778"),
    ("601187", "厦门银行", 12, "千元", "5,859,815"),
    ("601229", "上海银行", 16, "千元", "54,761,019"),
]

# 2025 year-end NPL rate. Numeric column 2 in Suzhou follows a regulatory-limit
# column; Nanjing has paired period-end / average columns for each year.
NPL = [
    ("600036", 16, 1, "0.94", (5,)),
    ("000001", 17, 1, "1.05", (4,)),
    ("600919", 19, 1, "0.84", (4,)),
    ("601009", 18, 1, "0.83", (7,)),
    ("600926", 15, 1, "0.76", (5,)),
    ("601838", 16, 1, "0.68", (4,)),
    ("002966", 15, 2, "0.82", (5,)),
    ("601577", 13, 1, "1.15", (5,)),
    ("002948", 9, 1, "0.97", (5,)),
    ("601187", 13, 1, "0.77", (5,)),
    ("601229", 18, 1, "1.18", (5,)),
]


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def number(value):
    """Do not remove internal commas or join fragments: each value must be whole."""
    return compact(value).removesuffix("%")


def label(value):
    value = compact(value).lstrip("0123456789-－–—")
    return re.sub(r"\((?:%|\d+)\)$", "", value)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def golden_cases():
    result = []
    for code, bank, page, unit, value in REVENUE:
        result.append({"id": f"revenue-{code}", "code": code, "company": bank,
                       "pdfPage": page, "label": "营业收入", "unit": unit,
                       "cells": {1: value}, "columnCounts": (4, 5) if code == "000001" else (5,)})
    banks = {row[0]: row[1] for row in REVENUE}
    for code, page, column, value, widths in NPL:
        result.append({"id": f"npl-{code}", "code": code, "company": banks[code],
                       "pdfPage": page, "label": "不良贷款率", "unit": "%",
                       "cells": {column: value}, "columnCounts": widths})
    result.extend([
        {"id": "ningbo-corporate-loans", "code": "002142", "pdfPage": 9,
         "label": "公司贷款及垫款本金", "unit": "百万元", "columnCounts": (5,),
         "cells": {1: "1,073,136", 2: "822,628", 3: "30.45", 4: "661,269"}},
        {"id": "cmb-attributable-profit", "code": "600036", "pdfPage": 14,
         "label": "归属于本行股东的净利润", "unit": "百万元", "columnCounts": (5,),
         "cells": {1: "150,181", 2: "148,391", 3: "1.21", 4: "146,602"}},
        {"id": "suzhou-net-profit", "code": "002966", "pdfPage": 12,
         "label": "净利润", "unit": "千元", "columnCounts": (5,),
         "cells": {1: "5,578,439", 2: "5,272,956", 3: "5.79", 4: "4,797,129"}},
        {"id": "suzhou-attributable-profit", "code": "002966", "pdfPage": 12,
         "label": "归属于母公司股东的净利润", "unit": "千元", "columnCounts": (5,),
         "cells": {1: "5,348,303", 2: "5,068,207", 3: "5.53", 4: "4,600,649"}},
        {"id": "jiangsu-net-interest-margin", "code": "600919", "pdfPage": 23,
         "label": "净息差", "unit": "%", "columnCounts": (4,), "cells": {3: "1.73"}},
    ])
    for case in result:
        case.setdefault("company", banks[case["code"]])
        case["reportYear"] = 2025
    check(len(result) == 28, "Expected 28 independent financial table checks")
    return result


def verify_fact(case, page, tables):
    source_text = compact(page.extract_text() or "")
    check("2025" in source_text, "2025 is absent from the physical source page")
    source_words = page.extract_words(x_tolerance=2, y_tolerance=2)
    # Search source text, not extracted table rows. Whitespace allowance covers
    # labels wrapping over two physical lines in the original PDF.
    source_label_pattern = r"\s*".join(re.escape(char) for char in case["label"])
    label_boxes = page.search(source_label_pattern, regex=True) or []
    check(label_boxes, f"Label {case['label']!r} absent from original PDF text")
    for expected in case["cells"].values():
        actual_words = [word for word in source_words if number(word["text"]) == expected]
        same_row = [word for word in actual_words if any(
            box["top"] - 4 <= (word["top"] + word["bottom"]) / 2 <= box["bottom"] + 4
            for box in label_boxes)]
        check(same_row, f"Original labelled PDF row has no complete word {expected!r}")
    found = []
    for table in tables:
        for row_index, row in enumerate(table["rawRows"]):
            if row and label(row[0]) == case["label"]:
                found.append((table, row_index, row))
    check(found, f"Extracted row label {case['label']!r} missing")
    exact = [(table, index, row) for table, index, row in found
             if all(column < len(row) and number(row[column]) == expected
                    for column, expected in case["cells"].items())]
    check(exact, f"Wrong or split values / columns: {[row for _, _, row in found]!r}")
    table, row_index, row = exact[0]
    check(len(row) in case["columnCounts"], f"Wrong column count: {len(row)}")
    check(all(len(other) == len(table["headers"]) for other in table["rawRows"]),
          "Ragged extracted grid / headers")
    preceding_headers = " ".join(str(cell or "") for previous in table["rawRows"][:row_index]
                                 for cell in previous)
    check("2025" in preceding_headers, "The 2025 source header was dropped before this row")
    # Unit can be in the table header or its immediately preceding title line.
    x0, top, x1, bottom = table["bbox"]
    source_context = page.crop((max(page.bbox[0], x0 - 6), max(page.bbox[1], top - 85),
                                min(page.bbox[2], x1 + 6), min(page.bbox[3], bottom + 2)))
    check(compact(case["unit"]) in compact(source_context.extract_text() or ""),
          f"Source table and nearby unit label do not contain {case['unit']!r}")
    return {"method": table["method"], "qualityFlags": table["qualityFlags"],
            "headers": table["headers"], "actualRow": row,
            "bbox": table["bbox"], "originalSourceRowVerified": True}


def verify_layouts(get_page):
    results = []

    def record(name, code, page_no, callback):
        page, tables = get_page(code, page_no)
        try:
            callback(page, tables)
            results.append({"id": name, "code": code, "pdfPage": page_no, "passed": True})
        except AssertionError as exc:
            results.append({"id": name, "code": code, "pdfPage": page_no,
                            "passed": False, "error": str(exc)})

    def quarter_number(page, tables):
        source = page.extract_words(x_tolerance=2, y_tolerance=2)
        check(any(word["text"] == "20,759,566" for word in source), "Source word changed")
        rows = [row for table in tables for row in table["rawRows"] if label(row[0]) == "营业收入"]
        check(any(row == ["营业收入", "22,304,128", "22,560,164", "22,318,509", "20,759,566"]
                  for row in rows), "Grid split the fourth-quarter amount or dropped its column")

    def year_pairs(page, tables):
        expected = ["2025年期末", "2025年平均", "2024年期末", "2024年平均", "2023年期末", "2023年平均"]
        check(any([compact(cell) for cell in table["headers"][1:]] == expected for table in tables),
              "Multi-level year / period-end / average headers lost")
        check(any(table["rawRows"][1][1:] == ["期末", "平均", "期末", "平均", "期末", "平均"]
                  for table in tables), "Original subheader row not preserved")

    def repeated_header(page, tables):
        table = next(table for table in tables if any(label(row[0]) == "不良贷款率" for row in table["rawRows"]))
        before = table["rawRows"][:next(i for i, row in enumerate(table["rawRows"]) if label(row[0]) == "不良贷款率")]
        check(any("2025年12月31日" in compact(row[1]) and "2024年12月31日" in compact(row[2])
                  for row in before), "Mid-table switch to year-end dates was dropped")

    def currency_header(page, tables):
        row_tables = [table for table in tables if any(label(row[0]) == "营业收入" for row in table["rawRows"])]
        check(any("百万元" in compact(table["headers"][0]) for table in row_tables), "Ningbo unit missing from header")

    def prose_only(page, tables):
        check("不良贷款率0.76%" in compact(page.extract_text() or ""), "Original prose fact changed")
        check(not any("不良贷款率" in compact(row[0]) for table in tables for row in table["rawRows"]),
              "Narrative-only NPL ratio incorrectly represented as a financial table")

    record("complete-quarter-number", "600919", 19, quarter_number)
    record("paired-year-subheaders", "601009", 18, year_pairs)
    record("internal-date-header", "000001", 17, repeated_header)
    record("currency-unit-in-header", "002142", 9, currency_header)
    record("narrative-is-not-a-table", "002142", 18, prose_only)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workdir", type=Path, required=True,
                        help="Directory containing CODE-2025.pdf for all 12 banks")
    parser.add_argument("--json", type=Path, help="Optional machine-readable verification result")
    args = parser.parse_args()
    sources = {source["code"]: source for source in json.loads(
        (Path(__file__).resolve().parent.parent / "sources.json").read_text(encoding="utf-8"))}
    cases, pdfs, cache, hashes = golden_cases(), {}, {}, {}
    started = time.perf_counter()
    results = []
    try:
        for code in sorted({case["code"] for case in cases}):
            path = args.workdir / f"{code}-2025.pdf"
            if not path.is_file():
                raise FileNotFoundError(f"Missing required source PDF: {path}")
            hashes[code] = hashlib.sha256(path.read_bytes()).hexdigest()
            pdfs[code] = pdfplumber.open(path)

        def get_page(code, page_no):
            key = (code, page_no)
            if key not in cache:
                page = pdfs[code].pages[page_no - 1]
                cache[key] = (page, extract_tables(page))
            return cache[key]

        for case in cases:
            result = {**case, "sourceUrl": sources[case["code"]]["sourceUrl"],
                      "sourceSha256": hashes[case["code"]]}
            try:
                page, tables = get_page(case["code"], case["pdfPage"])
                result.update(verify_fact(case, page, tables), passed=True)
                print(f"PASS {case['id']} | {case['company']} PDF p{case['pdfPage']} | {case['label']}")
            except (AssertionError, ValueError, IndexError) as exc:
                result.update(passed=False, error=str(exc))
                print(f"FAIL {case['id']}: {exc}")
            results.append(result)
        layouts = verify_layouts(get_page)
        for result in layouts:
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}" +
                  (f": {result['error']}" if not result["passed"] else ""))
        failed = sum(not result["passed"] for result in results + layouts)
        report = {"schemaVersion": 1, "goldSource": "Manual transcription from original official PDF physical pages",
                  "reportYear": 2025, "banks": len(pdfs), "pagesChecked": len(cache),
                  "financialChecks": len(results), "layoutChecks": len(layouts), "failed": failed,
                  "durationSeconds": round(time.perf_counter() - started, 3),
                  "checks": results, "layouts": layouts}
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n{len(results)} financial checks + {len(layouts)} layout checks; "
              f"{len(pdfs)} banks, {len(cache)} physical pages; failures: {failed}")
        return 1 if failed else 0
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Cannot verify: {exc}", file=sys.stderr)
        return 2
    finally:
        for pdf in pdfs.values():
            pdf.close()


if __name__ == "__main__":
    raise SystemExit(main())

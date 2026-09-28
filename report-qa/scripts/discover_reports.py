"""Reproduce the official CNINFO full annual-report source list (no PDF download).

Example, reproducing the assignment's discovery date:
    python scripts/discover_reports.py --year 2025 --as-of 2026-09-28

The stock/orgId mapping and announcement records both come from CNINFO's public
endpoints. An empty keyword is intentional: CNINFO tokenizes "2025" differently
from "2025年年度报告", so combining that keyword with the annual-report category
can silently omit genuine reports. We instead filter complete titles locally.
Only a complete, unique list of the requested companies replaces sources.json.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import html
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STOCKS_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
ANNOUNCEMENTS_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_ORIGIN = "https://static.cninfo.com.cn/"
BEIJING = timezone(timedelta(hours=8))
DEFAULT_CODES = (
    "600036", "000001", "002142", "600919", "601009", "600926",
    "601838", "002966", "601577", "002948", "601187", "601229",
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AnnualReportResearch/1.0)",
    "Referer": "https://www.cninfo.com.cn/",
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def request_json(url: str, fields: dict | None = None):
    body = urlencode(fields).encode("utf-8") if fields is not None else None
    for attempt in range(3):
        try:
            with urlopen(Request(url, data=body, headers=HEADERS), timeout=35) as response:
                content = response.read(12 * 1024 * 1024 + 1)
                if len(content) > 12 * 1024 * 1024:
                    raise ValueError("CNINFO JSON response exceeds the size limit")
                return json.loads(content.decode("utf-8-sig"))
        except (HTTPError, URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def clean_title(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", str(value))).strip()


def is_full_annual_report(title: str, year: int) -> bool:
    compact = re.sub(r"\s+", "", title)
    if re.search(r"摘要|英文|业绩快报|业绩预告|提示性|审计报告|股东|董事|可持续|ESG|English|summary", compact, re.I):
        return False
    return bool(re.search(rf"(?<!\d){year}年?年度报告(?:全文)?(?:[（(][^（）()]*[）)])?$", compact))


def discover_one(stock: dict, year: int, as_of: date) -> dict:
    code = stock["code"]
    start = date(year + 1, 1, 1)
    if as_of < start:
        raise ValueError("The discovery date must follow the report year")
    candidates = {}
    for page in range(1, 11):
        fields = {
            "pageNum": page, "pageSize": 30,
            "column": "sse" if code.startswith("6") else "szse",
            "tabName": "fulltext", "plate": "", "stock": f"{code},{stock['orgId']}",
            "searchkey": "", "secid": "", "category": "category_ndbg_szsh;",
            "trade": "", "seDate": f"{start.isoformat()}~{as_of.isoformat()}",
            "sortName": "time", "sortType": "desc", "isHLtitle": "false",
        }
        response = request_json(ANNOUNCEMENTS_URL, fields)
        for item in response.get("announcements") or []:
            title = clean_title(item.get("announcementTitle", ""))
            if item.get("secCode") != code or not is_full_annual_report(title, year):
                continue
            path = str(item.get("adjunctUrl", ""))
            match = re.fullmatch(r"finalpage/(\d{4}-\d{2}-\d{2})/(\d+)\.PDF", path, re.I)
            if not match or str(item.get("adjunctType", "")).upper() != "PDF":
                continue
            published = datetime.fromtimestamp(int(item["announcementTime"]) / 1000, BEIJING).date()
            if not start <= published <= as_of:
                continue
            candidates[path] = {
                "company": stock["zwjc"], "code": code, "reportYear": year,
                "title": title, "sourceUrl": STATIC_ORIGIN + path,
                "publicationDate": published.isoformat(),
            }
        if not response.get("hasMore"):
            break
    else:
        raise RuntimeError(f"{code}: announcement pagination exceeded ten pages")
    if not candidates:
        raise RuntimeError(f"{code} {stock['zwjc']}: no official complete {year} annual report found")
    # A later complete amended edition supersedes the original report. Summary,
    # English, and supplementary-notice records never enter this candidate set.
    selected = max(candidates.values(), key=lambda row: (row["publicationDate"], row["sourceUrl"]))
    if len(candidates) > 1:
        print(f"{code}: selected latest full edition from {len(candidates)} candidates", file=sys.stderr)
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(BEIJING).date())
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "sources.json")
    args = parser.parse_args(argv)
    if len(set(args.codes)) != len(args.codes) or any(not re.fullmatch(r"\d{6}", code) for code in args.codes):
        parser.error("Stock codes must be unique six-digit strings")
    if len(args.codes) < 11:
        parser.error("This assignment requires more than ten companies")
    catalog = request_json(STOCKS_URL)
    stocks = {item["code"]: item for item in catalog.get("stockList", []) if item.get("category") == "A股"}
    missing = [code for code in args.codes if code not in stocks]
    if missing:
        raise RuntimeError(f"No official security metadata for: {', '.join(missing)}")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(discover_one, stocks[code], args.year, args.as_of) for code in args.codes]
        sources = [future.result() for future in futures]
    if len({source["sourceUrl"] for source in sources}) != len(sources):
        raise RuntimeError("Different companies unexpectedly resolved to one document")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # All network requests and validations complete before touching the manifest.
    args.output.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for source in sources:
        print(f"{source['code']} {source['company']} | {source['publicationDate']} | {source['sourceUrl']}")
    print(f"Saved {len(sources)} full official annual-report records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

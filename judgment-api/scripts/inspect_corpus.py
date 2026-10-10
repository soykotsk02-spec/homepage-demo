"""Read private local judgments and write ONLY anonymous aggregate diagnostics."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from corpus import load_corpus


def inspect(source: Path):
    documents = load_corpus(source)
    count = len(documents)
    fields = {name: sum(bool(d[name]) for d in documents) for name in ("pre_reasoning", "cause", "laws", "direction", "verdict")}
    warnings = Counter(w for d in documents for w in d["extraction"]["warnings"])
    return {
        "schemaVersion": 1, "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(), "recordCount": count,
        "lawCitationCount": sum(len(d["laws"]) for d in documents),
        "distinctLawCitationCount": len({law for d in documents for law in d["laws"]}),
        "completeLabelRecordCount": sum(all(d[k] for k in ("cause", "laws", "direction", "verdict")) for d in documents),
        "fieldPresence": {name: {"count": value, "rate": round(value / count, 6) if count else 0} for name, value in fields.items()},
        "causeCounts": dict(Counter(d["cause"] or "未识别" for d in documents)),
        "directionCounts": dict(Counter(d["direction"] or "未识别" for d in documents)),
        "causeMethodCounts": dict(Counter(d["extraction"]["causeMethod"] for d in documents)),
        "warningCounts": dict(warnings),
        "lengths": {name: {"min": min((len(d[name]) for d in documents), default=0), "median": statistics.median([len(d[name]) for d in documents]) if documents else 0, "max": max((len(d[name]) for d in documents), default=0)} for name in ("pre_reasoning", "verdict", "laws")},
        "limitations": ["字段非空率是抽取覆盖，不是人工准确率。", "仅识别明文出现的受控案由及明确别名，未根据事实推断缺失案由。", "方向按裁判主文形式判断，不衡量支持金额比例；反诉或上诉可能有多个方向。", "引用法条反映历史文书，不代表现行有效法；不完整法名不补造。", "仅保存聚合统计，无原文、当事人、证件、案号、记录ID或原文件路径。"],
        "manualSampleReview": {"status": "pending", "sampleCount": 0, "note": "No manual accuracy claim until local sample review is completed."},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/corpus-summary.json")
    args = parser.parse_args()
    summary = inspect(args.source)
    # An existing local review receipt is reusable only for identical source and
    # extractor bytes. Fresh/changed inputs never inherit a manual accuracy claim.
    if args.output.is_file():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        receipt = previous.get("manualSampleReview", {})
        extractor_hash = hashlib.sha256((Path(__file__).resolve().parents[1] / "corpus.py").read_bytes()).hexdigest()
        if previous.get("sourceSha256") == summary["sourceSha256"] and receipt.get("extractorSha256") == extractor_hash:
            summary["manualSampleReview"] = receipt
            for key in ("validation", "lawTitleValidation"):
                if key in previous:
                    summary[key] = previous[key]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"recordCount": summary["recordCount"], "fieldPresence": summary["fieldPresence"], "warningCounts": summary["warningCounts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Reproducible local holdout; extracted labels are silver labels, not human gold.

No case text or party identity is printed. The holdout is excluded before both
TF-IDF fitting and classifier training, together with detected near duplicates.
This measures labels/citations, not executable-disposition correctness.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".deps"), str(ROOT)]
from corpus import load_corpus
from engine import JudgmentEngine, _near_duplicate


def evaluate(corpus_path, holdout_ids=None):
    records = load_corpus(corpus_path)
    available = [r for r in records if all(r.get(k) for k in ("cause", "direction", "laws", "pre_reasoning", "verdict"))
                 and not r.get("extraction", {}).get("warnings")]
    rng = random.Random(20261010)
    rng.shuffle(available)
    selected = []
    for label in ("支持", "部分支持", "驳回"):
        candidates = [r for r in available if r["direction"] == label]
        causes = Counter()
        for row in candidates:
            if causes[row["cause"]] >= 2 or any(_near_duplicate(row["pre_reasoning"], r["pre_reasoning"]) for r in selected):
                continue
            selected.append(row)
            causes[row["cause"]] += 1
            if sum(r["direction"] == label for r in selected) == 6:
                break
    if holdout_ids is not None:
        by_id = {row["id"]: row for row in records}
        selected = [by_id[row_id] for row_id in holdout_ids]
        if not all(all(row.get(key) for key in ("cause", "direction", "laws", "pre_reasoning", "verdict")) for row in selected):
            raise RuntimeError("A frozen holdout record no longer has complete extracted labels")
    if len(selected) < 10:
        raise RuntimeError("Insufficient independent holdout cases")
    selected_ids = {r["id"] for r in selected}
    engine = JudgmentEngine(corpus_path, exclude_ids=selected_ids, exclude_texts=[r["pre_reasoning"] for r in selected])
    train_ids = {r["id"] for r in engine.documents}
    assert not train_ids & selected_ids
    results = []
    for row in selected:
        try:
            prediction = engine.judge(row["pre_reasoning"])
            dbg = engine.last_debug
            assert not selected_ids & {r["id"] for r in dbg["neighbors"]}
            gold_laws, pred_laws = set(row["laws"]), set(prediction["laws"])
            results.append({
                "id": row["id"], "goldCause": row["cause"], "predictedCause": prediction["cause"],
                "goldDirection": row["direction"], "knnDirection": dbg["neighborDirection"],
                "classifierDirection": dbg["classifierDirection"], "finalDirection": prediction["direction"],
                "causeMatch": prediction["cause"] == row["cause"],
                "knnDirectionMatch": dbg["neighborDirection"] == row["direction"],
                "classifierDirectionMatch": dbg["classifierDirection"] == row["direction"],
                "finalDirectionMatch": prediction["direction"] == row["direction"],
                "lawPrecision": len(gold_laws & pred_laws) / len(pred_laws),
                "lawRecall": len(gold_laws & pred_laws) / len(gold_laws),
                "explicitDispositionLimitation": "无法据此形成可执行" in prediction["verdict"] or "不作补写" in prediction["verdict"],
                "neighborIds": [r["id"] for r in dbg["neighbors"]], "lawSourceIds": dbg["lawSourceIds"],
                "ruleReasons": dbg["ruleReasons"], "seconds": dbg["seconds"],
            })
        except Exception as error:
            results.append({"id": row["id"], "error": type(error).__name__, "message": str(error)})
    count = len(results)
    metrics = {name: round(sum(r.get(name, 0) for r in results) / count, 4) for name in
               ("causeMatch", "knnDirectionMatch", "classifierDirectionMatch", "finalDirectionMatch", "lawPrecision", "lawRecall", "explicitDispositionLimitation")}
    eligible = [r for r in records if all(r.get(k) for k in ("cause", "pre_reasoning", "laws", "verdict")) and r.get("direction") in {"支持", "部分支持", "驳回"}]
    return {"method": "fixed-seed stratified holdout; six per direction where available, at most two per cause per direction",
            "seed": 20261010, "holdoutCount": count, "holdoutIds": sorted(selected_ids),
            "holdoutSelection": "frozen ids from previous evaluation" if holdout_ids is not None else "seeded stratified selection",
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(), "engineSha256": hashlib.sha256((ROOT / "engine.py").read_bytes()).hexdigest(),
            "training": engine.stats, "excludedNearDuplicateCount": len(eligible) - len(engine.documents) - len(selected_ids),
            "metrics": metrics, "results": results,
            "limitations": ["Ground truth is extracted from local judgments, not manually annotated gold.",
                            "Balanced small holdout is not a production accuracy estimate.",
                            "Cause appears in some source introductions; cause match can be optimistic for unlabeled new narratives.",
                            "Direction and law overlap do not measure complete or legally correct dispositions.",
                            "Corpus citations are historical; current legal effect was not externally verified."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reuse-holdout", type=Path)
    args = parser.parse_args()
    previous = json.loads(args.reuse_holdout.read_text(encoding="utf-8")) if args.reuse_holdout else None
    result = evaluate(args.corpus, previous["holdoutIds"] if previous else None)
    if previous:
        result["previousVersion"] = {"engineSha256": previous.get("engineSha256"), "metrics": previous.get("metrics"),
                                     "note": "Final narrow delivery/established performance-defense fixes; same held-out ids, no label selection or hyperparameter tuning."}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)

"""Local-only, auditable extraction of judgment labels. Standard library only.

No network calls, model service, persistence, or missing-label imputation. Law
citations describe what the historic judgment cited, not current applicable law.
Direction describes the formal disposition, not how much of a claimed amount
was awarded. Consumers must inspect extraction warnings and nullable labels.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

CAUSES = tuple(sorted({
    "物业服务合同纠纷", "买卖合同纠纷", "民间借贷纠纷", "劳动争议", "劳动合同纠纷",
    "机动车交通事故责任纠纷", "提供劳务者受害责任纠纷", "金融借款合同纠纷", "离婚纠纷",
    "借款合同纠纷", "劳务合同纠纷", "追索劳动报酬纠纷", "经济补偿金纠纷", "工伤保险待遇纠纷",
    "确认劳动关系纠纷", "社会保险纠纷", "劳动争议纠纷", "人事争议", "信用卡纠纷",
    "房屋租赁合同纠纷", "租赁合同纠纷", "融资租赁合同纠纷", "建设工程施工合同纠纷",
    "建设工程分包合同纠纷", "建设工程合同纠纷", "承揽合同纠纷", "运输合同纠纷",
    "服务合同纠纷", "委托合同纠纷", "中介合同纠纷", "居间合同纠纷", "担保合同纠纷",
    "保证合同纠纷", "抵押合同纠纷", "质押合同纠纷", "借用合同纠纷", "储蓄存款合同纠纷",
    "保险合同纠纷", "财产保险合同纠纷", "人身保险合同纠纷", "责任保险合同纠纷",
    "商品房销售合同纠纷", "商品房预售合同纠纷", "房屋买卖合同纠纷", "合同纠纷",
    "股权转让纠纷", "合伙协议纠纷", "追偿权纠纷", "债权转让合同纠纷", "债务转移合同纠纷",
    "债权人代位权纠纷", "债权人撤销权纠纷", "债权债务概括转移纠纷",
    "不当得利纠纷", "无因管理纠纷", "侵权责任纠纷", "生命权、身体权、健康权纠纷",
    "生命权、健康权、身体权纠纷", "健康权纠纷", "身体权纠纷", "生命权纠纷",
    "提供劳务者致害责任纠纷", "义务帮工人受害责任纠纷", "义务帮工人致害责任纠纷",
    "触电人身损害责任纠纷", "雇员受害赔偿纠纷", "雇员受害责任纠纷", "人身损害赔偿纠纷",
    "道路交通事故人身损害赔偿纠纷", "道路交通事故损害赔偿纠纷", "交通事故责任纠纷",
    "婚约财产纠纷", "同居关系析产纠纷", "同居关系子女抚养纠纷", "离婚后财产纠纷",
    "抚养费纠纷", "抚养关系纠纷", "赡养费纠纷", "赡养纠纷", "继承纠纷",
    "财产损害赔偿纠纷", "排除妨害纠纷", "返还原物纠纷", "所有权确认纠纷",
    "相邻关系纠纷", "宅基地使用权纠纷", "土地承包经营权纠纷", "农村土地承包合同纠纷",
    "物业管理合同纠纷", "物业服务纠纷", "提供劳务受害责任纠纷", "劳动合同争议纠纷",
}, key=lambda value: (-len(value), value)))

# Narrow spelling/name normalization; no guessing from a party's profession or
# an isolated "loan"/"bank" word. Historic cause names otherwise remain intact.
CAUSE_ALIASES = {
    "劳动争议纠纷": "劳动争议", "劳动合同争议纠纷": "劳动合同纠纷",
    "物业管理合同纠纷": "物业服务合同纠纷", "物业服务纠纷": "物业服务合同纠纷",
    "提供劳务受害责任纠纷": "提供劳务者受害责任纠纷",
    "生命权、健康权、身体权纠纷": "生命权、身体权、健康权纠纷",
}

LAW_ALIASES = {name: "中华人民共和国" + name for name in (
    "民事诉讼法", "刑事诉讼法", "行政诉讼法", "合同法", "民法通则", "民法总则", "民法典",
    "侵权责任法", "担保法", "物权法", "劳动合同法", "劳动法", "婚姻法", "继承法",
    "道路交通安全法", "保险法", "公司法", "商业银行法", "劳动争议调解仲裁法", "刑法",
)}
LAW_ALIASES.update({
    "关于审理人身损害赔偿案件适用法律若干问题的解释": "最高人民法院关于审理人身损害赔偿案件适用法律若干问题的解释",
    "关于审理民间借贷案件适用法律若干问题的规定": "最高人民法院关于审理民间借贷案件适用法律若干问题的规定",
    "关于民事诉讼证据的若干规定": "最高人民法院关于民事诉讼证据的若干规定",
    "最高人民法院关于适用中华人民共和国民事诉讼法的解释": "最高人民法院关于适用〈中华人民共和国民事诉讼法〉的解释",
    "最高人民法院关于适用《中华人民共和国民事诉讼法》的解释": "最高人民法院关于适用〈中华人民共和国民事诉讼法〉的解释",
})
# Verified against local context: this title is an employer's internal rule,
# not a statute or judicial interpretation. Do not turn it into legal authority.
INTERNAL_RULE_TITLES = frozenset({"关于企业管理人员退职规定"})
GENERIC_LAW_TITLES = frozenset({"解释", "条例", "办法", "规定", "实施办法", "实施细则"})
MISSING_IMPLEMENTATION_OBJECT = re.compile(r"(?:[^《》〈〉<>]{2,}(?:省|市|自治区))?实施(?:暂行|试行)?办法")
NUM = r"[零〇一二三四五六七八九十百千万两\d]+"
ARTICLE = re.compile(r"第(" + NUM + r")条(?:之(" + NUM + r"))?")
REASONING = re.compile(r"本\s*院\s*认\s*为")
DECISION = re.compile(r"判\s*决\s*如\s*下\s*[：:]?")
FOOTERS = re.compile(
    r"(?:案件|本案)?(?:受理费|诉讼费用|诉讼费)|申请保全费|财产保全费|"
    r"(?:如果|如|若)?未按(?:照)?(?:本判决|上述判决|判决|本法律文书)|"
    r"如果未按|如不服|如对本判决不服|本判决为终审判决|"
    r"代理审判员|审判长|审判员|书记员|附[：:]?(?:相关|本案适用|法律条文|法律依据)"
)
REJECT = re.compile(r"驳回.{0,120}?(?:诉讼请求|诉请|请求|起诉|上诉)|不(?:予|准予|准许|准).{0,45}?(?:离婚|支持)|不支持.{0,50}(?:请求|诉请)")
OTHER_REJECT = re.compile(r"驳回.{0,100}?(?:其他|其它|其余|剩余|其馀)(?:的)?(?:诉讼)?请求")
AFFIRM = re.compile(r"支付|偿还|赔偿|赔付|归还|返还|给付|付给|清偿|付清|偿付|退还|补偿|交纳|缴纳|补付|解除|确认|准予|准许|准允|撤销|腾退|交付|履行|补发|补缴|办理|协助|消除|停止|恢复|分割|补足|享有|归.{0,20}所有|承担.{0,20}责任|维持.{0,20}判决")


def _cause_matches(text: str):
    """Longer recognized names suppress nested generic labels, preserving offsets."""
    found = []
    occupied = []
    for label in CAUSES:
        for match in re.finditer(re.escape(label), text):
            if label in {"劳动争议", "人事争议"} and text[match.end():match.end() + 8].startswith(("仲裁", "调解仲裁")):
                continue
            if any(start <= match.start() and match.end() <= end for start, end in occupied):
                continue
            occupied.append(match.span())
            found.append((match.start(), match.end(), label))
    return sorted(found)


def extract_cause(pre: str, reasoning: str):
    prefix = re.sub(r"\s+", "", pre)
    reason = re.sub(r"\s+", "", reasoning)
    # A court's explicit statement outranks the narrative's incidental cases.
    explicit = []
    for start, end, label in _cause_matches(reason):
        preceding = reason[max(0, start - 24):start]
        if re.search(r"本案(?:案由)?(?:系|为|属于|属|是|应为|应属)|本案的?案由(?:应为|为|是)", preceding):
            explicit.append((start, label))
    if explicit:
        label = explicit[0][1]
        return CAUSE_ALIASES.get(label, label), "court_explicit", len({x[1] for x in explicit}) > 1
    candidates = _cause_matches(prefix)
    if candidates:
        # The opening case introduction precedes evidence/other lawsuit citations.
        label = candidates[0][2]
        return CAUSE_ALIASES.get(label, label), "opening_recognized_cause", len({x[2] for x in candidates}) > 1
    return None, "unrecognized", False


def _book_titles(text: str):
    """Balanced title marks, including laws whose full title embeds 《another law》."""
    depth = 0; start = 0
    for index, char in enumerate(text):
        if char == "《":
            if depth == 0:
                start = index
            depth += 1
        elif char == "》" and depth:
            depth -= 1
            if depth == 0:
                yield start, index + 1, text[start + 1:index]


def extract_laws(reasoning: str):
    text = re.sub(r"\s+", "", reasoning)
    titles = list(_book_titles(text)); laws = []; unresolved = []
    for index, (start, end, raw) in enumerate(titles):
        name = LAW_ALIASES.get(raw, raw)
        if name in INTERNAL_RULE_TITLES:
            unresolved.append("internal_rule_excluded"); continue
        if name in GENERIC_LAW_TITLES:
            unresolved.append("generic_law_title_excluded"); continue
        if MISSING_IMPLEMENTATION_OBJECT.fullmatch(name):
            unresolved.append("incomplete_implementation_title_excluded"); continue
        # A contract/expert report in book-title marks is not automatically a law.
        if not (name.startswith(("中华人民共和国", "最高人民法院", "最高人民检察院")) or name.endswith(("条例", "办法", "规定", "解释"))):
            continue
        if "关于适用的解释" in name or len(name) > 160:
            unresolved.append("incomplete_law_title"); continue
        next_start = titles[index + 1][0] if index + 1 < len(titles) else len(text)
        tail = text[end:min(next_start, end + 600)]
        # Only adjacent citation lists; do not associate later prose articles.
        adjacent = re.match(r"[（(]?(?:第" + NUM + r"条(?:之" + NUM + r")?(?:第" + NUM + r"[款项])?(?:[、，,及和至与]|第" + NUM + r"[款项]|[（(][^）)]{0,35}[）)])*)+", tail)
        if not adjacent:
            continue
        article_text = adjacent.group(0)
        if "至" in article_text:
            unresolved.append("article_range_endpoints_only")
        for match in ARTICLE.finditer(article_text):
            suffix = "之" + match.group(2) if match.group(2) else ""
            value = f"《{name}》第{match.group(1)}条{suffix}"
            if value not in laws:
                laws.append(value)
    return laws, sorted(set(unresolved))


def extract_verdict(text: str):
    starts = list(DECISION.finditer(text))
    if not starts:
        return "", None, ["missing_decision_marker"], None
    marker = starts[-1]
    raw = text[marker.end():]
    footer = FOOTERS.search(raw)
    cutoff = footer.start() if footer else len(raw)
    if footer:
        # Footer phrases sometimes start after "如果被告..." or a party name.
        # Remove that short sentence prefix, not a preceding substantive award.
        previous_end = max(raw.rfind(char, 0, cutoff) for char in "。；;\n") + 1
        prefix = raw[previous_end:cutoff]
        if (len(prefix) <= 45 and not AFFIRM.search(prefix)) or re.match(r"\s*(?:如果|如|若)", prefix):
            cutoff = previous_end
    verdict = raw[:cutoff]
    verdict = verdict.strip(" \r\n\t：:；;")
    verdict = re.sub(r"(?:[。；;]\s*)?[一二三四五六七八九十\d]+[、.．]\s*$", "", verdict).strip()
    warnings = ["multiple_decision_markers_used_last"] if len(starts) > 1 else []
    if not footer:
        warnings.append("verdict_footer_unrecognized")
    if len(verdict) < 5:
        warnings.append("empty_or_short_verdict")
    classification_text = re.sub(r"\s+", "", verdict)
    clauses = [x.strip() for x in re.split(r"[。；;]+|(?=[一二三四五六七八九十\d]{1,3}、)", classification_text) if x.strip()]
    rejected = []; accepted = []
    for clause in clauses:
        if REJECT.search(clause):
            rejected.append(clause)
        elif AFFIRM.search(clause):
            accepted.append(clause)
    if rejected and accepted:
        direction = "部分支持"
    elif rejected:
        # A separate refusal of divorce is not erased by an "other claims" clause.
        explicit_refusal = any(not OTHER_REJECT.search(clause) or "离婚" in clause for clause in rejected)
        direction = "驳回" if explicit_refusal else None
    elif accepted:
        direction = "支持"
    else:
        direction = None
    if "反诉" in verdict:
        warnings.append("counterclaim_may_have_distinct_direction")
    if "上诉" in verdict or "维持" in verdict:
        warnings.append("appeal_direction_requires_context")
    if re.search(r"(?:存在|建立|形成).{0,8}(?:劳动关系|事实劳动关系)", classification_text) and not re.search(r"支付|给付|赔偿|赔付|补偿|补缴|补发|偿还", classification_text):
        # "确认不存在" may support an employer but reject a worker's request.
        # Determining whose request prevailed requires separate claim analysis.
        direction = None
        warnings.append("declaratory_labor_direction_requires_claim_analysis")
    if direction is None:
        warnings.append("direction_unrecognized")
    return verdict, direction, warnings, marker.start()


def extract_document(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not isinstance(record.get("text"), str):
        raise ValueError("Each record must have string id and text fields")
    text = record["text"].replace("\r\n", "\n").replace("\r", "\n")
    marker = REASONING.search(text)
    warnings = []
    pre = text[:marker.start()].strip() if marker else text.strip()
    verdict, direction, verdict_warnings, verdict_start = extract_verdict(text)
    warnings.extend(verdict_warnings)
    reasoning = text[marker.end():verdict_start] if marker and verdict_start is not None and verdict_start >= marker.end() else ""
    if marker is None:
        warnings.append("missing_reasoning_marker")
    cause, cause_method, ambiguous = extract_cause(pre, reasoning)
    if cause is None:
        warnings.append("cause_unrecognized")
    if ambiguous:
        warnings.append("multiple_cause_mentions")
    laws, law_warnings = extract_laws(reasoning)
    warnings.extend(law_warnings)
    if not laws:
        warnings.append("laws_unrecognized")
    return {"id": record["id"], "pre_reasoning": pre, "cause": cause, "laws": laws,
            "direction": direction, "verdict": verdict,
            "extraction": {"reasoningMarkerFound": marker is not None, "verdictMarkerFound": verdict_start is not None,
                           "causeMethod": cause_method, "directionMethod": "formal_disposition_rule",
                           "warnings": sorted(set(warnings))}}


def load_corpus(path: str | Path) -> list[dict[str, Any]]:
    result = []; ids = set()
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                document = extract_document(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                # Never include a private source line in exceptions or logs.
                raise ValueError(f"Invalid judgment record at line {line_number}") from exc
            if document["id"] in ids:
                raise ValueError(f"Duplicate judgment id at line {line_number}")
            ids.add(document["id"]); result.append(document)
    return result

"""Offline retrieval-and-rules baseline, not a generative language model.

Only a query supplies parties, requested relief and monetary amounts. Training
judgments supply cause/direction labels and verbatim, locally cited statutes.
No requests, model downloads, or external legal material are used here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
import time
from typing import Iterable

DIRECTIONS = {"支持", "部分支持", "驳回"}
LAW = re.compile(r"《[^《》\n]{2,100}》第[零〇一二三四五六七八九十百千万两\d]+条\Z")
MONEY = re.compile(r"(?:人民币\s*)?(?P<n>\d[\d,]*(?:\.\d+)?|[零〇一二两三四五六七八九十百千万亿]+)\s*(?P<u>[亿万千]?元)")
MAIN_WORDS = r"借款本金|贷款本金|本金|物业服务费|物业费|服务费|劳动报酬|工资|货款|租金|工程款|赔偿款|赔偿金|损失|欠款|借款|贷款|垫付款|抚养费|赡养费|货物价款|价款|款项"
AUX_WORDS = r"逾期利息|借期利息|利息|违约金|罚息|滞纳金|资金占用费"
FEE_WORDS = r"诉讼费|案件受理费|保全费|鉴定费|律师费"
LABEL = re.compile(f"{FEE_WORDS}|{AUX_WORDS}|{MAIN_WORDS}")
DEFENCE = re.compile(r"被告(?:辩称|称|答辩|主张)|答辩称")
PROVED = re.compile(r"(?:查明|查证|证实|证明|双方确认|双方认可|原告认可|原告承认|经核实|银行流水显示|转账记录显示)")


def _normalize(text: str, numbers: bool = False) -> str:
    value = re.sub(r"\s+|[^\u3400-\u9fffA-Za-z0-9]", "", text).lower()
    return re.sub(r"\d+", "0", value) if numbers else value


def _before_reasoning(text: str) -> str:
    return re.split(r"本院认为|本庭认为|判决如下|裁定如下", text, maxsplit=1)[0].strip()


def _features(text: str) -> str:
    text = _before_reasoning(text)
    # Long appendices must not dominate the limited character-ngram index.
    return text if len(text) <= 7500 else text[:2500] + text[-5000:]


def _shingles(text: str) -> set[str]:
    text = _normalize(text, numbers=True)
    return {text[i:i + 5] for i in range(0, max(0, len(text) - 4), 2)}


def _near_duplicate(left: str, right: str) -> bool:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) >= 100 and min(len(a), len(b)) / max(len(a), len(b)) >= .6 and (a in b or b in a):
        return True
    if min(len(a), len(b)) < 120 or min(len(a), len(b)) / max(len(a), len(b)) < .7:
        return False
    x, y = _shingles(a), _shingles(b)
    return bool(x and y and len(x & y) / len(x | y) >= .88)


def _chinese_number(text: str) -> Decimal:
    digits = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    small = {"十": 10, "百": 100, "千": 1000}
    number = section = total = 0
    for char in text:
        if char in digits:
            number = digits[char]
        elif char in small:
            section += (number or 1) * small[char]
            number = 0
        elif char in "万亿":
            section += number
            number = 0
            if char == "万":
                total += section * 10000
            else:
                total = (total + section) * 100000000
            section = 0
    return Decimal(total + section + number)


def _amount(match: re.Match) -> Decimal:
    text = match.group("n").replace(",", "")
    value = Decimal(text) if re.fullmatch(r"\d+(?:\.\d+)?", text) else _chinese_number(text)
    return value * {"元": 1, "千元": 1000, "万元": 10000, "亿元": 100000000}[match.group("u")]


def _format_money(value: Decimal) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".") + "元"


@dataclass
class Claim:
    kind: str
    label: str
    amount: Decimal | None = None
    raw: str = ""


def _money_claims(text: str) -> list[Claim]:
    result: list[Claim] = []
    previous_end = 0
    for match in MONEY.finditer(text):
        try:
            value = _amount(match)
        except (InvalidOperation, KeyError):
            continue
        if value <= 0:
            continue
        before = text[max(previous_end, match.start() - 24):match.start()]
        after = text[match.end():match.end() + 12]
        labels = list(LABEL.finditer(before))
        post_label = re.match(r"\s*(?:的)?(" + AUX_WORDS + "|" + MAIN_WORDS + ")", after)
        label = post_label.group(1) if post_label else labels[-1].group() if labels else "款项"
        kind = "fee" if re.fullmatch(FEE_WORDS, label) else "aux" if re.fullmatch(AUX_WORDS, label) else "main"
        result.append(Claim(kind, label, value, match.group()))
        previous_end = match.end()
    return result


def _request_text(text: str) -> str:
    patterns = [
        r"(?:诉讼请求(?:为|是)?\s*[:：]?|请求(?:法院)?(?:依法)?判令|请求判决|诉请\s*[:：]|原告请求)(.+?)(?=被告辩称|被告答辩|被告称|被告主张|经审理查明|本院查明|法院查明|事实与理由|$)",
        r"((?:请求|要求|诉请)[^。；\n]{0,220})",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.S)
        if matches:
            bounded = []
            for value in matches:
                sentences = re.split(r"(?<=[。\n])", value)
                chosen = [sentences[0]]
                for sentence in sentences[1:]:
                    if re.match(r"\s*[一二三四五六七八九十\d]+[、.．）)]", sentence):
                        chosen.append(sentence)
                    else:
                        break
                bounded.append("".join(chosen))
            return "；".join(bounded)[:5000]
    return ""


def _claims(text: str) -> tuple[list[Claim], str]:
    requested = _request_text(text)
    source = requested or text
    claims = [claim for claim in _money_claims(source) if claim.kind != "fee"]
    if not claims and requested and re.search(r"上述|前述|还款|本金|借款|欠款|货款|物业费", requested):
        # Resolving "上述款项" uses only this input's facts, never a neighbor.
        candidates = [claim for claim in _money_claims(text) if claim.kind != "fee"]
        main = [claim for claim in candidates if claim.kind == "main"]
        aux = [claim for claim in candidates if claim.kind == "aux"]
        if main:
            claims.append(max(main, key=lambda claim: claim.amount or Decimal(0)))
        claims.extend(aux[:2])
    unique = []
    seen = set()
    for claim in claims:
        key = claim.kind, claim.label, claim.amount
        if key not in seen:
            seen.add(key)
            unique.append(claim)
    for label in re.findall(AUX_WORDS, source):
        if not any(claim.label == label for claim in unique):
            unique.append(Claim("aux", label))
    return unique, requested


def _factual_segments(text: str) -> list[str]:
    result = []
    for segment in re.split(r"[。；;\n]", text):
        if not segment.strip():
            continue
        # A contested defense is not an established payment or missing proof.
        positive_proof = any(not re.search(r"(?:未|不|无|没有|不能|无法|不足).{0,6}$", segment[max(0, match.start() - 10):match.start()]) for match in PROVED.finditer(segment))
        if DEFENCE.search(segment) and not positive_proof:
            continue
        result.append(segment)
    return result


def _principal_rejected(segments: list[str]) -> str | None:
    for text in [part for segment in segments for part in re.split(r"[，,]", segment)]:
        if re.search(AUX_WORDS, text) and not re.search(r"本金|借款|欠款|交付|基础债权", text):
            continue
        if re.search(r"(?:未|没有)(?:实际)?(?:交付|出借|支付)(?:过)?(?:借款|本金|款项)|(?:借款|本金|款项)(?:实际)?(?:未|没有)(?:交付|出借|到账)", text):
            return "输入事实不能证明借款本金已实际交付"
        if re.search(r"(?:不能|无法|未能|不足以)(?:证明|证实).{0,8}(?:实际交付|已经交付|借款交付|本金交付|款项交付)", text):
            return "输入明确记载现有证据不能证明实际交付"
        if re.search(r"(?:仅|只有).{0,12}(?:口头陈述|口头主张|单方陈述).{0,20}(?:无|未|没有).{0,6}(?:证据|凭证)", text):
            return "输入明确记载请求缺乏证据支持"
        if re.search(r"(?:原告|出借人|主张方).{0,10}(?:无任何证据|没有任何证据|未提供任何证据)", text):
            return "输入明确记载原告未提供支持请求的证据"
        if re.search(r"(?:借款|本金|交付|债权|欠款|合同关系).{0,16}(?:证据不足|无法证明|不能证明|未能证明)", text) or re.search(r"(?:无|没有|未能提供|未提交|无法提供).{0,8}证据.{0,12}(?:证明|证实).{0,10}(?:借款|本金|交付|欠款|债权)", text):
            return "输入明确记载本金或基础债权证据不足"
    return None


def _unperformed_payment_rejected(segments: list[str], cause: str) -> bool:
    """An established concurrent-performance defense, never bare denial."""
    if not re.search(r"买卖|承揽|服务|租赁|建设工程", cause):
        return False
    facts = "；".join(segments)
    incomplete = re.search(r"(?:原告|卖方|出卖人|供货方|承揽人).{0,16}(?:未|没有|尚未).{0,16}(?:交付|交货|交工|完成约定|履行)|(?:交货|交付|约定履行)(?:尚)?未完成", facts)
    payment_not_due = re.search(r"被告.{0,10}(?:有权|可以).{0,25}(?:拒付|拒绝付款|拒绝支付)|付款条件(?:尚)?未成就|付款义务(?:尚)?未到期|依法享有.{0,10}(?:同时履行|先履行)抗辩权", facts)
    return bool(incomplete and payment_not_due)


def _principal_fully_paid(segments: list[str]) -> bool:
    return any(re.search(r"(?:本金|借款|欠款|货款|债务).{0,12}(?:已|已经)(?:全部|全额)(?:归还|返还|偿还|清偿|支付|结清)", text) or
               re.search(r"(?:已|已经)(?:全部|全额)(?:归还|返还|偿还|清偿|结清).{0,8}(?:本金|借款|欠款|债务)", text)
               for text in segments)


def _aux_rejected(label: str, segments: list[str]) -> bool:
    target = re.escape(label)
    for text in segments:
        if label not in text and not ("利息" in label and "利息" in text):
            continue
        if re.search(r"(?:未|没有|并无|无)(?:书面)?约定.{0,8}" + target, text) or re.search(target + r".{0,10}(?:没有约定|未约定|无约定|无依据|缺乏依据|证据不足|不予支持|不支持|应予驳回)", text) or re.search(r"(?:未提供|未提交|无|没有)(?:任何)?(?:证据|凭证).{0,8}(?:证明|证实)?.{0,6}" + target, text):
            # An unagreed borrowing-period interest claim is different from an
            # expressly claimed overdue remedy; do not invent a statutory rate.
            if "利息" in label and "逾期利息" in text and "借期利息" in text and label == "逾期利息":
                continue
            return True
    return False


def _payment_deduction(text: str, requested: str, amount: Decimal) -> Decimal:
    if re.search(r"尚欠|剩余|余欠|未还|余额|余款|剩下", requested):
        return Decimal(0)
    payments = []
    for segment in _factual_segments(text):
        for match in re.finditer(r"(?:已经|已)(?:归还|返还|偿还|还款|支付|清偿|给付)(?:了)?[^，,。；;]{0,12}", segment):
            if re.search(r"未|没有|否认", segment[max(0, match.start() - 5):match.start()]):
                continue
            tail = segment[match.start():match.end() + 35]
            money = MONEY.search(tail)
            if money:
                payments.append(_amount(money))
    # Repetition of the same established payment in claims/facts is not two payments.
    return min(sum(set(payments), Decimal(0)), amount)


def _principal_limit(text: str, amount: Decimal) -> Decimal:
    for segment in _factual_segments(text):
        match = re.search(r"(?:实际交付|实际出借|实际借款|实际到账|实收本金|确认借款本金|确认本金)[^，。；]{0,12}", segment)
        if match:
            money = MONEY.search(segment[match.start():match.end() + 28])
            if money:
                value = _amount(money)
                if 0 < value < amount:
                    return value
    return amount


def _strong_support(segments: list[str]) -> bool:
    """Require a positive admission or concrete performance evidence, not a claim."""
    for text in segments:
        if re.search(r"(?:承认|认可|确认).{0,12}(?:欠款|尚欠|未还|应付|借款属实|借款事实)", text) and not re.search(r"不承认|不认可|未确认|否认", text):
            return True
        has_proof = bool(re.search(r"借条|借据|转账(?:记录|凭证)|银行流水|合同|签收|收货|欠条", text))
        # A defendant's deposit/payment does not establish the claimant's
        # delivery or complete performance of a sale contract.
        has_performance = bool(re.search(r"已经交付|已交付|已交货|已签收|已收货|已实际履行|证明.{0,15}交付|齐全|到期未还|未付款|未支付|未偿还", text))
        negative = bool(re.search(r"未提供|未提交|无证据|没有证据|无法证明|未能证明|不能证明|证据不足|伪造", text))
        if has_proof and has_performance and not negative:
            return True
    return False


class _BM25:
    """Dependency-free fallback over Chinese character bigrams."""
    def __init__(self, documents: list[str]):
        self.postings = defaultdict(list)
        self.lengths = []
        for index, document in enumerate(documents):
            tokens = self.tokens(document)
            self.lengths.append(len(tokens))
            for token, count in Counter(tokens).items():
                self.postings[token].append((index, count))
        self.count = len(documents)
        self.average = max(1, sum(self.lengths) / max(1, self.count))

    @staticmethod
    def tokens(text: str) -> list[str]:
        text = _normalize(text)
        return [text[i:i + 2] for i in range(max(0, len(text) - 1))]

    def scores(self, query: str) -> list[float]:
        result = [0.] * self.count
        for token in set(self.tokens(query)):
            entries = self.postings.get(token, ())
            if not entries:
                continue
            idf = math.log(1 + (self.count - len(entries) + .5) / (len(entries) + .5))
            for index, frequency in entries:
                denom = frequency + 1.5 * (.25 + .75 * self.lengths[index] / self.average)
                result[index] += idf * frequency * 2.5 / denom
        return result


class JudgmentEngine:
    def __init__(self, corpus_path, *, exclude_ids: Iterable[str] = (), exclude_texts: Iterable[str] = ()):
        started = time.monotonic()
        from corpus import load_corpus
        excluded = set(exclude_ids)
        forbidden_texts = list(exclude_texts)
        records = load_corpus(corpus_path)
        self.documents = [record for record in records if record.get("id") not in excluded and record.get("cause") and
                          record.get("pre_reasoning") and record.get("laws") and record.get("verdict") and record.get("direction") in DIRECTIONS and
                          not any(_near_duplicate(record["pre_reasoning"], text) for text in forbidden_texts)]
        if not self.documents:
            raise ValueError("本地语料中没有可用于训练的完整标注案件")
        self.cause_counts = Counter(record["cause"] for record in self.documents)
        self.causes = sorted(self.cause_counts, key=len, reverse=True)
        self.texts = [_features(record["pre_reasoning"]) for record in self.documents]
        self.valid_laws = [[law for law in record.get("laws", []) if isinstance(law, str) and LAW.fullmatch(law)] for record in self.documents]
        if not any(self.valid_laws):
            raise ValueError("本地语料没有格式完整且可溯源的法律条文引用")
        self.vectorizer = self.matrix = self.direction_classifier = None
        self.backend = "bm25-char-bigram"
        try:
            import numpy as np
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=2 if len(self.texts) > 20 else 1,
                                              max_df=1.0, max_features=65000, sublinear_tf=True, dtype=np.float32)
            self.matrix = self.vectorizer.fit_transform(self.texts)
            self.backend = "tfidf-char-2-4"
            if len({record["direction"] for record in self.documents}) > 1:
                from sklearn.svm import LinearSVC
                self.direction_classifier = LinearSVC(C=1.0, class_weight="balanced", random_state=20261010, max_iter=4000)
                self.direction_classifier.fit(self.matrix, [record["direction"] for record in self.documents])
        except ImportError:
            self.bm25 = _BM25(self.texts)
        self.last_debug = {}
        self.stats = {"documents": len(self.documents), "causes": len(self.causes), "backend": self.backend,
                      "initializationSeconds": round(time.monotonic() - started, 3), "excludedIds": len(excluded)}

    def _scores(self, query: str) -> list[float]:
        if self.vectorizer is not None:
            return (self.matrix @ self.vectorizer.transform([query]).T).toarray().ravel().tolist()
        return self.bm25.scores(query)

    def _neighbors(self, text: str) -> tuple[list[tuple[int, float]], list[str]]:
        query = _features(text)
        scores = self._scores(query)
        order = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
        neighbors = []
        excluded = []
        for index in order:
            if scores[index] <= 0:
                break
            if _near_duplicate(query, self.texts[index]):
                excluded.append(str(self.documents[index]["id"]))
                continue
            neighbors.append((index, float(scores[index])))
            if len(neighbors) >= 60:
                break
        if not neighbors:
            raise ValueError("未找到非重复的相关本地案件；请补充争议事实、请求与证据")
        return neighbors, excluded

    def _cause(self, text: str, neighbors: list[tuple[int, float]]) -> str:
        for cause in self.causes:
            if cause in text:
                return cause
        # These are lexical task cues, not invented training labels. A cue is
        # used only when its exact standardized label occurs in this corpus.
        cues = [("民间借贷纠纷", r"借条|出借|借款|偿还本金"), ("物业服务合同纠纷", r"物业费|物业服务"),
                ("买卖合同纠纷", r"货款|买卖合同"), ("租赁合同纠纷", r"租金|租赁合同"),
                ("劳动争议", r"拖欠工资|劳动合同|加班费"), ("离婚纠纷", r"请求离婚|要求离婚|夫妻感情")]
        for label, pattern in cues:
            if label in self.cause_counts and re.search(pattern, text):
                return label
        votes = Counter()
        for rank, (index, score) in enumerate(neighbors[:12]):
            votes[self.documents[index]["cause"]] += score * score / (1 + rank * .12)
        return votes.most_common(1)[0][0]

    def _laws(self, cause: str, neighbors: list[tuple[int, float]]) -> tuple[list[str], list[str]]:
        candidates = [(index, score) for index, score in neighbors if self.documents[index]["cause"] == cause and self.valid_laws[index]]
        if not candidates:
            candidates = [(index, score) for index, score in neighbors if self.valid_laws[index]]
        if not candidates:
            raise ValueError("相关非重复案例未提取到合法法条引用，无法可靠生成法律依据")
        votes = Counter()
        occurrences = Counter()
        for rank, (index, score) in enumerate(candidates[:8]):
            for law in self.valid_laws[index]:
                # Procedure-only boilerplate should not displace relevant
                # substantive citations. Nothing is invented or modernized.
                factor = .4 if "民事诉讼法" in law else 1.
                votes[law] += max(score, .001) * factor / (1 + .15 * rank)
                occurrences[law] += 1
        shared = [law for law, _ in votes.most_common() if occurrences[law] >= 2]
        chosen = (shared or [votes.most_common(1)[0][0]])[:5]
        sources = [str(self.documents[index]["id"]) for index, _ in candidates[:8] if any(law in self.valid_laws[index] for law in chosen)]
        return chosen, sources

    def _vote_direction(self, cause: str, neighbors: list[tuple[int, float]]) -> str:
        votes = Counter()
        same = [(index, score) for index, score in neighbors if self.documents[index]["cause"] == cause]
        for rank, (index, score) in enumerate((same or neighbors)[:9]):
            votes[self.documents[index]["direction"]] += score * score / (1 + rank * .2)
        return votes.most_common(1)[0][0]

    def judge(self, case_text: str) -> dict:
        if not isinstance(case_text, str) or len(case_text.strip()) < 8:
            raise ValueError("请提供至少八个字的案件事实、诉讼请求及证据情况")
        if len(case_text) > 50000:
            raise ValueError("案件输入超过五万字符")
        started = time.monotonic()
        text = _before_reasoning(case_text)
        neighbors, excluded = self._neighbors(text)
        cause = self._cause(text, neighbors)
        laws, law_sources = self._laws(cause, neighbors)
        neighbor_direction = self._vote_direction(cause, neighbors)
        classified = str(self.direction_classifier.predict(self.vectorizer.transform([_features(text)]))[0]) if self.direction_classifier is not None else None
        predicted = classified or neighbor_direction
        direction, verdict, reasons = self._draft(text, cause, predicted)
        result = {"cause": cause, "laws": laws, "direction": direction, "verdict": verdict}
        assert result["cause"] and result["laws"] and direction in DIRECTIONS and verdict
        self.last_debug = {"backend": self.backend, "querySha256": hashlib.sha256(text.encode()).hexdigest(),
                           "neighbors": [{"id": self.documents[index]["id"], "score": round(score, 5)} for index, score in neighbors[:12]],
                           "lawSourceIds": law_sources, "excludedNearDuplicateIds": excluded,
                           "neighborDirection": neighbor_direction, "classifierDirection": classified, "ruleReasons": reasons, "seconds": round(time.monotonic() - started, 4)}
        return result

    @staticmethod
    def _draft(text: str, cause: str, predicted: str) -> tuple[str, str, list[str]]:
        claims, requested = _claims(text)
        segments = _factual_segments(text)
        reject_reason = _principal_rejected(segments)
        if reject_reason:
            return "驳回", "驳回原告的全部诉讼请求。", [reject_reason]
        if _unperformed_payment_rejected(segments, cause):
            return "驳回", "驳回原告的付款诉讼请求。", ["输入已明确原告未完成约定履行且被告依法有权在履行前拒付；不将被告预付款当作原告完成履行"]
        main = [claim for claim in claims if claim.kind == "main"]
        auxiliary = [claim for claim in claims if claim.kind == "aux"]
        supported = []
        rejected = []
        reasons = []
        reduced = False
        principal_paid = _principal_fully_paid(segments)
        for index, claim in enumerate(main):
            value = claim.amount
            if value is None:
                continue
            if index == 0 and ("借贷" in cause or claim.label in {"本金", "借款本金", "贷款本金", "借款", "欠款"}):
                limited = _principal_limit(text, value)
                deduction = limited if principal_paid else _payment_deduction(text, requested, limited)
                if deduction or limited < value:
                    reasons.append("依据本次输入中的实际交付或已还款事实调整本金；不使用邻案金额")
                    reduced = True
                value = max(Decimal(0), limited - deduction)
            if value == 0:
                rejected.append(f"关于{claim.label}的请求")
            else:
                label = "借款本金" if "借贷" in cause and claim.label in {"款项", "借款", "本金", "贷款本金"} else claim.label
                supported.append(f"被告向原告支付{label}人民币{_format_money(value)}。")
        for claim in auxiliary:
            if _aux_rejected(claim.label, segments):
                rejected.append(f"关于{claim.label}的诉讼请求")
                reasons.append(f"输入明确记载{claim.label}无约定、无依据或证据不足")
            elif claim.amount is not None:
                supported.append(f"被告向原告支付{claim.label}人民币{_format_money(claim.amount)}。")
            else:
                # Neither neighbor amounts nor an invented legal rate may fill
                # a missing interest calculation. The limitation stays visible.
                rejected.append(f"关于{claim.label}中未明确计算依据及金额部分的诉讼请求")
                reasons.append(f"输入未提供足以计算{claim.label}的明确金额，本基线不臆定利率或金额")
        request = requested or text
        # Non-monetary relief can coexist with rent or damages. It must not
        # disappear just because monetary claims were also extracted.
        has_request = bool(requested or re.search(r"请求|要求|诉请", text))
        if has_request:
            if "离婚" in cause and "离婚" in request and predicted != "驳回":
                supported.append("准予原告与被告离婚；本输入未明确的财产分割及子女抚养事项，不作具体金额处分。")
            if re.search(r"解除.{0,20}合同", request) and predicted != "驳回":
                supported.append("解除原告与被告就本案争议标的订立的合同。")
            if re.search(r"(?:返还|腾退|交还|退还).{0,12}(?:房屋|房产|租赁物|商铺|门面|店面)", request) and predicted != "驳回":
                supported.append("被告将本案请求中明确的租赁房屋或租赁物返还原告。")
            if re.search(r"确认.{0,12}劳动关系", request) and predicted != "驳回":
                supported.append("确认原告与被告之间存在本案所争议的劳动关系。")
        clear_basis = _strong_support(segments) or principal_paid or reduced
        if predicted == "驳回" and not clear_basis:
            return "驳回", "驳回原告的全部诉讼请求。", reasons + ["输入没有足以覆盖分类结论的明确履行或承认证据；本地训练方向为驳回"]
        if predicted == "部分支持" and supported and not rejected and not clear_basis:
            # A classifier cannot determine a partial award. Do not transform
            # the claimed amount into an award or fabricate a reduction ratio.
            return "部分支持", "本地基线预测本案为部分支持。现有输入不能确定获支持的具体请求、范围或金额，无法据此形成可执行的判决主文；须补充事实认定与证据后确定。", ["方向分类为部分支持，但无可据以确定差额的输入事实；不把请求金额直接当作裁判金额"]
        if not supported:
            if rejected or predicted == "驳回":
                return "驳回", "驳回原告的全部诉讼请求。", reasons or ["相关本地案例方向预测为驳回；输入不足以形成具体给付条款"]
            if predicted == "支持":
                return "支持", "本地基线预测本案为支持，但输入未明确具体给付对象、标的或金额，无法据此形成可执行的判决主文，缺失事实不作补写。", ["无可安全确定的具体请求内容；明确保留主文生成失败"]
            return "部分支持", "本地基线预测本案为部分支持，但输入未明确获支持的请求范围或金额，无法据此形成可执行的判决主文，缺失事实不作补写。", ["方向仅作回退；不臆定部分支持比例或金额，明确保留主文生成失败"]
        if reduced:
            rejected.append("超过上述已确认金额部分的诉讼请求")
        direction = "部分支持" if rejected else "支持"
        lines = supported + (["驳回原告" + "、".join(dict.fromkeys(rejected)) + "。"] if rejected else [])
        numerals = "一二三四五六七八九十"
        verdict = "\n".join(f"{numerals[index] if index < len(numerals) else index + 1}、{line}" for index, line in enumerate(lines))
        return direction, verdict, reasons or ["主文仅由本次输入的请求种类和金额构造；未复制相似案主文"]

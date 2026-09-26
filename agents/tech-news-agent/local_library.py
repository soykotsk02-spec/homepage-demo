"""Read an explicit DOCX research allowlist afresh and retain paragraph evidence."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import stat
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


MAX_DOCX_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PARAGRAPHS_PER_SOURCE = 12
MAX_CHARS_PER_SOURCE = 5000
MIN_CHUNK_CHARS = 80
MAX_CHUNK_CHARS = 1200
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TOPICS = {
    "人工智能": 5, "商业模式": 5, "市场结构": 4, "网络外部性": 4,
    "网络效应": 4, "平台": 3, "云": 2, "微软": 4, "azure": 4,
    "openai": 4, "ai": 3, "算力": 3, "模型": 2, "开发者": 3,
    "生态": 3, "订阅": 3, "收入": 2, "定价": 3, "竞争": 2,
    "成本": 2, "统计": 2, "数据": 1, "训练": 2, "推理": 2,
    "技术": 1, "产品": 1, "研发": 2, "api": 2, "合作": 1,
    "市场": 1, "消费者": 1, "用户": 1, "数字化": 2,
}
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was",
    "were", "has", "have", "had", "its", "into", "will", "can", "not",
    "but", "our", "you", "your", "their", "than", "how", "why", "what",
    "new", "said", "says", "more", "about", "through", "which", "over",
    "after", "before", "com", "https", "http", "www", "news", "blog",
}
_PRIVATE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|(?<!\d)(?:\+?86[\s-]?)?1[3-9](?:[\s-]?\d){9}(?!\d)"
    r"|(?<!\d)0\d{2,3}[- ]?\d{7,8}(?!\d)"
    r"|(?<!\w)\d{17}[\dXx](?!\w)"
    r"|(?:身份证|证件号码|护照号码|学生姓名|作者姓名|姓名|学号|联系电话|手机号码|出生日期|家庭住址|联系地址|指导教师|指导老师)\s*[:：=]"
    r"|(?:^|\s)(?:作者|学生|导师|班级|院系)\s*[:：]"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
    r"|\\\\[^\s\\/]+[\\/][^\s]+"
    r"|/(?:Users|home|mnt|tmp|var|private)/[^\s]+"
    r"|\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|secret[_-]?key)\s*[:=]\s*\S+"
    r"|(?:密码|口令|密钥|令牌|授权码)\s*[:：=]\s*\S+"
    r"|\bsk-[A-Za-z0-9_-]{12,}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bya29\.[A-Za-z0-9_-]{16,}\b",
    re.IGNORECASE,
)


class LocalContextError(RuntimeError):
    """No usable, freshly read local research context is available."""


class _SourceError(RuntimeError):
    pass


def _safe_title(title: str, asset_id: str) -> str:
    title = " ".join(title.split())[:200]
    return f"本地研究材料 {asset_id}" if not title or _PRIVATE.search(title) else title


def _read_docx(path: Path) -> tuple[str, list[str]]:
    """Read only word/document.xml; never follow document relationships."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise _SourceError("源文档缺失，本次未读取。") from None
    except OSError:
        raise _SourceError("无法取得源文档信息。") from None
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
        raise _SourceError("源文档是链接，已跳过。")
    if not stat.S_ISREG(info.st_mode) or path.suffix.lower() != ".docx":
        raise _SourceError("只支持清单中明确列出的普通 DOCX 文件。")
    if info.st_size > MAX_DOCX_BYTES:
        raise _SourceError("DOCX 文件超过读取大小限制。")
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_DOCX_BYTES + 1)
        if len(raw) > MAX_DOCX_BYTES:
            raise _SourceError("DOCX 文件超过读取大小限制。")
        digest = sha256(raw).hexdigest()
        with ZipFile(BytesIO(raw)) as archive:
            entries = [entry for entry in archive.infolist() if entry.filename == "word/document.xml"]
            if len(entries) != 1:
                raise _SourceError("DOCX 正文 XML 缺失或重复。")
            entry = entries[0]
            if entry.file_size > MAX_XML_BYTES or entry.flag_bits & 1:
                raise _SourceError("正文 XML 超过大小限制或已加密。")
            with archive.open(entry) as handle:
                xml = handle.read(MAX_XML_BYTES + 1)
        if len(xml) > MAX_XML_BYTES:
            raise _SourceError("正文 XML 超过大小限制。")
        if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", xml.replace(b"\0", b""), re.IGNORECASE):
            raise _SourceError("正文含 DTD 或实体声明，已拒绝读取。")
        root = ET.fromstring(xml)
        body = root.find(_W + "body")
        if root.tag != _W + "document" or body is None:
            raise _SourceError("DOCX 缺少有效正文结构。")
        paragraphs = []
        for paragraph in body.iter(_W + "p"):
            parts = []

            def visit(node: ET.Element) -> None:
                if node is not paragraph and node.tag == _W + "p":
                    return
                if node.tag == _W + "t":
                    parts.append(node.text or "")
                elif node.tag in (_W + "tab", _W + "br", _W + "cr"):
                    parts.append(" ")
                for child in node:
                    visit(child)

            visit(paragraph)
            paragraphs.append(" ".join("".join(parts).split()))
        return digest, paragraphs
    except _SourceError:
        raise
    except (OSError, BadZipFile, ET.ParseError, RuntimeError, ValueError):
        raise _SourceError("源文档无法作为完整 DOCX 正文读取。") from None


def _news_terms(news: dict) -> list[str]:
    texts = []
    for item in news.get("items", []) if isinstance(news, dict) else []:
        if isinstance(item, dict):
            texts.extend(str(item.get(key, "")) for key in ("title", "summary"))
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,31}", " ".join(texts).lower())
    return [word for word, _ in Counter(words).most_common() if word not in _STOP_WORDS][:64]


def _count(text: str, term: str) -> int:
    if term.isascii():
        return len(re.findall(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))
    return text.count(term)


def _score(text: str, news_terms: list[str]) -> int:
    lowered = text.lower()
    return sum(min(3, _count(lowered, term)) * weight for term, weight in _TOPICS.items()) + sum(
        min(2, _count(lowered, term)) * 3 for term in news_terms
    )


def _windows(text: str, maximum: int = MAX_CHUNK_CHARS) -> list[str]:
    """Create nonoverlapping excerpts while keeping their original paragraph ID."""
    windows = []
    while len(text) > maximum:
        boundary = max(text.rfind(mark, maximum // 2, maximum) for mark in "。！？；.!?; ")
        cut = boundary + 1 if boundary >= maximum // 2 else maximum
        windows.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        windows.append(text)
    return windows


def _select(paragraphs: list[str], news_terms: list[str]) -> list[tuple[int, str]]:
    candidates = []
    seen = set()
    for number, text in enumerate(paragraphs, start=1):
        if len(text) < MIN_CHUNK_CHARS or _PRIVATE.search(text) or text in seen:
            continue
        seen.add(text)
        excerpts = [part for part in _windows(text) if len(part) >= MIN_CHUNK_CHARS]
        if not excerpts:
            continue
        best = max(excerpts, key=lambda part: _score(part, news_terms))
        score = _score(best, news_terms)
        if score:
            candidates.append((score, number, best))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
    selected = []
    remaining = MAX_CHARS_PER_SOURCE
    for _, number, text in candidates:
        if len(selected) >= MAX_PARAGRAPHS_PER_SOURCE or remaining < MIN_CHUNK_CHARS:
            break
        if len(text) > remaining:
            text = _windows(text, remaining)[0]
        if len(text) < MIN_CHUNK_CHARS:
            continue
        selected.append((number, text))
        remaining -= len(text)
    return sorted(selected)


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def collect_local_context(manifest_path: Path, run_dir: Path, news: dict) -> dict:
    """Reread every allowed source; never load previously generated context."""
    manifest_path, run_dir = Path(manifest_path), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    context: dict[str, Any] = {"schemaVersion": 1, "assets": [], "chunks": [], "limitations": []}
    evidence: dict[str, Any] = {
        "schemaVersion": 1, "readAtUtc": datetime.now(timezone.utc).isoformat(),
        "freshRead": True, "cacheUsed": False, "sources": [],
    }
    try:
        with manifest_path.open("rb") as handle:
            raw_manifest = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(raw_manifest) > MAX_MANIFEST_BYTES:
            raise ValueError("oversized manifest")
        manifest = json.loads(raw_manifest.decode("utf-8-sig"))
        sources = manifest["sources"]
        if not isinstance(sources, list) or not sources or len(sources) > 64:
            raise ValueError("invalid source list")
        ids = set()
        for source in sources:
            if not isinstance(source, dict) or not all(isinstance(source.get(k), str) and source[k].strip() for k in ("id", "title", "path")):
                raise ValueError("invalid source")
            if not re.fullmatch(r"[\w.-]{1,80}", source["id"]) or source["id"] in ids:
                raise ValueError("invalid source id")
            ids.add(source["id"])
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        context["limitations"].append("本地资料清单缺失、为空或格式无效；没有使用旧资料缓存。")
        evidence["status"] = "manifest_error"
        _write_json(run_dir / "local-context.json", context)
        _write_json(run_dir / "local-evidence.json", evidence)
        raise LocalContextError(context["limitations"][0]) from None

    news_terms = _news_terms(news)
    for source in sources:
        asset_id = source["id"]
        title = _safe_title(source["title"], asset_id)
        path = Path(source["path"]).expanduser()
        if not path.is_absolute():
            path = manifest_path.parent / path
        record: dict[str, Any] = {"assetId": asset_id, "title": title, "path": str(path.absolute())}
        try:
            digest, paragraphs = _read_docx(path)
            record.update(sha256=digest, paragraphCount=len(paragraphs))
            context["assets"].append({"assetId": asset_id, "title": title, "sha256": digest, "paragraphCount": len(paragraphs)})
            selected = _select(paragraphs, news_terms)
            if not selected:
                record["status"] = "no_usable_paragraphs"
                context["limitations"].append(f"{asset_id}：没有符合长度、研究主题及隐私过滤条件的正文段落。")
            else:
                record.update(status="read", selectedParagraphs=[n for n, _ in selected], selectedCharacters=sum(len(t) for _, t in selected))
                for number, text in selected:
                    context["chunks"].append({
                        "chunkId": f"{asset_id}:p{number}", "assetId": asset_id,
                        "title": title, "anchor": f"正文段落 {number}", "text": text,
                    })
        except _SourceError as error:
            record.update(status="unavailable", reason=str(error))
            context["limitations"].append(f"{asset_id}：{error}")
        evidence["sources"].append(record)
    context["limitations"].append("仅读取清单中 DOCX 的主正文；不读取关系链接、批注、页眉页脚或附件。每份最多选 12 个原始段落、共 5000 字符，长段只保留相关节选。")
    context["limitations"].append("这些材料提供研究概念与分析框架，不证明文件作者身份、用户熟练程度或其中结论已获验证。")
    evidence["status"] = "ready" if context["chunks"] else "no_context"
    evidence["chunkCount"] = len(context["chunks"])
    _write_json(run_dir / "local-evidence.json", evidence)
    _write_json(run_dir / "local-context.json", context)
    if not context["chunks"]:
        raise LocalContextError("本次未读取到任何可用本地研究段落；已停止使用泛化背景替代真实资料。")
    return context

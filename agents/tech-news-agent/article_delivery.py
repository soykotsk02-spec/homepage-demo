"""Private, permanent per-recipient article deduplication. Never prompt the model with this index."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class ArticleHistoryError(RuntimeError):
    pass


class DuplicateArticleError(ArticleHistoryError):
    pass


def canonical_url(value):
    if not isinstance(value, str):
        raise ArticleHistoryError("invalid_article_url")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ArticleHistoryError("invalid_article_url")
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in
             {"at_medium", "at_campaign", "fbclid", "gclid", "ref", "ref_src", "spm", "from", "source", "mc_cid", "mc_eid"}]
    return urlunsplit(("https", parsed.hostname.lower().removeprefix("www."), parsed.path.rstrip("/") or "/", urlencode(sorted(pairs)), ""))


def normalized_title(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(c for c in value if unicodedata.category(c)[0] in {"L", "N"})


def fingerprints(item):
    result = {hashlib.sha256(("url:" + canonical_url(item["url"])).encode()).hexdigest()}
    for field in ("title", "originalTitle"):
        title = normalized_title(item.get(field, ""))
        if title:
            result.add(hashlib.sha256(("title:" + title).encode()).hexdigest())
    return result


def _recipient_key(recipient):
    if not isinstance(recipient, str) or not recipient.isascii():
        raise ArticleHistoryError("invalid_recipient")
    normalized = recipient.strip().lower()
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,63}", normalized):
        raise ArticleHistoryError("invalid_recipient")
    return hashlib.sha256(normalized.encode()).hexdigest()


class ArticleLedger:
    """Atomic reservations plus append-only history import. Old delivery files are read-only."""

    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.path = self.root / "article-deliveries.json"

    def _load(self):
        if not self.path.exists():
            return {"schemaVersion": 1, "recipients": {}, "imported": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if value.get("schemaVersion") != 1 or not isinstance(value.get("recipients"), dict) or not isinstance(value.get("imported"), dict):
                raise ValueError()
            return value
        except (OSError, ValueError, AttributeError):
            raise ArticleHistoryError("article_index_invalid_do_not_reset") from None

    def _transaction(self, callback):
        from agent import exclusive_lock, write_json
        with exclusive_lock(self.root / "article-deliveries.lock"):
            value = self._load()
            result = callback(value)
            write_json(self.path, value)
            return result

    def backfill(self, recipient):
        key = _recipient_key(recipient)
        def update(value):
            records = value["recipients"].setdefault(key, {})
            for ledger in sorted((self.root / "deliveries").glob("*.json")):
                try:
                    raw = ledger.read_bytes()
                    entry = json.loads(raw.decode("utf-8-sig"))
                    if _recipient_key(entry.get("recipient", "")) != key or entry.get("status") not in {"sent", "sending", "uncertain"}:
                        continue
                    marker = key + ":" + ledger.name
                    digest = hashlib.sha256(raw).hexdigest()
                    if value["imported"].get(marker) == digest:
                        continue
                    run_id = entry["runId"]
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
                        raise ValueError()
                    folder = self.root / "runs" / run_id
                    report_path = folder / ("public-analysis.json" if (folder / "public-analysis.json").exists() else "analysis.json")
                    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
                    news_path = folder / "news.json"
                    news = json.loads(news_path.read_text(encoding="utf-8-sig")) if news_path.exists() else {"items": []}
                    sources = {canonical_url(item["url"]): item for item in news["items"]}
                    if not report.get("items"):
                        raise ValueError()
                    state = "sent" if entry["status"] == "sent" else "uncertain"
                    for item in report["items"]:
                        source = sources.get(canonical_url(item["url"]), {})
                        combined = {**item, "originalTitle": source.get("title", "")}
                        for fingerprint in fingerprints(combined):
                            previous = records.get(fingerprint)
                            if not previous or state == "sent" or previous.get("runId") == run_id:
                                records[fingerprint] = {"state": state, "runId": run_id, "at": entry.get("sentAtUtc") or entry.get("createdAtUtc")}
                    value["imported"][marker] = digest
                except (OSError, ValueError, KeyError, TypeError):
                    # Malformed unrelated old entries cannot be silently treated as empty history.
                    raise ArticleHistoryError("historical_delivery_unreadable") from None
            return len(records)
        return self._transaction(update)

    def filter(self, recipient, items, run_id):
        key = _recipient_key(recipient)
        def select(value):
            records = value["recipients"].get(key, {})
            selected, seen = [], set()
            for item in items:
                keys = fingerprints(item)
                blocked = any(k in records and (records[k]["state"] != "reserved" or records[k]["runId"] != run_id) for k in keys)
                if not blocked and not seen.intersection(keys):
                    selected.append(item)
                    seen.update(keys)
            return selected
        return self._transaction(select)

    def reserve(self, recipient, items, run_id):
        key = _recipient_key(recipient)
        def update(value):
            records = value["recipients"].setdefault(key, {})
            seen = set()
            for item in items:
                keys = fingerprints(item)
                if seen.intersection(keys):
                    raise DuplicateArticleError("duplicate_article_in_current_report")
                seen.update(keys)
                if any(k in records and (records[k]["state"] != "reserved" or records[k]["runId"] != run_id) for k in keys):
                    raise DuplicateArticleError("article_already_sent_or_reserved")
            for fingerprint in seen:
                records[fingerprint] = {"state": "reserved", "runId": run_id, "at": datetime.now(timezone.utc).isoformat()}
        self._transaction(update)

    def finish(self, recipient, run_id, outcome):
        if outcome not in {"sent", "uncertain", "not_sent"}:
            raise ValueError("invalid_delivery_outcome")
        key = _recipient_key(recipient)
        def update(value):
            records = value["recipients"].get(key, {})
            for fingerprint in list(records):
                record = records[fingerprint]
                if record.get("runId") != run_id or record.get("state") == "sent":
                    continue
                if outcome == "not_sent" and record.get("state") == "reserved":
                    del records[fingerprint]
                elif outcome != "not_sent":
                    record.update(state=outcome, at=datetime.now(timezone.utc).isoformat())
        self._transaction(update)


def selected_source_items(analysis, news):
    """The final send cannot smuggle excluded articles back from cached/model output."""
    by_url = {canonical_url(item["url"]): item for item in news["items"]}
    selected = []
    for item in analysis["items"]:
        source = by_url.get(canonical_url(item["url"]))
        if source is None or (item.get("itemId") and item["itemId"] != source["id"]):
            raise DuplicateArticleError("report_article_not_in_current_allowed_candidates")
        selected.append({**source, "originalTitle": source.get("title", ""), "title": item["title"]})
    return selected


def ensure_recent_articles(items, now=None):
    reference = now or datetime.now(timezone.utc)
    for item in items:
        try:
            published = datetime.fromisoformat(item["publishedAtUtc"].replace("Z", "+00:00"))
            if published.tzinfo is None or not 0 <= (reference - published).total_seconds() <= 72 * 3600:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            from analyzer import InsufficientNewsError
            raise InsufficientNewsError("发送前核验发现新闻不在当前近三天内，停止发送。") from None

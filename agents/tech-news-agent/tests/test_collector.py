"""Offline boundary tests: local fixtures only, never real internet requests."""
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
SOURCE = collector.Source("fixture", "Fixture Source", "https://fixture.invalid/feed")


def rss(items):
    return ("<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>" + items + "</channel></rss>").encode()


def item(title, url, date, summary="A local fixture summary"):
    return f"<item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate><description><![CDATA[{summary}]]></description></item>"


class FakeResponse:
    def __init__(self, body, headers=None):
        self.body = io.BytesIO(body)
        self.headers = headers or {}
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return 200

    def geturl(self):
        return SOURCE.url

    def read(self, size):
        self.read_sizes.append(size)
        return self.body.read(size)


class ParseTests(unittest.TestCase):
    def test_mixed_http_and_https_keeps_valid_https_and_counts_invalid(self):
        fixture = rss(
            item("Insecure URL", "http://example.org/insecure", "Sat, 26 Sep 2026 11:00:00 GMT")
            + item("Secure URL", "https://example.org/secure", "Sat, 26 Sep 2026 10:00:00 GMT")
        )
        news, counts = collector._parse_feed(fixture, SOURCE, NOW, "2026-09-26T12:00:00Z")
        self.assertEqual([entry["url"] for entry in news], ["https://example.org/secure"])
        self.assertEqual(counts["invalidItemExcludedCount"], 1)
        self.assertEqual(counts["selectedItemCount"], 1)

    def test_dates_window_future_unknown_and_url_deduplication(self):
        fixture = rss(
            item("Recent", "https://example.org/a?utm_source=x#section", "Sat, 26 Sep 2026 10:00:00 GMT")
            + item("Same URL older title", "https://example.org/a", "Sat, 26 Sep 2026 09:00:00 GMT")
            + item("Background", "https://example.org/b", "Wed, 23 Sep 2026 12:00:00 GMT")
            + item("Future", "https://example.org/f", "Sat, 26 Sep 2026 12:00:01 GMT")
            + item("Too old", "https://example.org/o", "Fri, 18 Sep 2026 12:00:00 GMT")
            + item("Unknown date", "https://example.org/u", "not a date")
        )
        news, counts = collector._parse_feed(fixture, SOURCE, NOW, "2026-09-26T12:00:00Z")
        self.assertEqual([entry["title"] for entry in news], ["Recent", "Background"])
        self.assertEqual(news[0]["url"], "https://example.org/a")
        self.assertEqual(news[0]["publishedAtUtc"], "2026-09-26T10:00:00Z")
        self.assertEqual(news[0]["freshness"], "fresh")
        self.assertTrue(news[1]["isOutdated"])
        for key in ("futureDatedExcludedCount", "olderThanLookbackExcludedCount", "undatedExcludedCount", "duplicateExcludedCount"):
            self.assertEqual(counts[key], 1, key)

    def test_atom_namespace_timezone_and_plain_text(self):
        fixture = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>A &amp; B</title><link rel="alternate" href="https://example.org/atom"/>
        <published>2026-09-26T18:00:00+08:00</published>
        <summary>&lt;p&gt;Useful&lt;/p&gt;&lt;script&gt;bad()&lt;/script&gt;&lt;b&gt;text&lt;/b&gt;</summary>
        </entry></feed>'''
        news, _ = collector._parse_feed(fixture, SOURCE, NOW, "2026-09-26T12:00:00Z")
        self.assertEqual(news[0]["publishedAtUtc"], "2026-09-26T10:00:00Z")
        self.assertEqual(news[0]["title"], "A & B")
        self.assertEqual(news[0]["summary"], "Useful text")

    def test_dtd_entity_and_utf16_are_rejected(self):
        unsafe = b'<!DOCTYPE rss [<!ENTITY secret SYSTEM "file:///never-read">]><rss><channel/></rss>'
        for fixture in (unsafe, unsafe.decode().encode("utf-16")):
            with self.subTest(fixture=fixture[:20]):
                with self.assertRaises(collector.FeedError):
                    collector._parse_feed(fixture, SOURCE, NOW, "2026-09-26T12:00:00Z")

    def test_cap_and_excerpt_limit(self):
        fixture = rss("".join(item(f"Story {n}", f"https://example.org/{n}", f"Sat, 26 Sep 2026 {n:02}:00:00 GMT", "x" * 2000) for n in range(12)))
        news, _ = collector._parse_feed(fixture, SOURCE, NOW, "2026-09-26T12:00:00Z")
        self.assertEqual(len(news), 8)
        self.assertEqual(news[0]["title"], "Story 11")
        self.assertEqual(len(news[0]["summary"]), 600)


class DownloadLimitTests(unittest.TestCase):
    def test_excessive_content_length_is_rejected_before_body_read(self):
        response = FakeResponse(b"never read", {"Content-Length": "101"})
        with patch.object(collector, "MAX_FEED_BYTES", 100), patch.object(collector, "urlopen", return_value=response):
            with self.assertRaises(collector.FeedError):
                collector._download(SOURCE.url)
        self.assertEqual(response.read_sizes, [])

    def test_unreported_oversize_body_stops_after_cap_plus_one(self):
        response = FakeResponse(b"x" * 1000)
        with patch.object(collector, "MAX_FEED_BYTES", 100), patch.object(collector, "READ_CHUNK_BYTES", 40), patch.object(collector, "urlopen", return_value=response):
            with self.assertRaises(collector.FeedError):
                collector._download(SOURCE.url)
        self.assertEqual(response.body.tell(), 101)
        self.assertTrue(all(0 < size <= 40 for size in response.read_sizes))


class CollectionTests(unittest.TestCase):
    def test_three_fresh_requests_cross_source_dedup_and_audit_files(self):
        fixture = rss(item("Shared story", "https://example.org/shared", "Sat, 26 Sep 2026 10:00:00 GMT"))
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, "_download", return_value=(fixture, 200, SOURCE.url)) as download:
            folder = Path(directory) / "sample-run"
            result = collector.collect(folder, now=NOW)
            self.assertEqual(download.call_count, 3)
            self.assertEqual(result["runId"], "sample-run")
            self.assertEqual(result["successfulSources"], 3)
            self.assertEqual(result["itemCount"], 1)
            self.assertEqual(len(list((folder / "raw").glob("*.xml"))), 3)
            saved = json.loads((folder / "news.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, result)
            self.assertTrue((folder / "source-status.json").exists())
            self.assertTrue(all(status["rawSha256"] for status in result["sourceStatus"]))

    def test_all_failures_overwrite_previous_result_and_raise(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, "_download", side_effect=TimeoutError("fixture timeout")) as download:
            folder = Path(directory)
            (folder / "news.json").write_text('{"status":"ok","items":["old story"]}', encoding="utf-8")
            with self.assertRaises(collector.CollectionError) as caught:
                collector.collect(folder, now=NOW)
            result = json.loads((folder / "news.json").read_text(encoding="utf-8"))
            self.assertEqual(download.call_count, 3)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["items"], [])
            self.assertEqual(result["failedSources"], 3)
            self.assertEqual(caught.exception.result, result)
            self.assertTrue(all(status["status"] == "error" for status in result["sourceStatus"]))

    def test_partial_failure_is_explicit_and_does_not_discard_successes(self):
        fixture = rss(item("One story", "https://example.org/one", "Sat, 26 Sep 2026 10:00:00 GMT"))
        def download(url):
            if "bbci" in url:
                return fixture, 200, url
            raise TimeoutError("fixture timeout")
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, "_download", side_effect=download):
            result = collector.collect(Path(directory), now=NOW)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["partialFailure"])
        self.assertEqual(result["successfulSources"], 1)
        self.assertEqual(result["itemCount"], 1)


if __name__ == "__main__":
    unittest.main()

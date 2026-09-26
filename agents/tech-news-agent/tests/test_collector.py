"""Offline boundary tests: local fixtures only, never real internet requests."""
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
        self.assertFalse(news[1]["isOutdated"])
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
        fixture = rss("".join(item(f"Story {n}", f"https://example.org/{n}", collector._iso(NOW - timedelta(minutes=n)), "x" * 2000) for n in range(25)))
        news, _ = collector._parse_feed(fixture, SOURCE, NOW, "2026-09-26T12:00:00Z")
        self.assertEqual(len(news), 20)
        self.assertEqual(news[0]["title"], "Story 0")
        self.assertEqual(len(news[0]["summary"]), 600)

    def test_exact_72_hour_boundary_is_fresh_but_one_second_older_is_excluded(self):
        fixture = rss(
            item("Just inside", "https://example.org/inside", "2026-09-23T12:00:01Z")
            + item("At cutoff", "https://example.org/cutoff", "2026-09-23T12:00:00Z")
            + item("Too old by a second", "https://example.org/old", "2026-09-23T11:59:59Z")
        )
        news, counts = collector._parse_feed(fixture, SOURCE, NOW, collector._iso(NOW))
        self.assertEqual([entry["title"] for entry in news], ["Just inside", "At cutoff"])
        self.assertTrue(all(entry["freshness"] == "fresh" and not entry["isOutdated"] for entry in news))
        self.assertEqual(news[-1]["ageHours"], 72)
        self.assertEqual(counts["olderThanLookbackExcludedCount"], 1)

    def test_region_hint_is_publisher_metadata_not_event_classification(self):
        source = collector.Source("local-publisher", "Domestic publisher", "https://example.org/rss", "domestic")
        fixture = rss(item("US company releases an AI model", "https://example.org/us-event", "2026-09-26T10:00:00Z"))
        news, _ = collector._parse_feed(fixture, source, NOW, collector._iso(NOW))
        self.assertEqual(news[0]["regionHint"], "domestic")
        self.assertNotIn("region", news[0])


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
    def test_every_source_is_fetched_cross_source_dedup_and_audit_files(self):
        fixture = rss(item("Shared story", "https://example.org/shared", "Sat, 26 Sep 2026 10:00:00 GMT"))
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, "_download", return_value=(fixture, 200, SOURCE.url)) as download:
            folder = Path(directory) / "sample-run"
            result = collector.collect(folder, now=NOW)
            self.assertEqual(download.call_count, len(collector.SOURCES))
            self.assertEqual(result["runId"], "sample-run")
            self.assertEqual(result["successfulSources"], len(collector.SOURCES))
            self.assertEqual(result["itemCount"], 1)
            self.assertEqual(len(list((folder / "raw").glob("*.xml"))), len(collector.SOURCES))
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
            self.assertEqual(download.call_count, len(collector.SOURCES))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["items"], [])
            self.assertEqual(result["failedSources"], len(collector.SOURCES))
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

    def test_fast_domestic_source_cannot_displace_slower_international_sources(self):
        sources = (
            collector.Source("fast-cn", "Fast domestic source", "https://fast.example/feed", "domestic"),
            collector.Source("slow-us", "Slower US source", "https://us.example/feed", "international"),
            collector.Source("slow-uk", "Slower UK source", "https://uk.example/feed", "international"),
        )
        fixtures = {}
        for source_index, source in enumerate(sources):
            fixtures[source.url] = rss("".join(
                item(f"{source.id} story {n}", f"https://example.org/{source.id}/{n}", collector._iso(NOW - timedelta(hours=source_index * 20, minutes=n)))
                for n in range(20)
            ))
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, "SOURCES", sources), patch.object(collector, "TOTAL_LIMIT", 6), patch.object(collector, "_download", side_effect=lambda url: (fixtures[url], 200, url)):
            result = collector.collect(Path(directory), now=NOW)
        self.assertEqual(result["itemCount"], 6)
        self.assertEqual({source.id: sum(entry["sourceId"] == source.id for entry in result["items"]) for source in sources}, {"fast-cn": 2, "slow-us": 2, "slow-uk": 2})
        self.assertEqual([entry["publishedAtUtc"] for entry in result["items"]], sorted([entry["publishedAtUtc"] for entry in result["items"]], reverse=True))
        self.assertTrue(all(status["retainedItemCount"] == 2 for status in result["sourceStatus"]))

    def test_balanced_candidates_skip_duplicate_slots_and_do_not_invent_news(self):
        fixture = rss("".join(item(f"Story {n}", f"https://example.org/{n}", collector._iso(NOW - timedelta(minutes=n))) for n in range(4)))
        entries, _ = collector._parse_feed(fixture, SOURCE, NOW, collector._iso(NOW))
        selected = collector._balanced_candidates([entries[:3], [entries[0], entries[3]]], 80)
        self.assertEqual(len(selected), 4)
        self.assertEqual(len({entry["url"] for entry in selected}), 4)


if __name__ == "__main__":
    unittest.main()

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "analyzer.py"
SPEC = importlib.util.spec_from_file_location("analyzer", MODULE_PATH)
analyzer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyzer)


def batch():
    return {
        "runId": "test-only",
        "generatedAtUtc": "2026-09-26T14:00:00Z",
        "items": [{
            "id": "article-001", "sourceId": "publisher-feed",
            "sourceName": "Test source", "title": "An actual captured headline",
            "url": "https://example.org/story?utm_source=rss",
            "publishedAtUtc": "2026-09-25T14:00:00Z", "summary": "Captured excerpt.",
        }],
    }


def library():
    return {"assets": [{"assetId": "study", "title": "平台研究", "sha256": "abc"}],
            "chunks": [{"chunkId": "study:p7", "assetId": "study", "title": "平台研究",
                        "anchor": "正文段落7", "text": "平台参与者的数量与质量会影响网络效应和商业模式选择。"}],
            "limitations": []}


def profile():
    return {"interests": ["AI"], "localLibrary": library()}


def model_result(item_id="article-001"):
    return {
        "overview": "本次科技新闻概览。",
        "items": [{"itemId": item_id, "title": "中文新闻标题",
                   "summary": "这是一条依据来源摘要整理的中文科技新闻，内容仅用于验证摘要字段的边界和数据映射，不添加未经来源支持的事实，也不声称已经阅读过完整文章。",
                   "whyItMatters": "可作为一次数据分析练习的选题。", "region": "international"}],
        "learningSuggestions": ["花二十分钟整理新闻观察表，记录日期、来源和待验证问题。"],
        "localConnections": [{"itemId": "article-001", "chunkId": "study:p7",
                              "quote": "平台参与者的数量与质量会影响网络效应", "relationship": "这是可供对照的研究观点，新闻并未证明因果关系。",
                              "nextStep": "在平台研究旁补充这一新闻案例，并列出需要进一步验证的问题。"}],
        "localRelevanceNote": "对照提供的平台研究片段。",
    }


def full_batch():
    news = batch()
    news["items"] = [{**news["items"][0], "id": f"article-{index:03d}", "url": f"https://example.org/story-{index}"}
                     for index in range(1, 11)]
    return news


def full_model_result():
    raw = model_result()
    raw["items"] = [{**model_result(f"article-{index:03d}")["items"][0],
                     "region": "domestic" if index % 2 else "international"} for index in range(1, 11)]
    return raw


class NormalizeTests(unittest.TestCase):
    def normalize(self, raw=None, news=None):
        news = batch() if news is None else news
        _, candidates = analyzer._candidates(news)
        return analyzer._normalize(model_result() if raw is None else raw, news, candidates, library())

    def test_source_fields_are_mapped_and_freshness_recomputed(self):
        news = batch()
        news["items"][0]["freshness"] = "stale"
        result = self.normalize(news=news)
        item = result["items"][0]
        self.assertEqual(item["itemId"], "article-001")
        self.assertEqual(item["sourceId"], "publisher-feed")
        self.assertEqual(item["url"], news["items"][0]["url"])
        self.assertEqual(item["source"], "Test source")
        self.assertEqual(item["freshness"], "recent")
        self.assertEqual(item["ageHours"], 24)

    def test_source_group_is_not_an_item_id(self):
        with self.assertRaises(analyzer.AnalysisError):
            self.normalize(model_result("publisher-feed"))

    def test_duplicate_ids_and_more_than_fifteen_rejected(self):
        for count in (2, 16):
            raw = model_result()
            raw["items"] *= count
            with self.assertRaises(analyzer.AnalysisError):
                self.normalize(raw)

    def test_duplicate_urls_even_with_tracking_tags_rejected(self):
        news = batch()
        second = copy.deepcopy(news["items"][0])
        second.update(id="article-002", url="https://example.org/story/?at_medium=RSS#top")
        news["items"].append(second)
        raw = model_result()
        raw["items"].append(model_result("article-002")["items"][0])
        with self.assertRaises(analyzer.AnalysisError):
            self.normalize(raw, news)

    def test_model_cannot_supply_an_arbitrary_url(self):
        raw = model_result()
        raw["items"][0]["url"] = "https://wrong.example/"
        with self.assertRaises(analyzer.AnalysisError):
            self.normalize(raw)

    def test_empty_fields_short_summary_and_extra_suggestion_rejected(self):
        variants = []
        for field in ("itemId", "title", "summary", "whyItMatters"):
            raw = model_result()
            raw["items"][0][field] = "   "
            variants.append(raw)
        raw = model_result()
        raw["items"][0]["summary"] = "太短。"
        variants.append(raw)
        raw = model_result()
        raw["learningSuggestions"].append("额外建议")
        variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(analyzer.AnalysisError):
                self.normalize(raw)

    def test_dates_are_derived_from_capture_clock(self):
        for published, expected in (("2026-09-23T14:00:00Z", "recent"),
                                    ("2026-09-23T13:59:59Z", "stale"),
                                    (None, "undated")):
            news = batch()
            news["items"][0]["publishedAtUtc"] = published
            self.assertEqual(self.normalize(news=news)["items"][0]["freshness"], expected)
        news = batch()
        news["items"][0]["publishedAtUtc"] = "2026-09-27T14:00:00Z"
        with self.assertRaises(analyzer.AnalysisError):
            self.normalize(news=news)

    def test_html_remains_literal_text_and_cannot_become_a_source(self):
        raw = model_result()
        raw["items"][0]["title"] = "标题<script>alert(1)</script>"
        item = self.normalize(raw)["items"][0]
        self.assertEqual(item["title"], raw["items"][0]["title"])
        self.assertEqual(item["url"], batch()["items"][0]["url"])

    def test_local_citation_maps_title_and_exact_paragraph_from_actual_input(self):
        result = self.normalize()
        link = result["localConnections"][0]
        self.assertEqual(link["sourceTitle"], "平台研究")
        self.assertEqual(link["anchor"], "正文段落7")
        self.assertIn(link["quote"], library()["chunks"][0]["text"])
        self.assertEqual(link["newsTitle"], result["items"][0]["title"])

    def test_invented_citation_quote_or_unselected_news_rejected(self):
        for field, value in (("chunkId", "invented:p1"), ("quote", "这是一句资料中根本不存在的虚构引文。"),
                             ("itemId", "not-selected")):
            raw = model_result()
            raw["localConnections"][0][field] = value
            with self.subTest(field=field), self.assertRaises(analyzer.AnalysisError):
                self.normalize(raw)

    def test_ten_to_fifteen_with_domestic_and_international_coverage(self):
        news = batch()
        raw = model_result()
        raw.update(localConnections=[], localRelevanceNote="本次资料没有直接关联。")
        news["items"] = []
        raw["items"] = []
        for number in range(15):
            item_id = f"article-{number}"
            news["items"].append({**batch()["items"][0], "id": item_id, "url": f"https://example.org/story-{number}",
                                  "regionHint": "domestic" if number % 2 else "international"})
            raw["items"].append({**model_result(item_id)["items"][0], "region": "domestic" if number % 2 else "international"})
        output = self.normalize(raw, news)
        self.assertEqual(len(output["items"]), 15)
        self.assertEqual({item["region"] for item in output["items"]}, {"domestic", "international"})
        self.assertIn("本期 15 条", output["sourceNote"])
        for size in (10, 12):
            valid = {**raw, "items": raw["items"][:size]}
            self.assertEqual(len(self.normalize(valid, news)["items"]), size)
        for size in (1, 8, 9):
            with self.subTest(size=size), self.assertRaises(analyzer.AnalysisError):
                self.normalize({**raw, "items": raw["items"][:size]}, news)
        wrong = copy.deepcopy(raw)
        for item in wrong["items"]:
            item["region"] = "international"
        with self.assertRaises(analyzer.AnalysisError):
            self.normalize(wrong, news)

    def test_region_does_not_follow_source_language(self):
        raw = model_result()
        news = batch()
        news["items"][0].update(sourceName="中文科技媒体", regionHint="domestic")
        output = self.normalize(raw, news)
        self.assertEqual(output["items"][0]["region"], "international")

    def test_no_relevant_match_can_be_reported_without_fabricating_citations(self):
        raw = model_result()
        raw.update(localConnections=[], localRelevanceNote="本次新闻与提供的资料没有足够直接联系。")
        self.assertEqual(self.normalize(raw)["localConnections"], [])

    def test_full_local_path_or_email_in_model_output_rejected(self):
        for value in ("读取 " + "Z:" + chr(92) + "synthetic-fixture.docx", "请发往 fake@example.com"):
            raw = model_result()
            raw["localConnections"][0]["nextStep"] = value
            with self.assertRaises(analyzer.AnalysisError):
                self.normalize(raw)


class ProcessTests(unittest.TestCase):
    def test_independent_process_contract_and_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            run_dir = Path(folder)

            def fake_cli(command, prompt, cwd):
                self.assertIn("--ignore-user-config", command)
                self.assertIn("--ephemeral", command)
                self.assertIn("--skip-git-repo-check", command)
                self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
                self.assertEqual(command[-1], "-")
                self.assertNotIn("resume", command)
                self.assertNotIn("--model", command)
                self.assertIn('"itemId": "article-001"', prompt)
                self.assertIn('"sourceId": "publisher-feed"', prompt)
                self.assertIn('"chunkId": "study:p7"', prompt)
                self.assertIn(library()["chunks"][0]["text"], prompt)
                self.assertEqual(cwd, run_dir.resolve())
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps(full_model_result()), encoding="utf-8")

            with patch.object(analyzer, "_run_cli", side_effect=fake_cli):
                result = analyzer.analyze(full_batch(), profile(), run_dir, "codex.exe")
            self.assertEqual(result["selectedItemIds"], [f"article-{index:03d}" for index in range(1, 11)])
            for filename in ("analysis-prompt.txt", "analysis-schema.json", "model-output.json",
                             "analysis.json", "analysis-input-evidence.json"):
                self.assertTrue((run_dir / filename).is_file())
            with self.assertRaises(analyzer.AnalysisError):
                analyzer.analyze(full_batch(), profile(), run_dir, "codex.exe")

    def test_insufficient_or_duplicate_news_stops_before_cli(self):
        variants = [batch(), full_batch(), full_batch()]
        variants[1]["items"] = variants[1]["items"][:9]
        variants[2]["items"][-1]["url"] = variants[2]["items"][0]["url"]
        for news in variants:
            with self.subTest(count=len(news["items"])), tempfile.TemporaryDirectory() as folder, \
                 patch.object(analyzer, "_run_cli") as cli:
                with self.assertRaises(analyzer.InsufficientNewsError):
                    analyzer.analyze(news, profile(), Path(folder), "codex.exe")
                cli.assert_not_called()
                self.assertFalse((Path(folder) / "analysis.json").exists())

    def test_generic_profile_cannot_substitute_for_document_content(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(analyzer, "_run_cli") as cli:
            with self.assertRaises(analyzer.AnalysisError):
                analyzer.analyze(batch(), {"interests": ["AI"]}, Path(folder), "codex.exe")
            cli.assert_not_called()

    def test_failed_cli_cannot_be_replaced_with_previous_result(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(analyzer, "_run_cli", side_effect=analyzer.AnalysisError("CLI failed")):
                with self.assertRaises(analyzer.AnalysisError):
                    analyzer.analyze(full_batch(), profile(), Path(folder), "codex.exe")
            self.assertFalse((Path(folder) / "analysis.json").exists())

    def test_missing_output_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(analyzer, "_run_cli"):
            with self.assertRaises(analyzer.AnalysisError):
                analyzer.analyze(full_batch(), profile(), Path(folder), "codex.exe")

    def test_timeout_terminates_process_tree(self):
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired("codex", 300)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(analyzer.subprocess, "Popen", return_value=process), \
                 patch.object(analyzer, "_stop_process_tree") as stop:
                with self.assertRaises(analyzer.AnalysisError):
                    analyzer._run_cli(["codex.exe", "exec", "-"], "reference data", Path(folder))
            stop.assert_called_once_with(process)
            status = json.loads((Path(folder) / "model-stderr-log.json").read_text())
            self.assertEqual(status["status"], "timeout")

    def test_stderr_is_not_persisted_as_secret_bearing_text(self):
        process = Mock(returncode=1)
        process.communicate.return_value = ("", "SECRET_VALUE_DO_NOT_PERSIST")
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(analyzer.subprocess, "Popen", return_value=process):
                with self.assertRaises(analyzer.AnalysisError):
                    analyzer._run_cli(["codex.exe"], "reference data", Path(folder))
            text = (Path(folder) / "model-stderr-log.json").read_text()
            self.assertNotIn("SECRET_VALUE_DO_NOT_PERSIST", text)
            self.assertFalse(json.loads(text)["rawStderrPersisted"])


if __name__ == "__main__":
    unittest.main()

import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
import analyzer
import collector
import gmail_delivery
import local_library
import public_news
import web_worker


JOB_ID = "21fda210-6381-4a81-9417-519339655a89"
RUN_ID = "20260927T010203000001Z"


def batch(run_id=RUN_ID):
    return {"runId": run_id, "generatedAtUtc": "2026-09-27T01:02:04+00:00", "items": [{
        "id": "public-one", "sourceId": "rss", "sourceName": "公开来源", "title": "Public captured headline",
        "url": "https://example.com/news", "publishedAtUtc": "2026-09-27T00:00:00+00:00", "summary": "Public RSS excerpt."}]}


def result():
    return {"overview": "本次公开科技新闻概览。", "items": [{"itemId": "public-one", "title": "公开新闻标题",
        "summary": "这是一条依据来源摘要整理的中文科技新闻，内容仅用于验证摘要字段的边界和数据映射，不添加未经来源支持的事实，也不声称已经阅读过完整文章。",
        "whyItMatters": "可以用来练习核对新闻来源。", "region": "international"}], "learningSuggestions": ["花二十分钟核对这条公开新闻的来源和日期。"]}


def report(run_id=RUN_ID):
    news = batch(run_id)
    _, candidates = analyzer._candidates(news)
    output = analyzer._normalize({**result(), "localConnections": [], "localRelevanceNote": "公开新闻。"},
                                 news, candidates, {"assets": [], "chunks": []})
    for key in ("localConnections", "localSourcesRead", "localContext"):
        output.pop(key)
    output["audience"] = "public"
    return output


class PublicInputTests(unittest.TestCase):
    def test_one_normalized_mailbox_only(self):
        self.assertEqual(public_news.normalize_recipient(" Reader+News@Example.COM "), "reader+news@example.com")
        invalid = ["a@example.com\r\nBcc:b@example.com", "Name <a@example.com>", "a@example.com,b@example.com",
                   "a@example.com b@example.com", "a@-example.com", "a@example-.com", "a@e..com",
                   ".a@example.com", "a.@example.com", "a..b@example.com", "中@example.com", "a@localhost",
                   "x" * 65 + "@example.com", "a@" + "x" * 64 + ".com", "a@domain.123", None]
        for address in invalid:
            with self.subTest(address=address), self.assertRaises(ValueError):
                public_news.normalize_recipient(address)

    def test_private_recipient_override_rejected_before_config_read(self):
        with patch.object(agent, "read_json") as read, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                agent.main(["run", "--send", "--recipient", "reader@example.com"])
        read.assert_not_called()

    def test_public_worker_command_is_fixed_and_private_job_cannot_gain_recipient(self):
        job = {"id": JOB_ID, "mode": "public-send", "recipient": "Reader@Example.com"}
        command = web_worker.build_command(job, Path("fixture"))
        self.assertEqual(command, [sys.executable, str(Path("fixture") / "agent.py"), "run", "--demo-id", "web-" + JOB_ID,
                                   "--send", "--public-only", "--recipient", "reader@example.com"])
        for bad in ({**job, "recipient": "a@example.com\n--public-only"}, {**job, "profile": "private.json"},
                    {**job, "mode": "send"}, {"id": JOB_ID, "mode": "public-send"}):
            with self.subTest(job=bad), self.assertRaises(web_worker.WorkerError):
                web_worker.build_command(bad)


class PublicAnalysisTests(unittest.TestCase):
    def test_only_captured_public_data_reaches_tool_disabled_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            def cli(command, prompt, cwd):
                self.assertIn("--ignore-user-config", command)
                for capability in ("apps", "remote_plugin", "shell_tool", "browser_use", "computer_use"):
                    self.assertIn(["--disable", capability], [command[i:i + 2] for i in range(len(command) - 1)])
                self.assertIn("Public RSS excerpt.", prompt)
                self.assertNotIn("localLibrary", prompt)
                self.assertNotIn("localProfileSummary", prompt)
                Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(result()), encoding="utf-8")
            with patch.object(public_news, "_run_cli", side_effect=cli):
                output = public_news.analyze_public(batch(), Path(folder), "fixture.exe")
            self.assertEqual(output["audience"], "public")
            self.assertFalse(any(key.startswith("local") for key in output))
            self.assertEqual(output["items"][0]["url"], batch()["items"][0]["url"])
            evidence = json.loads((Path(folder) / "analysis-input-evidence.json").read_text(encoding="utf-8"))
            self.assertFalse(evidence["localFilesRead"])
            self.assertFalse(evidence["personalProfileUsed"])
            body, text = agent.render_report(output, "reader@example.com", public_only=True)
            self.assertNotIn("结合你的本地资料", body + text)

    def test_model_cannot_add_local_fields_and_cache_cannot_be_private(self):
        with tempfile.TemporaryDirectory() as folder:
            def cli(command, *args):
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    json.dumps({**result(), "localConnections": [{"quote": "PRIVATE"}]}), encoding="utf-8")
            with patch.object(public_news, "_run_cli", side_effect=cli), self.assertRaises(analyzer.AnalysisError):
                public_news.analyze_public(batch(), Path(folder), "fixture.exe")
        value = report()
        value["localContext"] = "PRIVATE"
        with self.assertRaises(analyzer.AnalysisError):
            agent.render_report(value, "reader@example.com", public_only=True)


class PublicPipelineTests(unittest.TestCase):
    def test_public_job_does_not_read_local_data_override_config_or_replace_private_latest(self):
        config = {"mail": {"recipient": "owner@example.com", "username": "sender@example.com"}, "codexExecutable": "fixture.exe"}
        original = copy.deepcopy(config)
        mail = Mock()
        mail.send.return_value = {"status": "sent", "acceptedAtUtc": "2026-09-27T01:05:00Z", "recipient": "reader@example.com"}
        args = argparse.Namespace(send=True, demo_id="web-" + JOB_ID, trigger="manual", public_only=True,
                                  recipient="reader@example.com")
        with tempfile.TemporaryDirectory() as folder, patch.object(agent, "ROOT", Path(folder)):
            root = Path(folder)
            (root / "最新简报.html").write_text("PRIVATE LATEST", encoding="utf-8")
            def collect(run):
                # Even an unrelated private artifact must not substitute for public analysis.
                agent.write_json(run / "analysis.json", {"overview": "PRIVATE SECRET"})
                return batch(run.name)
            with patch.object(collector, "collect", side_effect=collect) as capture, \
                 patch.object(local_library, "collect_local_context", side_effect=AssertionError("must never read local data")) as local, \
                 patch.object(analyzer, "analyze", side_effect=AssertionError("must never use private analyzer")) as private, \
                 patch.object(public_news, "analyze_public", side_effect=lambda news, *a: report(news["runId"])) as public, \
                 patch.object(gmail_delivery, "GmailMailer", return_value=mail) as mailer:
                self.assertEqual(agent.run_pipeline(args, config), 0)
                self.assertEqual(agent.run_pipeline(args, config), 0)  # repeated delivery cannot resend
            local.assert_not_called()
            private.assert_not_called()
            public.assert_called_once()
            capture.assert_called_once()
            mail.send.assert_called_once()
            self.assertEqual(config, original)
            self.assertEqual(mailer.call_args.args[0]["mail"]["recipient"], "reader@example.com")
            self.assertEqual((root / "最新简报.html").read_text(encoding="utf-8"), "PRIVATE LATEST")
            html, text = mail.send.call_args.args[:2]
            self.assertNotIn("PRIVATE", html + text)
            self.assertNotIn("本地资料", html + text)
            run = next((root / "data/runs").iterdir())
            status = agent.read_json(run / "status.json")
            self.assertEqual(status["mode"], "public-send")
            self.assertTrue(status["publicOnly"])
            self.assertEqual(status["localReadStatus"], "not_used_public_news")
            self.assertFalse((root / "profile-context.json").exists())
            self.assertFalse((root / "local-sources.json").exists())
            self.assertTrue(agent.find_state(root / "data", "public:demo:web-" + JOB_ID + ":reader@example.com").is_file())
            self.assertFalse(agent.find_state(root / "data", "demo:web-" + JOB_ID + ":reader@example.com").exists())

    def test_public_completion_is_send_and_never_published_as_private_daily(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "worker.json"
            config.write_text(json.dumps({**web_worker.EXAMPLE_CONFIG, "workerToken": "fixture-only-worker-token-with-enough-length"}), encoding="utf-8")
            worker = web_worker.WebWorker(config, root)
            run = root / "data/runs" / RUN_ID
            run.mkdir(parents=True)
            status = {"status": "sent", "sent": True, "publicOnly": True, "mode": "public-send", "audience": "public"}
            agent.write_json(run / "status.json", status)  # no demo ID: explicit public guard still prevents publish
            agent.write_json(run / "public-analysis.json", report())
            agent.write_json(run / "analysis.json", {"overview": "PRIVATE SECRET"})
            job = {"id": JOB_ID, "mode": "public-send", "recipient": "reader@example.com"}
            with patch.object(worker, "_post", return_value={}) as api:
                worker._finish(job, 0, run, status)
                self.assertEqual(job["status"], "completed")
                self.assertTrue(job["updatePayload"]["mailSent"])
                self.assertEqual(job["updatePayload"]["report"]["localConnections"], [])
                self.assertEqual(job["updatePayload"]["report"]["localContext"], "")
                self.assertNotIn("PRIVATE", json.dumps(job["updatePayload"]))
                worker.publish_daily_reports()
            self.assertEqual([call.args[0] for call in api.call_args_list], ["update"])


if __name__ == "__main__":
    unittest.main()

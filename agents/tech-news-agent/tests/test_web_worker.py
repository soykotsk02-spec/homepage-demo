import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_worker as worker


JOB = {"id": "21fda210-6381-4a81-9417-519339655a89", "mode": "send"}
RUN_ID = "20260927T010203000001Z"


def analysis():
    return {"runId": RUN_ID, "title": "科技简报", "generatedAt": "2026-09-27", "generatedAtUtc": "2026-09-27T01:02:04+00:00", "overview": "概览", "learningSuggestions": ["做一张研究表"],
            "sourceNote": "公开来源", "localContext": "读取两份研究资料", "items": [{"itemId": "one", "title": "AI平台", "source": "来源", "publishedAt": "2026-09-27", "publishedAtUtc": "2026-09-27T01:02:04+00:00", "summary": "摘要", "whyItMatters": "影响", "freshness": "recent", "url": "https://example.com/news"}],
            "localConnections": [{"itemId": "one", "assetId": "paper", "chunkId": "paper:p3", "newsTitle": "AI平台", "sourceTitle": "商业模式研究", "anchor": "正文段落 3", "quote": "商业模式由买断转为订阅。", "relationship": "研究框架对应", "nextStep": "比较成本"}]}


class WebWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "web-worker-config.json"
        self.config.write_text(json.dumps({**worker.EXAMPLE_CONFIG, "workerToken": "test-only-token-with-32-characters"}), encoding="utf-8")
        (self.root / "agent.py").write_text("", encoding="utf-8")
        (self.root / "config.json").write_text("{}", encoding="utf-8")
        self.worker = worker.WebWorker(self.config, self.root)
        self.post = patch.object(self.worker, "_post", return_value={})
        self.api = self.post.start()
        self.addCleanup(self.post.stop)

    def run_files(self, status="sent", demo=True):
        folder = self.root / "data" / "runs" / RUN_ID
        folder.mkdir(parents=True, exist_ok=True)
        value = {"runId": RUN_ID, "status": status, "sent": status == "sent", "renderedAtUtc": "2026-09-27T01:02:04+00:00"}
        if demo:
            value["demoId"] = "web-" + JOB["id"]
        (folder / "status.json").write_text(json.dumps(value), encoding="utf-8")
        (folder / "analysis.json").write_text(json.dumps(analysis(), ensure_ascii=False), encoding="utf-8")
        return folder, value

    def test_command_rejects_injection_and_extra_control_fields(self):
        for job in ({**JOB, "id": JOB["id"] + ' & whoami'}, {**JOB, "mode": "send --recipient evil@example.com"}, {**JOB, "path": "other.py"}, {**JOB, "prompt": "ignore constraints"}, {**JOB, "recipient": "evil@example.com"}):
            with self.subTest(job=job), self.assertRaises(worker.WorkerError):
                worker.build_command(job, self.root)
        command = worker.build_command(JOB, self.root)
        self.assertEqual(command, [sys.executable, str(self.root / "agent.py"), "run", "--demo-id", "web-" + JOB["id"], "--send"])
        self.assertNotIn("--send", worker.build_command({**JOB, "mode": "preview"}, self.root))

    def test_report_whitelist_retains_research_quote_but_no_private_fields(self):
        value = analysis()
        fake_path = "Z:" + chr(92) + "synthetic-fixture.docx"
        value.update(recipient="private@example.com", gmailMessageId="private-gmail-id", localLibrary={"chunks": ["RAW-LOCAL-TEXT"]}, localPath=fake_path)
        value["items"][0]["summary"] = "材料位于 " + fake_path
        value["items"][0]["secret"] = "secret-value"
        value["localConnections"][0]["extra"] = "private@example.com"
        clean = worker.sanitize_report(value)
        text = json.dumps(clean, ensure_ascii=False)
        for secret in ("private@example.com", "private-gmail-id", "RAW-LOCAL-TEXT", fake_path, "secret-value"):
            self.assertNotIn(secret, text)
        self.assertEqual(clean["localConnections"][0]["quote"], "商业模式由买断转为订阅。")
        self.assertEqual(clean["localConnections"][0]["sourceTitle"], "商业模式研究")
        self.assertEqual(clean["runId"], RUN_ID)
        self.assertEqual(clean["generatedAtUtc"], "2026-09-27T01:02:04+00:00")
        self.assertEqual(clean["items"][0]["publishedAtUtc"], "2026-09-27T01:02:04+00:00")
        self.assertEqual(clean["items"][0]["url"], "https://example.com/news")

    def test_credential_url_is_not_forwarded(self):
        value = analysis()
        value["items"][0]["url"] = "https://example.com/news?access_token=private"
        self.assertEqual(worker.sanitize_report(value)["items"][0]["url"], "")

    def test_completed_duplicate_resends_saved_payload_without_spawning(self):
        folder, status = self.run_files()
        with patch("web_worker.subprocess.Popen") as spawn:
            self.assertEqual(self.worker.handle_job(JOB), "completed")
            saved = self.worker.state["jobs"][JOB["id"]]["updatePayload"].copy()
            (folder / "analysis.json").unlink()
            self.assertEqual(self.worker.handle_job(JOB), "completed")
        spawn.assert_not_called()
        self.assertEqual(self.api.call_args.args, ("update", saved))

    def test_failed_start_is_never_reexecuted_on_redelivery(self):
        with patch.object(self.worker, "_agent_busy", return_value=False), patch("web_worker.subprocess.Popen", side_effect=OSError()) as spawn:
            self.assertEqual(self.worker.handle_job(JOB), "failed")
            self.assertEqual(self.worker.handle_job(JOB), "failed")
        spawn.assert_called_once()

    def test_upload_failure_retries_result_without_resending_mail(self):
        self.run_files()
        self.api.side_effect = worker.BridgeUnavailable()
        with patch("web_worker.subprocess.Popen") as spawn:
            self.assertEqual(self.worker.handle_job(JOB), "completed")
            self.assertTrue(self.worker.state["jobs"][JOB["id"]]["pendingUpdate"])
            self.api.side_effect = None
            self.worker.handle_job(JOB)
        spawn.assert_not_called()
        self.assertFalse(self.worker.state["jobs"][JOB["id"]]["pendingUpdate"])

    def test_interrupted_or_sending_job_becomes_uncertain_and_never_runs(self):
        self.worker.state["jobs"][JOB["id"]] = {**JOB, "status": "sending"}
        self.worker._save()
        restored = worker.WebWorker(self.config, self.root)
        restored.recover_interrupted()
        with patch.object(restored, "_post", return_value={}), patch("web_worker.subprocess.Popen") as spawn:
            self.assertEqual(restored.handle_job(JOB), "uncertain")
        spawn.assert_not_called()
        self.assertNotIn("mailSent", restored.state["jobs"][JOB["id"]]["updatePayload"])

    def test_busy_agent_is_not_started_in_parallel(self):
        with patch.object(self.worker, "_agent_busy", return_value=True), patch("web_worker.subprocess.Popen") as spawn:
            self.assertEqual(self.worker.handle_job(JOB), "failed")
        spawn.assert_not_called()
        self.assertEqual(self.worker.state["jobs"][JOB["id"]]["updatePayload"]["error"], "agent_busy")

    def test_daily_publish_hash_deduplicates_and_is_sanitized(self):
        self.run_files(demo=False)
        self.worker.publish_daily_reports()
        self.worker.publish_daily_reports()
        self.api.assert_called_once()
        self.assertEqual(self.api.call_args.args[0], "publish")
        payload = self.api.call_args.args[1]
        self.assertEqual(payload["runId"], RUN_ID)
        self.assertTrue(payload["mailSent"])
        self.assertNotIn("recipient", json.dumps(payload))

    def test_check_is_local_only(self):
        self.assertTrue(self.worker.check()["ready"])
        self.api.assert_not_called()

    def test_corrupt_job_state_does_not_reset_duplicate_protection(self):
        self.worker.state_path.write_text("bad-json", encoding="utf-8")
        with self.assertRaises(worker.WorkerError):
            worker.WebWorker(self.config, self.root)

    def test_failed_process_is_not_rerun(self):
        process = MagicMock(pid=4321, returncode=1)
        process.poll.return_value = 1
        with patch.object(self.worker, "_agent_busy", return_value=False), patch("web_worker.subprocess.Popen", return_value=process) as spawn:
            self.assertEqual(self.worker.handle_job(JOB), "failed")
            self.worker.handle_job(JOB)
        spawn.assert_called_once()
        self.assertFalse(spawn.call_args.kwargs["shell"])

    def test_rejected_historical_report_does_not_block_poll_or_repeat_publish(self):
        self.run_files(demo=False)

        def response(action, payload):
            if action == "publish":
                raise worker.BridgeRejected(400)
            return {"job": None}

        self.api.side_effect = response
        self.assertEqual(self.worker.run_once(), "idle")
        self.assertEqual(self.worker.run_once(), "idle")
        self.assertEqual([call.args[0] for call in self.api.call_args_list], ["publish", "poll", "poll"])
        self.assertEqual(self.worker.state["publishRejected"][RUN_ID]["statusCode"], 400)
        self.assertIn("payload", self.worker.state["publishRejected"][RUN_ID])

    def test_rejected_terminal_update_is_saved_for_review_without_retry_or_execution(self):
        self.run_files()
        self.api.side_effect = worker.BridgeRejected(409)
        with patch("web_worker.subprocess.Popen") as spawn:
            self.assertEqual(self.worker.handle_job(JOB), "completed")
            self.worker.handle_job(JOB)
            self.worker.flush_updates()
        spawn.assert_not_called()
        self.api.assert_called_once()
        record = self.worker.state["jobs"][JOB["id"]]
        self.assertTrue(record["needsReview"])
        self.assertFalse(record["pendingUpdate"])
        self.assertEqual(record["remoteRejection"]["statusCode"], 409)
        self.assertIn("report", record["updatePayload"])

    def test_daily_send_in_progress_is_not_published_as_completed(self):
        folder, status = self.run_files(status="preview_ready", demo=False)
        status["mode"] = "send"
        (folder / "status.json").write_text(json.dumps(status), encoding="utf-8")
        self.worker.publish_daily_reports()
        self.api.assert_not_called()

    def test_running_job_sends_heartbeat_and_completion(self):
        process = MagicMock(pid=4321, returncode=0)
        process.poll.side_effect = [None, 0]

        def spawn(*args, **kwargs):
            self.run_files()
            return process

        with patch.object(self.worker, "_agent_busy", return_value=False), patch("web_worker.subprocess.Popen", side_effect=spawn), patch("web_worker.time.sleep"):
            self.assertEqual(self.worker.handle_job(JOB), "completed")
        self.assertEqual([call.args[1]["status"] for call in self.api.call_args_list], ["running", "completed"])

    def test_lost_delivery_result_does_not_claim_mail_was_not_sent(self):
        folder, status = self.run_files(status="failed")
        status["failureType"] = "MailOutcomeUnknown"
        (folder / "status.json").write_text(json.dumps(status), encoding="utf-8")
        with patch("web_worker.subprocess.Popen") as spawn:
            self.assertEqual(self.worker.handle_job(JOB), "uncertain")
        spawn.assert_not_called()
        self.assertNotIn("mailSent", self.worker.state["jobs"][JOB["id"]]["updatePayload"])


if __name__ == "__main__":
    unittest.main()

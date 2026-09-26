"""Outbound-only HTTPS bridge for fixed private or public-only news jobs."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from agent import exclusive_lock
from analyzer import AnalysisError

ROOT = Path(__file__).resolve().parent
JOB_TIMEOUT_SECONDS = 1800
HEARTBEAT_SECONDS = 25
EXAMPLE_CONFIG = {
    "baseUrl": "https://homepage-demo1111.vercel.app/api/agent",
    "workerId": "home-pc",
    "workerToken": "REPLACE_WITH_RANDOM_WORKER_TOKEN",
    "pollSeconds": 30,
}
_RUN_ID = re.compile(r"\d{8}T\d{6,18}Z\Z")
_PHASES = {"collect", "local-read", "analyze", "render", "send", "complete", "failed"}
_SENSITIVE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\s]+"
    r"|/(?:Users|home|mnt|tmp|var|private)/"
    r"|\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|credential)\s*[:=]"
    r"|(?:密码|密钥|令牌|身份证|学号)\s*[:：=]"
    r"|\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|ya29\.[A-Za-z0-9_-]{16,})"
    r"|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\w)\d{17}[\dXx](?!\w)", re.I,
)


class WorkerError(RuntimeError):
    pass


class BridgeUnavailable(WorkerError):
    pass


class BridgeRejected(WorkerError):
    def __init__(self, status_code):
        self.status_code = int(status_code)
        super().__init__("bridge_rejected_" + str(self.status_code))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _json_read(path: Path, maximum=8 * 1024 * 1024):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("json_too_large")
    return json.loads(raw.decode("utf-8-sig"))


def _json_write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_job(value):
    if not isinstance(value, dict):
        raise WorkerError("invalid_job_fields")
    public_only = value.get("mode") == "public-send"
    expected = {"id", "mode", "recipient", "itemCount", "keywords"} if public_only else {"id", "mode"}
    required = {"id", "mode", "recipient"} if public_only else expected
    if not required.issubset(value) or not set(value).issubset(expected):
        raise WorkerError("invalid_job_fields")
    identifier = value["id"]
    if not isinstance(identifier, str) or value["mode"] not in ("preview", "send", "public-send"):
        raise WorkerError("invalid_job")
    try:
        parsed = UUID(identifier)
    except (ValueError, AttributeError):
        raise WorkerError("invalid_job_id") from None
    if str(parsed) != identifier.lower():
        raise WorkerError("invalid_job_id")
    job = {"id": str(parsed), "mode": value["mode"]}
    if public_only:
        from public_news import normalize_recipient, normalize_preferences
        try:
            job["recipient"] = normalize_recipient(value["recipient"])
            job["itemCount"], job["keywords"] = normalize_preferences(value.get("itemCount", 12), value.get("keywords", ""))
        except ValueError:
            raise WorkerError("invalid_recipient") from None
    return job


def build_command(job, root=ROOT):
    job = validate_job(job)
    command = [sys.executable, str(Path(root) / "agent.py"), "run", "--demo-id", "web-" + job["id"]]
    if job["mode"] in {"send", "public-send"}:
        command.append("--send")
    if job["mode"] == "public-send":
        command.extend(["--public-only", "--recipient", job["recipient"], "--item-count", str(job["itemCount"]), "--keywords=" + job["keywords"]])
    return command


def _text(value, maximum=2000):
    if not isinstance(value, str):
        return ""
    if _SENSITIVE.search(value):
        return "（含敏感信息的内容已省略）"
    return "".join(c for c in value if c in "\n\t" or ord(c) >= 32)[:maximum]


def _public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or _SENSITIVE.search(unquote(value)):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        if any(re.search(r"token|password|secret|credential|api.?key|authorization", key, re.I) for key, _ in parse_qsl(parsed.query)):
            return ""
        return value
    except ValueError:
        return ""


def sanitize_report(analysis):
    """Explicit public-to-this-user schema; never copy an arbitrary nested dict."""
    if not isinstance(analysis, dict):
        raise WorkerError("invalid_analysis")
    result = {key: _text(analysis.get(key), 3000 if key == "overview" else 2000)
              for key in ("runId", "title", "generatedAt", "generatedAtUtc", "overview", "sourceNote", "localContext")}
    result["learningSuggestions"] = [_text(value, 2000) for value in analysis.get("learningSuggestions", [])[:3]] if isinstance(analysis.get("learningSuggestions"), list) else []
    result["items"] = []
    for item in analysis.get("items", [])[:15] if isinstance(analysis.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        cleaned = {key: _text(item.get(key), 1800) for key in ("itemId", "title", "source", "publishedAt", "publishedAtUtc", "summary", "whyItMatters", "freshness")}
        cleaned["url"] = _public_url(item.get("url"))
        if item.get("region") in {"domestic", "international"}:
            cleaned["region"] = item["region"]
        result["items"].append(cleaned)
    result["localConnections"] = []
    for item in analysis.get("localConnections", [])[:3] if isinstance(analysis.get("localConnections"), list) else []:
        if not isinstance(item, dict):
            continue
        result["localConnections"].append({
            key: _text(item.get(key), 300 if key == "quote" else 1600)
            for key in ("itemId", "assetId", "chunkId", "newsTitle", "sourceTitle", "anchor", "quote", "relationship", "nextStep")
        })
    if not result["title"] or not result["items"]:
        raise WorkerError("incomplete_analysis")
    return result


class WebWorker:
    def __init__(self, config_path: Path, root: Path = ROOT):
        self.root = Path(root)
        try:
            self.config = _json_read(Path(config_path), 65536)
            url = urlsplit(self.config["baseUrl"])
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.config["workerId"]):
                raise ValueError()
            token = self.config["workerToken"]
            if not isinstance(token, str) or len(token) < 24 or any(c.isspace() for c in token) or token.startswith("REPLACE_"):
                raise ValueError()
            poll = self.config.get("pollSeconds", 30)
            if isinstance(poll, bool) or not isinstance(poll, int) or not 5 <= poll <= 300:
                raise ValueError()
        except (OSError, ValueError, TypeError, KeyError):
            raise WorkerError("worker_configuration_invalid") from None
        self.poll_seconds = poll
        self.folder = self.root / "data" / "web-worker"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state_path = self.folder / "state.json"
        if self.state_path.exists():
            try:
                self.state = _json_read(self.state_path, 128 * 1024 * 1024)
                if not isinstance(self.state.get("jobs"), dict) or not isinstance(self.state.get("published"), dict):
                    raise ValueError()
            except (OSError, ValueError, AttributeError):
                raise WorkerError("worker_state_invalid_do_not_restart_jobs") from None
        else:
            self.state = {"schemaVersion": 1, "jobs": {}, "published": {}}
        self.state.setdefault("publishRejected", {})
        self.retry_at = 0.0
        self.backoff = 5

    def _save(self):
        _json_write(self.state_path, self.state)

    def _log(self, code, job_id=None):
        entry = {"atUtc": utc_now(), "code": code}
        if job_id:
            entry["jobId"] = job_id
        with (self.folder / "worker.log.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def check(self):
        if not (self.root / "agent.py").is_file() or not (self.root / "config.json").is_file():
            raise WorkerError("agent_files_missing")
        return {"ready": True, "transport": "outbound_https_polling", "pollSeconds": self.poll_seconds}

    def _post(self, action, payload):
        if action not in {"poll", "update", "publish"}:
            raise WorkerError("invalid_action")
        if time.monotonic() < self.retry_at:
            raise BridgeUnavailable("bridge_backoff")
        request = Request(
            self.config["baseUrl"].rstrip("/") + "?action=" + action,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "TechNewsAgent-WebWorker/1"},
        )
        # urllib does not copy unredirected headers to a redirect destination.
        request.add_unredirected_header("Authorization", "Bearer " + self.config["workerToken"])
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            result = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(result, dict):
                raise ValueError()
        except HTTPError as error:
            if 400 <= error.code < 500 and error.code not in {401, 403, 408, 429}:
                self._log("bridge_rejected_" + str(error.code))
                raise BridgeRejected(error.code) from None
            self.retry_at = time.monotonic() + self.backoff
            self.backoff = min(300, self.backoff * 2)
            self._log("bridge_unavailable")
            raise BridgeUnavailable("bridge_unavailable") from None
        except (OSError, URLError, ValueError):
            self.retry_at = time.monotonic() + self.backoff
            self.backoff = min(300, self.backoff * 2)
            self._log("bridge_unavailable")
            raise BridgeUnavailable("bridge_unavailable") from None
        self.backoff = 5
        self.retry_at = 0
        return result

    def _deliver_update(self, record):
        payload_hash = hashlib.sha256(json.dumps(record["updatePayload"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if record.get("remoteRejection", {}).get("payloadHash") == payload_hash:
            return True
        try:
            self._post("update", record["updatePayload"])
        except BridgeRejected as error:
            record.update(pendingUpdate=False, needsReview=True,
                          remoteRejection={"statusCode": error.status_code, "payloadHash": payload_hash, "atUtc": utc_now()})
            self._save()
            self._log("update_rejected_check_local", record["id"])
            return True
        except BridgeUnavailable:
            record["pendingUpdate"] = True
            self._save()
            return False
        record["pendingUpdate"] = False
        self._save()
        return True

    def _payload(self, record, status, phase, error=None, report=None, mail_sent=None):
        payload = {"workerId": self.config["workerId"], "jobId": record["id"], "status": status, "phase": phase}
        if record.get("runId"):
            payload["runId"] = record["runId"]
        if mail_sent is not None:
            payload["mailSent"] = bool(mail_sent)
        if error:
            payload["error"] = error
        if report is not None:
            payload["report"] = report
        record.update(updatePayload=payload, pendingUpdate=True, updatedAtUtc=utc_now())

    def _run_statuses(self):
        runs = self.root / "data" / "runs"
        if not runs.exists():
            return []
        results = []
        for folder in sorted(runs.iterdir())[-500:]:
            if not folder.is_dir() or not _RUN_ID.fullmatch(folder.name):
                continue
            try:
                status = _json_read(folder / "status.json", 65536)
                if isinstance(status, dict):
                    results.append((folder, status))
            except (OSError, ValueError):
                continue
        return results

    def _find_run(self, identifier):
        matches = [(folder, status) for folder, status in self._run_statuses() if status.get("demoId") == "web-" + identifier]
        return matches[-1] if matches else (None, {})

    def _phases(self, folder):
        phases = []
        if folder is not None:
            try:
                with (folder / "events.jsonl").open("rb") as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    stream.seek(max(0, size - 65536))
                    lines = stream.read().decode("utf-8", errors="replace").splitlines()
                for line in lines:
                    try:
                        value = json.loads(line).get("phase")
                        if value in _PHASES:
                            phases.append(value)
                    except (ValueError, AttributeError):
                        pass
            except OSError:
                pass
        return phases

    def _phase(self, folder, status):
        phases = self._phases(folder)
        if phases:
            return phases[-1]
        if status.get("status") == "sent":
            return "complete"
        return "render" if status.get("status") == "preview_ready" else "collect"

    def _agent_busy(self):
        try:
            with exclusive_lock(self.root / "data" / "agent.lock"):
                pass
            return False
        except RuntimeError:
            return True

    def recover_interrupted(self):
        for record in self.state["jobs"].values():
            if record.get("status") in {"accepted", "running", "sending"}:
                record["status"] = "uncertain"
                self._payload(record, "failed", "failed", error="worker_interrupted_check_local")
        self._save()

    def _finish(self, record, returncode, folder, status, interrupted=False):
        if folder is not None:
            record["runId"] = folder.name
        mail_sent = status.get("sent") is True and status.get("status") == "sent"
        public_only = record["mode"] == "public-send"
        successful = returncode == 0 and (mail_sent if record["mode"] in {"send", "public-send"} else status.get("status") == "preview_ready")
        if mail_sent:
            successful = True  # A saved receipt/status remains authoritative after a later exit error.
        if successful:
            try:
                if public_only:
                    from public_news import validate_public_report
                    if status.get("publicOnly") is not True or status.get("mode") != "public-send":
                        raise WorkerError("public_run_required")
                    analysis = validate_public_report(_json_read(folder / "public-analysis.json"))
                else:
                    analysis = _json_read(folder / "analysis.json")
                report = None if public_only else sanitize_report(analysis)
            except (OSError, ValueError, WorkerError, AnalysisError):
                record["status"] = "failed"
                self._payload(record, "failed", "failed", error="report_unavailable", mail_sent=mail_sent)
            else:
                record["status"] = "completed"
                self._payload(record, "completed", "complete", report=report, mail_sent=mail_sent)
        else:
            uncertain = interrupted or status.get("failureType") == "MailOutcomeUnknown" or (
                status.get("failureType") != "MailNotSent" and (record.get("status") == "sending" or (
                    record["mode"] in {"send", "public-send"} and "send" in self._phases(folder)))
            )
            record["status"] = "uncertain" if uncertain else "failed"
            error_code = "delivery_unknown_check_local" if uncertain else (
                "insufficient_news" if status.get("failureType") == "InsufficientNewsError" else "agent_failed")
            self._payload(record, "failed", "failed", error=error_code, mail_sent=True if mail_sent else (None if uncertain else False))
        self._save()
        self._log(record["status"], record["id"])
        self._deliver_update(record)

    def handle_job(self, value):
        job = validate_job(value)
        if job["id"] in self.state["jobs"]:
            record = self.state["jobs"][job["id"]]
            self._log("duplicate_job_not_executed", job["id"])
            self._deliver_update(record)
            return record["status"]
        record = {**job, "status": "accepted", "createdAtUtc": utc_now()}
        self.state["jobs"][job["id"]] = record
        self._payload(record, "running", "collect")
        self._save()  # Save before Popen: crashes cannot cause the same job to run twice.
        existing, old_status = self._find_run(job["id"])
        if existing is not None:
            self._finish(record, 0, existing, old_status, interrupted=old_status.get("status") not in {"sent", "preview_ready", "failed"})
            return record["status"]
        if self._agent_busy():
            record["status"] = "failed"
            self._payload(record, "failed", "failed", error="agent_busy", mail_sent=False)
            self._save()
            self._deliver_update(record)
            return record["status"]
        options = {"cwd": str(self.root), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": False}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        try:
            process = subprocess.Popen(build_command(job, self.root), **options)
        except OSError:
            record["status"] = "failed"
            self._payload(record, "failed", "failed", error="agent_start_failed", mail_sent=False)
            self._save()
            self._deliver_update(record)
            return record["status"]
        record["status"] = "running"
        record["pid"] = process.pid
        self._save()
        self._log("job_started", job["id"])
        started = time.monotonic()
        heartbeat_at = 0
        folder, status = None, {}
        try:
            while process.poll() is None:
                folder, status = self._find_run(job["id"])
                phase = self._phase(folder, status)
                if folder is not None:
                    record["runId"] = folder.name
                if phase == "send":
                    record["status"] = "sending"
                if time.monotonic() >= heartbeat_at:
                    self._payload(record, "running", phase if phase not in {"complete", "failed"} else "render")
                    self._save()
                    self._deliver_update(record)
                    heartbeat_at = time.monotonic() + HEARTBEAT_SECONDS
                if time.monotonic() - started > JOB_TIMEOUT_SECONDS:
                    raise TimeoutError()
                time.sleep(1)
        except BaseException:
            from analyzer import _stop_process_tree
            _stop_process_tree(process)
            folder, status = self._find_run(job["id"])
            self._finish(record, -1, folder, status, interrupted=True)
            raise
        folder, status = self._find_run(job["id"])
        self._finish(record, process.returncode, folder, status)
        return record["status"]

    def flush_updates(self):
        for record in self.state["jobs"].values():
            if record.get("pendingUpdate") and not self._deliver_update(record):
                break

    def publish_daily_reports(self):
        for folder, status in self._run_statuses():
            if status.get("publicOnly") or status.get("audience") == "public" or status.get("mode") in {"public-send", "public-preview"}:
                continue
            if status.get("demoId") or status.get("status") not in {"sent", "preview_ready"}:
                continue
            if status.get("status") == "preview_ready" and status.get("mode") != "preview":
                continue  # A send-mode run is briefly preview_ready before delivery starts.
            try:
                report = sanitize_report(_json_read(folder / "analysis.json"))
            except (OSError, ValueError, WorkerError):
                continue
            mail_sent = status.get("status") == "sent" and status.get("sent") is True
            digest = hashlib.sha256(json.dumps({"report": report, "mailSent": mail_sent}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if self.state["published"].get(folder.name) == digest:
                continue
            if self.state["publishRejected"].get(folder.name, {}).get("hash") == digest:
                continue
            payload = {"workerId": self.config["workerId"], "runId": folder.name, "report": report, "mailSent": mail_sent,
                       "completedAt": _text(status.get("mailAcceptedAtUtc") or status.get("renderedAtUtc") or status.get("analyzedAtUtc"), 80)}
            try:
                self._post("publish", payload)
            except BridgeRejected as error:
                self.state["publishRejected"][folder.name] = {"hash": digest, "statusCode": error.status_code, "atUtc": utc_now(), "payload": payload}
                self._save()
                self._log("daily_report_rejected_check_local")
                continue
            self.state["published"][folder.name] = digest
            self._save()
            self._log("daily_report_published")

    def run_once(self):
        self.flush_updates()
        self.publish_daily_reports()
        response = self._post("poll", {"workerId": self.config["workerId"]})
        value = response.get("pollSeconds")
        if isinstance(value, int) and not isinstance(value, bool) and 5 <= value <= 300:
            self.poll_seconds = value
        if response.get("job") is not None:
            return self.handle_job(response["job"])
        return "idle"

    def serve(self, once=False):
        with exclusive_lock(self.folder / "worker.lock"):
            self.recover_interrupted()
            self._log("worker_started")
            while True:
                try:
                    result = self.run_once()
                except BridgeUnavailable:
                    result = "offline"
                except WorkerError:
                    self._log("invalid_job_rejected")
                    result = "failed"
                if once:
                    return 0 if result in {"completed", "idle"} else 1
                time.sleep(max(self.poll_seconds, self.retry_at - time.monotonic(), 1))


def main(argv=None):
    parser = argparse.ArgumentParser(description="网页任务的电脑端 HTTPS 轮询 worker")
    parser.add_argument("--config", type=Path, default=ROOT / "web-worker-config.json")
    parser.add_argument("--once", action="store_true", help="轮询一次；若领取任务则等待这次执行和回传")
    parser.add_argument("--check", action="store_true", help="只检查本地配置，不联网、不执行任务")
    args = parser.parse_args(argv)
    try:
        worker = WebWorker(args.config)
        if args.check:
            print(json.dumps(worker.check(), ensure_ascii=False))
            return 0
        return worker.serve(once=args.once)
    except KeyboardInterrupt:
        print("worker_stopped_check_any_interrupted_job")
        return 130
    except (WorkerError, RuntimeError, OSError):
        print("worker_failed_check_local_configuration_or_log", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Local-panel boundaries, using temporary files and no real API or credentials."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import dashboard  # noqa: E402

CASE = "甲公司依约交货，乙公司确认收货但未支付约定货款。"
RESULT = {"cause": "买卖合同纠纷", "laws": ["《接口测试规则》第1条"],
          "direction": "支持", "verdict": "这是隔离测试的虚构结果。"}


class FakeState:
    def __init__(self, root):
        self.root = root
        self.judge_slot = threading.BoundedSemaphore(1)
        self.status = mock.Mock(return_value={"app": dashboard.APP, "localHealth": True})
        self.check_public = mock.Mock(return_value={"publicHealth": {"ok": True}})
        self.judge = mock.Mock(return_value={"ok": True, "status": 200,
                                            "result": RESULT, "seconds": 0.01})


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / "runtime").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="dashboard-test-", dir=PROJECT / "runtime")
        self.root = Path(self.temporary.name)
        self.assertTrue(self.root.resolve().is_relative_to(PROJECT.resolve()))
        self.fake_key = secrets.token_urlsafe(32)
        (self.root / "dashboard.html").write_text(
            "<!doctype html><p>Local panel</p><script>const t='__DASHBOARD_TOKEN__';</script>",
            encoding="utf-8",
        )
        (self.root / ".env").write_text("JUDGE_API_KEY=" + self.fake_key, encoding="utf-8")
        self.state = FakeState(self.root)
        self.server = dashboard.make_dashboard(self.state, port=0)
        self.token = self.server.dashboard_token
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request(self, method, path, *, payload=None, raw=None, headers=None):
        actual = {"Host": f"127.0.0.1:{self.port}"}
        actual.update(headers or {})
        if raw is None and payload is not None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if raw is not None:
            actual.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=raw, headers=actual)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, path, payload=None, **kwargs):
        headers = {"X-Dashboard-Token": self.token}
        headers.update(kwargs.pop("headers", {}))
        return self.request("POST", path, payload={} if payload is None else payload,
                            headers=headers, **kwargs)

    def assert_safe_json(self, response, status):
        code, headers, raw = response
        self.assertEqual(code, status)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertNotIn(self.fake_key.encode(), raw)
        return json.loads(raw)

    def test_loopback_page_contains_only_page_nonce_not_api_key(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        code, headers, raw = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(self.token.encode(), raw)
        self.assertNotIn(b"__DASHBOARD_TOKEN__", raw)
        self.assertNotIn(self.fake_key.encode(), raw)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_unknown_paths_never_serve_files(self):
        for path in ("/.env", "/dashboard.py", "/runtime/status.json", "/runtime/api.log",
                     "/../.env", "/%2e%2e/.env", "/api/status?file=.env"):
            with self.subTest(path=path):
                self.assert_safe_json(self.request("GET", path), 404)
        self.state.status.assert_not_called()

    def test_host_origin_and_cross_site_requests_are_rejected(self):
        forbidden = [
            {"Host": "example.invalid"},
            {"Host": f"127.0.0.1:{self.port + 1}"},
            {"Host": f"127.0.0.1:{self.port}.example.invalid"},
            {"Origin": "https://example.invalid"},
            {"Origin": "null"},
            {"Origin": f"http://127.0.0.1:{self.port + 1}"},
            {"Sec-Fetch-Site": "cross-site"},
        ]
        for headers in forbidden:
            with self.subTest(header=next(iter(headers))):
                self.assert_safe_json(self.request("GET", "/api/status", headers=headers), 403)
                self.assert_safe_json(self.post("/api/check-public", headers=headers), 403)
        self.state.status.assert_not_called()
        self.state.check_public.assert_not_called()

    def test_missing_wrong_and_unicode_nonce_fail_before_json_parsing(self):
        for token in (None, "", "wrong-fixture-token", "caf\u00e9"):
            with self.subTest(token_present=token is not None):
                headers = {} if token is None else {"X-Dashboard-Token": token}
                self.assert_safe_json(self.request("POST", "/api/judge", raw=b"not-json",
                                                   headers=headers), 403)
        self.state.judge.assert_not_called()

    def test_local_status_and_authorized_actions_use_fake_state(self):
        self.assert_safe_json(self.request("GET", "/api/status"), 200)
        self.assert_safe_json(self.post("/api/check-public", headers={
            "Origin": f"http://127.0.0.1:{self.port}"}), 200)
        with contextlib.redirect_stdout(io.StringIO()):
            body = self.assert_safe_json(self.post("/api/judge", {"case_text": CASE}), 200)
        self.assertEqual(body["result"], RESULT)
        self.state.judge.assert_called_once_with(CASE)
        self.state.check_public.assert_called_once_with()

    def test_invalid_json_and_case_boundaries_never_call_judge(self):
        for raw in (b"{", b"[]", b"null", b'{"case_text":"\xff"}'):
            with self.subTest(raw_length=len(raw)):
                self.assert_safe_json(self.request("POST", "/api/judge", raw=raw,
                    headers={"X-Dashboard-Token": self.token}), 400)
        for value in (None, 1, [], "", "一二三四五六七", "测" * 50001):
            with self.subTest(value_type=type(value).__name__):
                self.assert_safe_json(self.post("/api/judge", {"case_text": value}), 400)
        self.state.judge.assert_not_called()

    def test_only_one_page_judgment_can_run_at_a_time(self):
        self.state.judge_slot.acquire()
        try:
            self.assert_safe_json(self.post("/api/judge", {"case_text": CASE}), 409)
        finally:
            self.state.judge_slot.release()
        self.state.judge.assert_not_called()

    def test_judge_failure_is_generic_and_releases_slot(self):
        marker = "PRIVATE_FIXTURE_ERROR_" + self.fake_key
        self.state.judge.side_effect = RuntimeError(marker)
        response = self.post("/api/judge", {"case_text": CASE})
        self.assert_safe_json(response, 502)
        self.assertNotIn(marker.encode(), response[2])
        self.assertTrue(self.state.judge_slot.acquire(blocking=False))
        self.state.judge_slot.release()

    def test_status_and_public_check_failures_are_generic_json(self):
        for path, method in (("/api/status", "GET"), ("/api/check-public", "POST")):
            with self.subTest(path=path):
                target = self.state.status if method == "GET" else self.state.check_public
                target.side_effect = RuntimeError("PRIVATE_FIXTURE_ERROR_" + self.fake_key)
                with contextlib.redirect_stderr(io.StringIO()):
                    response = self.request("GET", path) if method == "GET" else self.post(path)
                self.assert_safe_json(response, 502)
                self.assertNotIn(b"PRIVATE_FIXTURE_ERROR", response[2])


class DashboardBackendTests(unittest.TestCase):
    def test_state_projection_does_not_echo_private_fields(self):
        state = dashboard.DashboardState(Path("unused-fixture-root"))
        secret = secrets.token_urlsafe(32)
        fake_state = {"apiPid": 101, "tunnelPid": 102, "runnerPid": 103,
                      "url": "https://fixture-only.trycloudflare.com",
                      "key": secret, "error": "PRIVATE_SOURCE_PATH", "localSource": "PRIVATE_DOC"}
        with mock.patch.object(state, "state_file", return_value=fake_state), \
             mock.patch.object(state, "calls", return_value=[]), \
             mock.patch.object(dashboard, "request_json", return_value=(200, {"ok": True})), \
             mock.patch.object(dashboard, "process_alive", return_value=True):
            body = json.dumps(state.status())
        for private in (secret, "PRIVATE_SOURCE_PATH", "PRIVATE_DOC"):
            self.assertNotIn(private, body)

    def test_invalid_public_destination_is_not_requested(self):
        state = dashboard.DashboardState(Path("unused-fixture-root"))
        for url in ("http://127.0.0.1:9000", "https://example.invalid",
                    "https://fixture.trycloudflare.com@evil.invalid",
                    "https://fixture.trycloudflare.com/private"):
            with self.subTest(url_type=url.split(":")[0]):
                with mock.patch.object(state, "state_file", return_value={"url": url}), \
                     mock.patch.object(state, "status", return_value={"localHealth": True}), \
                     mock.patch.object(dashboard, "request_json") as request:
                    state.check_public()
                    request.assert_not_called()

    def test_judge_sends_fake_key_only_to_fixed_loopback_api(self):
        fake_key = secrets.token_urlsafe(32)
        state = dashboard.DashboardState(Path("unused-fixture-root"))
        with mock.patch("settings.load_settings", return_value={"JUDGE_API_KEY": fake_key}), \
             mock.patch.object(dashboard, "request_json", return_value=(200, RESULT)) as request:
            result = state.judge(CASE)
        request.assert_called_once_with("http://127.0.0.1:8000/judge", body={"case_text": CASE},
                                        key=fake_key, timeout=95)
        self.assertNotIn(fake_key, json.dumps(result))
        self.assertEqual(result["result"], RESULT)

    def test_loopback_requests_disable_proxies_and_redirects(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"ok":true}'
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(dashboard, "build_opener", return_value=opener) as build:
            self.assertEqual(dashboard.request_json("http://127.0.0.1:8000/health"),
                             (200, {"ok": True}))
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(h, dashboard.NoRedirect) for h in handlers))
        self.assertTrue(any(isinstance(h, dashboard.ProxyHandler) and h.proxies == {} for h in handlers))
        self.assertIsNone(dashboard.NoRedirect().redirect_request(None, None, 302, "", {},
                                                               "https://example.invalid"))


if __name__ == "__main__":
    unittest.main()

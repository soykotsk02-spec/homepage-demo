"""Isolated HTTP contract checks; no live key, source corpus, or tunnel needed.

Run from the project directory:
    python -m unittest discover -s tests -p test_api.py -v
"""

from __future__ import annotations

import copy
import contextlib
import http.client
import io
import json
from pathlib import Path
import secrets
import sys
import threading
import time
import unittest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from server import make_server  # noqa: E402


SAMPLE_TEXT = "甲公司已交付合同约定货物，乙公司确认收货但未支付约定价款。"
SAMPLE_RESULT = {
    "cause": "买卖合同纠纷",
    "laws": ["《接口测试规则》第1条"],
    "direction": "支持",
    "verdict": "本测试结果仅用于接口契约验证。",
}


class RecordingEngine:
    def __init__(self, result=None):
        self.result = copy.deepcopy(SAMPLE_RESULT if result is None else result)
        self.calls = []

    def judge(self, case_text):
        self.calls.append(case_text)
        return copy.deepcopy(self.result)


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.key = secrets.token_urlsafe(32)
        self.engine = RecordingEngine()
        self.server = None
        self.thread = None
        self.start_server()

    def start_server(self, *, engine=None, **options):
        self.stop_server()
        if engine is not None:
            self.engine = engine
        self.server = make_server(
            self.engine, self.key, host="127.0.0.1", port=0, **options
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.server = None
            self.thread = None

    def tearDown(self):
        self.stop_server()

    def request(self, method, path, *, payload=None, raw=None, authorization=None):
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        if raw is None and payload is not None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if raw is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=3
        )
        started = time.perf_counter()
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            body = response.read()
            return (
                response.status,
                dict(response.getheaders()),
                body,
                time.perf_counter() - started,
            )
        finally:
            connection.close()

    def judge(self, payload=None, **kwargs):
        if payload is None:
            payload = {"case_text": SAMPLE_TEXT}
        kwargs.setdefault("authorization", f"Bearer {self.key}")
        return self.request("POST", "/judge", payload=payload, **kwargs)

    def assert_json_response(self, response, status):
        actual, headers, body, elapsed = response
        self.assertEqual(actual, status)
        content_type = next(
            (value for name, value in headers.items() if name.lower() == "content-type"),
            "",
        )
        self.assertIn("application/json", content_type)
        self.assertNotIn("text/event-stream", content_type)
        self.assertLess(elapsed, 120)
        return json.loads(body.decode("utf-8"))

    def test_health_is_public_exact_and_does_not_invoke_engine(self):
        for authorization in (None, "Bearer deliberately-wrong-test-key"):
            with self.subTest(authorization_present=authorization is not None):
                result = self.assert_json_response(
                    self.request("GET", "/health", authorization=authorization), 200
                )
                self.assertEqual(result, {"ok": True})
                self.assertIs(result["ok"], True)
        self.assertEqual(self.engine.calls, [])

    def test_valid_bearer_returns_four_fields_as_one_json_response(self):
        response = self.judge()
        result = self.assert_json_response(response, 200)
        self.assertEqual(result, SAMPLE_RESULT)
        self.assertEqual(set(result), {"cause", "laws", "direction", "verdict"})
        self.assertEqual(self.engine.calls, [SAMPLE_TEXT])
        self.assertNotIn(self.key.encode(), response[2])

    def test_missing_empty_wrong_and_other_auth_schemes_are_rejected(self):
        for authorization in (
            None,
            "Bearer",
            "Bearer ",
            "Bearer deliberately-wrong-test-key",
            f"Basic {self.key}",
            self.key,
            "Bearer caf\u00e9",
        ):
            with self.subTest(auth_kind=(authorization or "missing").split(" ")[0]):
                response = self.request(
                    "POST", "/judge", payload={"case_text": SAMPLE_TEXT},
                    authorization=authorization,
                )
                self.assert_json_response(response, 401)
                self.assertNotIn(self.key.encode(), response[2])
        self.assertEqual(self.engine.calls, [])

    def test_authentication_precedes_json_parsing_and_size_check(self):
        self.start_server(max_body=64)
        for raw in (b"{not-valid-json", b"x" * 128):
            with self.subTest(size=len(raw)):
                self.assert_json_response(
                    self.request("POST", "/judge", raw=raw), 401
                )
        self.assertEqual(self.engine.calls, [])

    def test_query_parameter_does_not_replace_bearer_header(self):
        response = self.request(
            "POST", f"/judge?api_key={self.key}",
            payload={"case_text": SAMPLE_TEXT},
        )
        self.assertIn(response[0], (401, 404))
        self.assertNotIn(self.key.encode(), response[2])
        self.assertEqual(self.engine.calls, [])

    def test_unknown_routes_never_serve_local_files(self):
        for path in ("/", "/docs", "/.env", "/server.py", "/data/", "/missing"):
            with self.subTest(path=path):
                response = self.request("GET", path)
                self.assertEqual(response[0], 404)
                self.assertNotIn(self.key.encode(), response[2])
        self.assertEqual(self.engine.calls, [])

    def test_judge_cannot_be_called_using_get(self):
        response = self.request("GET", "/judge", authorization=f"Bearer {self.key}")
        self.assertIn(response[0], (404, 405))
        self.assertEqual(self.engine.calls, [])

    def test_malformed_json_utf8_and_empty_body_are_bad_requests(self):
        for raw in (b"", b"{", b"not-json", b"{\"case_text\":\"\xff\"}"):
            with self.subTest(raw_size=len(raw)):
                self.assert_json_response(
                    self.request("POST", "/judge", raw=raw,
                                 authorization=f"Bearer {self.key}"), 400
                )
        self.assertEqual(self.engine.calls, [])

    def test_missing_empty_and_non_string_case_text_are_bad_requests(self):
        for payload in ({}, [], {"case_text": ""}, {"case_text": " \n\t "},
                        {"case_text": "一二三四五六七"}, {"case_text": "测" * 50001},
                        {"case_text": 123}, {"case_text": False},
                        {"case_text": None}, {"case_text": [SAMPLE_TEXT]},
                        {"case_text": {"text": SAMPLE_TEXT}}):
            with self.subTest(payload_type=type(payload).__name__):
                self.assert_json_response(self.judge(payload), 400)
        self.assertEqual(self.engine.calls, [])

    def test_excessive_json_nesting_returns_400_instead_of_dropping_connection(self):
        raw = (b'{"case_text":"fixture","extra":' + b"[" * 20000
               + b"0" + b"]" * 20000 + b"}")
        self.assertLess(len(raw), 1048576)
        # Keep any failure traceback out of the test output; the HTTP result is
        # the regression contract and no real corpus or configuration is read.
        with contextlib.redirect_stderr(io.StringIO()):
            response = self.request(
                "POST", "/judge", raw=raw, authorization=f"Bearer {self.key}"
            )
        self.assert_json_response(response, 400)
        self.assertEqual(self.engine.calls, [])

    def test_too_large_authenticated_body_is_rejected_without_inference(self):
        self.start_server(max_body=128)
        self.assert_json_response(self.judge({"case_text": "测" * 100}), 413)
        self.assertEqual(self.engine.calls, [])

    def test_invalid_engine_outputs_do_not_become_successful_answers(self):
        invalid_results = [
            {}, [], "not a structured answer",
            {**SAMPLE_RESULT, "laws": "not a list"},
            {**SAMPLE_RESULT, "laws": [123]},
            {**SAMPLE_RESULT, "cause": None},
            {**SAMPLE_RESULT, "verdict": None},
            {**SAMPLE_RESULT, "direction": "unknown-direction"},
        ]
        for result in invalid_results:
            with self.subTest(result_type=type(result).__name__):
                self.engine.result = result
                body = self.assert_json_response(self.judge(), 500)
                self.assertFalse(set(SAMPLE_RESULT).issubset(body))

    def test_engine_exception_is_a_safe_error_not_a_fake_verdict(self):
        marker = "PRIVATE_INTERNAL_EXCEPTION_MARKER"

        class FailingEngine:
            def judge(self, case_text):
                raise RuntimeError(marker)

        self.start_server(engine=FailingEngine())
        response = self.judge()
        body = self.assert_json_response(response, 500)
        self.assertFalse(set(SAMPLE_RESULT).issubset(body))
        self.assertNotIn(marker.encode(), response[2])
        self.assertNotIn(b"Traceback", response[2])

    def test_timeout_is_504_and_health_remains_responsive(self):
        release = threading.Event()

        class SlowEngine:
            def judge(self, case_text):
                release.wait(timeout=1)
                return copy.deepcopy(SAMPLE_RESULT)

        self.start_server(engine=SlowEngine(), request_timeout=0.05)
        try:
            response = self.judge()
            body = self.assert_json_response(response, 504)
            self.assertLess(response[3], 1.5)
            self.assertFalse(set(SAMPLE_RESULT).issubset(body))
            self.assertEqual(
                self.assert_json_response(self.request("GET", "/health"), 200),
                {"ok": True},
            )
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()

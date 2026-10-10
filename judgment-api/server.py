"""Two-route, non-streaming coursework API. No filesystem serving or model tools."""
import concurrent.futures
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import socket
import threading
import time
from urllib.parse import urlsplit

LOGGER = logging.getLogger('judgment-api')
DIRECTIONS = {'支持', '部分支持', '驳回'}


def valid_result(value):
    if not isinstance(value, dict) or set(value) != {'cause', 'laws', 'direction', 'verdict'}:
        return False
    return (all(isinstance(value[k], str) and value[k].strip() for k in ('cause', 'direction', 'verdict'))
            and value['direction'] in DIRECTIONS
            and isinstance(value['laws'], list) and bool(value['laws'])
            and all(isinstance(x, str) and x.startswith('《') and '》第' in x and x.endswith('条') for x in value['laws']))


def make_server(engine, key, host='127.0.0.1', port=8000, request_timeout=90, max_body=1048576):
    if not key:
        raise ValueError('A nonempty dedicated key is required')
    expected = ('Bearer ' + key).encode('utf-8')
    workers = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='judge')
    slots = threading.BoundedSemaphore(2)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'CourseJudge/1.0'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_args):
            # No URLs, request bodies, personal details or Authorization headers in logs.
            pass

        def send_json(self, status, value):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            if status == 401:
                self.send_header('WWW-Authenticate', 'Bearer')
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass

        def do_GET(self):
            if urlsplit(self.path).path == '/health':
                self.send_json(200, {'ok': True})
            else:
                self.send_json(404, {'error': 'not_found'})

        def do_POST(self):
            if urlsplit(self.path).path != '/judge':
                self.send_json(404, {'error': 'not_found'})
                return
            supplied = self.headers.get('Authorization', '').encode('utf-8')
            if not hmac.compare_digest(supplied, expected):
                self.send_json(401, {'error': 'unauthorized'})
                return
            if self.headers.get('Transfer-Encoding'):
                self.send_json(400, {'error': 'content_length_required'})
                return
            lengths = self.headers.get_all('Content-Length', [])
            try:
                if len(lengths) != 1:
                    raise ValueError()
                size = int(lengths[0])
                if size < 0:
                    raise ValueError()
            except ValueError:
                self.send_json(400, {'error': 'invalid_content_length'})
                return
            if size > max_body:
                self.send_json(413, {'error': 'request_too_large'})
                return
            if self.headers.get_content_type() != 'application/json':
                self.send_json(400, {'error': 'application_json_required'})
                return
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError()
                body = json.loads(raw.decode('utf-8-sig'))
                case = body.get('case_text') if isinstance(body, dict) else None
                if not isinstance(case, str) or len(case.strip()) < 8 or len(case) > 50000:
                    raise ValueError()
                case = case.strip()
            except (UnicodeError, ValueError, RecursionError, socket.timeout):
                self.send_json(400, {'error': 'invalid_case_text_or_json'})
                return
            if not slots.acquire(blocking=False):
                self.send_json(503, {'error': 'busy_retry_later'})
                return
            started = time.monotonic()
            future = workers.submit(engine.judge, case)
            future.add_done_callback(lambda _: slots.release())
            try:
                answer = future.result(timeout=request_timeout)
                if not valid_result(answer):
                    raise ValueError('Invalid engine response')
                self.send_json(200, answer)
                LOGGER.info('judge status=200 duration_ms=%.1f', (time.monotonic() - started) * 1000)
            except concurrent.futures.TimeoutError:
                self.send_json(504, {'error': 'prediction_timeout'})
                LOGGER.warning('judge status=504')
            except Exception as exc:
                self.send_json(500, {'error': 'prediction_failed'})
                LOGGER.error('judge status=500 type=%s', type(exc).__name__)

        def do_OPTIONS(self):
            self.send_json(405, {'error': 'method_not_allowed'})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def server_close(self):
            super().server_close()
            workers.shutdown(wait=False, cancel_futures=True)

    return Server((host, port), Handler)


def main():
    from settings import load_settings
    from engine import JudgmentEngine
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    settings = load_settings()
    LOGGER.info('loading local corpus and search index')
    engine = JudgmentEngine(settings['JUDGMENTS_PATH'])
    server = make_server(engine, settings['JUDGE_API_KEY'])
    LOGGER.info('ready on 127.0.0.1:8000; only /health and /judge are enabled')
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()


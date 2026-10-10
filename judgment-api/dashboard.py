"""Visible, loopback-only companion to the already running coursework API.

The tunnel still forwards only port 8000. This UI on 8001 never returns the
coursework key, serves arbitrary files, or stops/restarts the existing service.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime'
APP = 'judgment-local-dashboard'
PUBLIC_URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com\Z')
LOG_LINE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*?judge status=(\d+)(?: duration_ms=([\d.]+))?')


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, *, body=None, key=None, timeout=3, local=True):
    headers = {'Content-Type': 'application/json'}
    if key is not None:
        headers['Authorization'] = 'Bearer ' + key
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = Request(url, data=raw, headers=headers, method='GET' if raw is None else 'POST')
    # Loopback requests must never pass case text or a key through a proxy.
    handlers = [NoRedirect()]
    if local:
        handlers.append(ProxyHandler({}))
    opener = build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read(1048577)
    except HTTPError as exc:
        status, raw = exc.code, exc.read(1048577)
    if len(raw) > 1048576:
        raise ValueError('Response too large')
    return status, json.loads(raw)


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DashboardState:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.runtime = self.root / 'runtime'
        self.lock = threading.Lock()
        self.public = {'ok': None, 'checkedAt': None, 'error': None, 'url': None}
        self.public_check_lock = threading.Lock()
        self.judge_slot = threading.BoundedSemaphore(1)

    def state_file(self):
        try:
            state = json.loads((self.runtime / 'status.json').read_text(encoding='utf-8'))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def calls(self):
        entries = []
        try:
            # Read only a bounded tail, never expose raw logs or traceback paths.
            with (self.runtime / 'api.log').open('rb') as file:
                size = file.seek(0, 2)
                file.seek(max(0, size - 1024 * 1024))
                content = file.read().decode('utf-8', errors='replace')
            for line in content.splitlines():
                match = LOG_LINE.match(line)
                if match:
                    entries.append({'time': match.group(1), 'status': int(match.group(2)),
                                    'durationMs': float(match.group(3)) if match.group(3) else None})
        except OSError:
            pass
        return entries

    def status(self):
        state = self.state_file()
        try:
            code, body = request_json('http://127.0.0.1:8000/health', timeout=1)
            healthy = code == 200 and body == {'ok': True}
        except Exception:
            healthy = False
        entries = self.calls()
        url = state.get('url')
        if not isinstance(url, str) or not PUBLIC_URL.fullmatch(url):
            url = None
        with self.lock:
            public = dict(self.public) if url and self.public.get('url') == url else {'ok': None, 'checkedAt': None, 'error': None}
        api_alive = process_alive(state.get('apiPid'))
        tunnel_alive = process_alive(state.get('tunnelPid'))
        supervisor_alive = process_alive(state.get('runnerPid'))
        return {'app': APP, 'source': 'local', 'checkedAt': now(), 'localHealth': healthy,
                'apiPid': state.get('apiPid'), 'tunnelPid': state.get('tunnelPid'), 'runnerPid': state.get('runnerPid'),
                'apiProcessAlive': api_alive, 'tunnelProcessAlive': tunnel_alive,
                'supervisorProcessAlive': supervisor_alive,
                'sleepInhibited': bool(supervisor_alive and state.get('sleepInhibited')),
                'publicUrl': url, 'publicHealth': public, 'log': list(reversed(entries[-30:])),
                'totalCalls': len(entries), 'latestCallAt': entries[-1]['time'] if entries else None}

    def check_public(self):
        if not self.public_check_lock.acquire(blocking=False):
            return self.status()
        try:
            url = self.state_file().get('url')
            result = {'ok': False, 'checkedAt': now(), 'error': None, 'url': url}
            if not isinstance(url, str) or not PUBLIC_URL.fullmatch(url):
                result['error'] = '尚无有效的公网地址'
            else:
                try:
                    code, body = request_json(url + '/health', timeout=12, local=False)
                    result['ok'] = code == 200 and body == {'ok': True}
                    if not result['ok']:
                        result['error'] = '公网健康检查未通过'
                except Exception:
                    result['error'] = '这次公网检查未能连接，请稍后再试'
            with self.lock:
                self.public = result
        finally:
            self.public_check_lock.release()
        return self.status()

    def judge(self, case):
        from settings import load_settings
        key = load_settings(self.root / '.env')['JUDGE_API_KEY']
        started = time.monotonic()
        code, result = request_json('http://127.0.0.1:8000/judge', body={'case_text': case}, key=key, timeout=95)
        return {'ok': code == 200, 'status': code, 'result': result, 'seconds': round(time.monotonic() - started, 3)}


def make_dashboard(state=None, port=8001, token=None):
    state = state or DashboardState()
    token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'LocalJudgePanel/1.0'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_args):
            pass

        def send(self, code, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass

        def allowed(self, *, mutation=False):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.client_address[0] != '127.0.0.1' or self.headers.get('Host') not in hosts:
                self.send(403, {'error': 'local_host_required'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin not in {'http://' + host for host in hosts}:
                self.send(403, {'error': 'same_origin_required'})
                return False
            if self.path.startswith('/api/') and self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.send(403, {'error': 'same_origin_required'})
                return False
            if mutation and not hmac.compare_digest(self.headers.get('X-Dashboard-Token', '').encode('utf-8'), token.encode('utf-8')):
                self.send(403, {'error': 'dashboard_token_required'})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            if self.path == '/':
                try:
                    html = (state.root / 'dashboard.html').read_text(encoding='utf-8').replace('__DASHBOARD_TOKEN__', token)
                    self.send(200, html.encode('utf-8'), 'text/html; charset=utf-8')
                except OSError:
                    self.send(503, {'error': 'dashboard_page_unavailable'})
            elif self.path == '/api/status':
                try:
                    self.send(200, state.status())
                except Exception:
                    self.send(502, {'error': '暂时无法读取本机状态，请稍后刷新'})
            else:
                self.send(404, {'error': 'not_found'})

        def do_POST(self):
            if not self.allowed(mutation=True):
                return
            if self.path not in ('/api/judge', '/api/check-public'):
                self.send(404, {'error': 'not_found'})
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
                    raise ValueError()
                size = int(lengths[0])
                if size < 0 or size > 1048576 or self.headers.get_content_type() != 'application/json':
                    raise ValueError()
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError()
                value = json.loads(raw.decode('utf-8'))
                if not isinstance(value, dict):
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError, socket.timeout):
                self.send(400, {'error': 'invalid_json'})
                return
            if self.path == '/api/check-public':
                try:
                    self.send(200, state.check_public())
                except Exception:
                    self.send(502, {'error': '暂时无法完成公网检查，请稍后重试'})
                return
            case = value.get('case_text')
            if not isinstance(case, str) or not 8 <= len(case.strip()) <= 50000:
                self.send(400, {'error': '案情需要 8 至 50,000 个字'})
                return
            if not state.judge_slot.acquire(blocking=False):
                self.send(409, {'error': '页面测试正在进行，请等待本次完成'})
                return
            try:
                answer = state.judge(case.strip())
                self.send(200, answer)
                print(f'[{now()}] 本机页面测试完成：HTTP {answer["status"]}，耗时 {answer["seconds"]} 秒', flush=True)
            except Exception:
                self.send(502, {'error': '本机 API 暂不可用或请求超时，请检查运行状态'})
            finally:
                state.judge_slot.release()

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = Server(('127.0.0.1', port), Handler)
    server.dashboard_token = token
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true', help='Open the local page in the default browser')
    args = parser.parse_args()
    url = 'http://127.0.0.1:8001'
    try:
        server = make_dashboard()
    except OSError:
        try:
            code, result = request_json(url + '/api/status', timeout=2)
            if code == 200 and result.get('app') == APP:
                if args.open:
                    webbrowser.open(url + '/')
                print('本机运行页面已经开启。')
                return
        except Exception:
            pass
        raise SystemExit('8001 端口无法使用；未停止任何现有程序。')
    # Public checks are on demand in the page; do not create another monitor.
    if os.name == 'nt':
        ctypes.windll.kernel32.SetConsoleTitleW('本机判案服务 · 运行控制台')
    print('\n本机判案服务 · 运行控制台\n', flush=True)
    print('运行位置：这台 Windows 电脑。计算不在云端。', flush=True)
    print('打开运行页面：' + url + '/', flush=True)
    print('现有 API 和 Cloudflare 隧道继续使用原进程、原网址。', flush=True)
    print('关闭本窗口只关闭运行页面，不会停止老师正在调用的 API。\n', flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url + '/')).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

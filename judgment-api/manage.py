"""Start/stop only this API and its temporary tunnel; keep all state local."""
import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime'
STATE = RUNTIME / 'status.json'
STOP = RUNTIME / 'stop.request'
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


@contextmanager
def service_lock(wait_seconds=0):
    """OS releases this lock on exit/crash; a stale file alone never blocks startup."""
    RUNTIME.mkdir(exist_ok=True)
    handle = (RUNTIME / 'service.lock').open('a+b')
    if handle.seek(0, 2) == 0:
        handle.write(b'0')
        handle.flush()
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('This service is already starting or running') from None
                time.sleep(0.1)
        yield
    finally:
        handle.close()


def write_state(**values):
    values['updatedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(STATE)


def read_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def local_health():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as response:
            return response.status == 200 and json.load(response) == {'ok': True}
    except Exception:
        return False


def port_is_free():
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', 8000)) != 0


def run():
    from settings import load_settings
    load_settings()
    if not port_is_free():
        raise RuntimeError('Port 8000 is already in use; no existing process was stopped')
    tunnel_exe = ROOT / 'bin' / 'cloudflared.exe'
    if not tunnel_exe.is_file():
        raise RuntimeError('Install the official cloudflared executable in bin first')
    RUNTIME.mkdir(exist_ok=True)
    api = tunnel = None
    url = None
    state = {'runnerPid': os.getpid(), 'status': 'starting', 'url': None}
    awake = False
    if os.name == 'nt':
        # Process-scoped: restored when this supervisor exits; no permanent power-setting changes.
        awake = bool(ctypes.windll.kernel32.SetThreadExecutionState(0x80000001))
    state['sleepInhibited'] = awake
    write_state(**state)
    try:
        with (RUNTIME / 'api.log').open('a', encoding='utf-8') as api_log, (RUNTIME / 'tunnel.log').open('w', encoding='utf-8') as tunnel_log:
            env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
            api = subprocess.Popen([sys.executable, '-u', str(ROOT / 'server.py')], cwd=ROOT, env=env,
                                   stdin=subprocess.DEVNULL, stdout=api_log, stderr=api_log, creationflags=FLAGS)
            state['apiPid'] = api.pid
            write_state(**state)
            deadline = time.monotonic() + 180
            while not local_health():
                if api.poll() is not None:
                    raise RuntimeError('API startup failed; see local runtime/api.log')
                if time.monotonic() > deadline:
                    raise RuntimeError('API startup timed out')
                if STOP.exists():
                    return
                time.sleep(1)
            # The sole origin is this loopback API. No filesystem or other project is proxied.
            tunnel_config = RUNTIME / 'cloudflared-empty.yml'
            tunnel_config.write_text('{}\n', encoding='utf-8')
            tunnel_env = {k: v for k, v in os.environ.items()
                          if not k.startswith(('TUNNEL_', 'CLOUDFLARED_')) and k != 'NO_TLS_VERIFY'}
            tunnel = subprocess.Popen([str(tunnel_exe), 'tunnel', '--config', str(tunnel_config), '--url', 'http://127.0.0.1:8000',
                                       '--protocol', 'http2', '--no-autoupdate', '--metrics', '127.0.0.1:0',
                                       '--loglevel', 'info', '--management-diagnostics=false'],
                                      cwd=ROOT, stdin=subprocess.DEVNULL, stdout=tunnel_log, stderr=tunnel_log,
                                      creationflags=FLAGS, env=tunnel_env)
            state['tunnelPid'] = tunnel.pid
            state['status'] = 'connecting'
            write_state(**state)
            last_check = 0.0
            while not STOP.exists():
                if api.poll() is not None:
                    raise RuntimeError('API process exited; tunnel will be closed')
                if tunnel.poll() is not None:
                    raise RuntimeError('Tunnel process exited; restarting creates a new URL')
                if time.monotonic() - last_check >= 5:
                    log = (RUNTIME / 'tunnel.log').read_text(encoding='utf-8', errors='replace')
                    matches = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', log)
                    if matches:
                        url = matches[-1]
                    state.update(url=url, status='running' if url and 'Registered tunnel connection' in log else 'connecting')
                    write_state(**state)
                    last_check = time.monotonic()
                time.sleep(1)
            state['status'] = 'stopped'
    except Exception as exc:
        state.update(status='failed', error=str(exc))
        raise
    finally:
        # Only terminate child handles created by this supervisor, never unrelated PIDs.
        for child in (tunnel, api):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        if awake:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        state['sleepInhibited'] = False
        if state['status'] in ('starting', 'connecting', 'running'):
            state['status'] = 'stopped'
        write_state(**state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'run', 'status', 'stop'))
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.action == 'run':
        with service_lock(wait_seconds=5):
            run()
    elif args.action == 'status':
        status = read_state()
        status['localHealth'] = local_health()
        print(json.dumps(status, ensure_ascii=False, indent=2))
    elif args.action == 'stop':
        STOP.write_text('stop only this project\n', encoding='utf-8')
        print('Stop requested for this API and its tunnel. The URL will no longer work.')
    else:
        with service_lock():
            if not port_is_free():
                print('Port 8000 is in use. Existing service was left untouched; use status to inspect.')
                return
            STOP.unlink(missing_ok=True)
            with (RUNTIME / 'supervisor.log').open('a', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), 'run'], cwd=ROOT,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=FLAGS,
                                         env=dict(os.environ, PYTHONUTF8='1'))
        print(json.dumps({'started': True, 'runnerPid': child.pid, 'statusFile': 'runtime/status.json'}))


if __name__ == '__main__':
    main()


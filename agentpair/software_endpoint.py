"""Linux installation worker on the existing authenticated endpoint queue."""
import argparse
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from .software_install import install_linux


def serve(server, identity, *, once=False):
    url = urlparse(server)
    if url.scheme != 'https' or url.username or url.password or url.path not in ('', '/'):
        raise ValueError('Trusted HTTPS platform origin required')
    base = server.rstrip('/')
    def api(path, data=None):
        req = Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                      headers={'Authorization': 'Bearer ' + identity['token'], 'Content-Type': 'application/json'})
        with urlopen(req, timeout=25) as response: return json.load(response)
    while True:
        api('/api/endpoint/report', {'os': 'Linux', 'architecture': os.uname().machine,
                                    'processes': [], 'applications': []})
        task = api('/api/endpoint/tasks')['task']
        if task:
            def report(result):
                api('/api/endpoint/tasks/result', {'taskId': task['taskId'], 'lease': task['lease'], 'result': result})
            report({'state': 'running', 'summary': 'Linux 执行端已接收安装任务'})
            stop = threading.Event()
            def heartbeat():
                while not stop.wait(20):
                    try: report({'state': 'running', 'summary': '安装任务仍在执行；保持租约'})
                    except Exception: stop.set()
            worker = threading.Thread(target=heartbeat, daemon=True); worker.start()
            try:
                if task['payload'].get('action') != 'install_software': raise ValueError('This endpoint only supports software installation')
                if os.geteuid() != 0: raise PermissionError('Linux apt installation requires a root-managed worker')
                result = install_linux(task['payload']['software'], report)
            except Exception as error:
                result = {'state': 'failed', 'summary': str(error)[:1000]}
            finally: stop.set(); worker.join(timeout=30)
            report(result)
        if once: return
        time.sleep(10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', required=True)
    parser.add_argument('--identity-file', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    path = Path(args.identity_file)
    if path.stat().st_mode & 0o077: raise PermissionError('Identity file must be private (0600)')
    serve(args.server, json.loads(path.read_text()), once=args.once)


if __name__ == '__main__': main()
